#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Isolated MobileBackup2 restore host. stdin: one request; stdout: JSON events.

Uses pymobiledevice3 (GPL-3.0). The web server talks to this worker over pipes.
Never logs the request, passwords, plist payloads or backup contents.
"""
import argparse
import asyncio
import contextlib
import json
from importlib.metadata import version as package_version
import logging
import math
import os
from pathlib import Path, PurePosixPath
import plistlib
import signal
import struct
import sys
import time

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.exceptions import AfcException
from pymobiledevice3.services.afc import AFC_LOCK_EX, AFC_LOCK_UN, AfcError, AfcService
from pymobiledevice3.services.device_link import DeviceLink
from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
from pymobiledevice3.services.notification_proxy import NotificationProxyService

CHUNK_SIZE = 32 * 1024


class RestoreFailure(Exception):
    pass


class RotatingLog:
    """Private bounded logs that retain recent events, including the final outcome."""
    def __init__(self, path, limit=2 * 1024 * 1024, backups=2):
        self.path, self.limit, self.backups = Path(path), limit, backups
        self.stream = self.open()

    def open(self):
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        return os.fdopen(fd, "w", encoding="utf-8")

    def write(self, text):
        if self.stream.tell() + len(text.encode("utf-8")) > self.limit:
            self.stream.close()
            for index in range(self.backups, 0, -1):
                source = self.path if index == 1 else Path(str(self.path) + ".%d" % (index - 1))
                if source.exists():
                    source.replace(str(self.path) + ".%d" % index)
            self.stream = self.open()
        self.stream.write(text)

    def flush(self):
        self.stream.flush()

    def close(self):
        self.stream.close()


class Reporter:
    def __init__(self, password="", diagnostic=None, device_log=None):
        self.password = password
        self.diagnostic = diagnostic
        self.device_log = device_log
        self.device_lines = 0
        self.last_device_report = 0.0

    def clean(self, value):
        if isinstance(value, str):
            return value.replace(self.password, "[redacted]") if self.password else value
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items() if "password" not in k.lower()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        return value

    def emit(self, event, **fields):
        record = self.clean(dict(event=event, ts=time.time(), **fields))
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        self.record(line)

    def record(self, line):
        if self.diagnostic:
            self.diagnostic.write(self.clean(line) + "\n")
            self.diagnostic.flush()

    def record_device(self, text):
        if not any(word in text.lower() for word in ("mobilebackup", "backupagent", "csresetrestore")):
            return
        self.device_lines += 1
        now = time.monotonic()
        if now - self.last_device_report >= 10:
            self.last_device_report = now
            self.emit("device_activity", lines=self.device_lines)
        # Debug output includes one MKBFileWrite record per block; it is activity,
        # not a failure. Keep notices and faults in a separate rotating file.
        if "<debug>" in text.lower() or not self.device_log:
            return
        self.device_log.write(json.dumps(self.clean({"ts": time.time(), "line": text[:4000]}),
                                         ensure_ascii=False) + "\n")
        self.device_log.flush()


class RestoreLink(DeviceLink):
    """Read-only backup source with measured transfers and explicit terminal replies.

    A restore must not mutate the only source backup. Unexpected host-write commands
    fail visibly instead of allowing the generic backup handlers to overwrite it.
    """
    def __init__(self, service, root, udid, reporter):
        super().__init__(service, root)
        self.source = (Path(root) / udid).resolve()
        self.udid = udid
        self.reporter = reporter
        self.bytes_sent = 0
        self.files_sent = 0
        self.last_report = 0.0
        self.current_file = ""
        self.stage = "等待手机请求文件"

    def source_path(self, name):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != self.udid:
            raise RestoreFailure("手机请求了备份目录之外的文件")
        resolved = (self.root_path / str(path)).resolve()
        if not resolved.is_relative_to(self.source):
            raise RestoreFailure("备份文件链接指向了所选备份之外")
        return resolved

    def report_transfer(self, force=False):
        now = time.monotonic()
        if force or now - self.last_report >= 1:
            self.last_report = now
            self.reporter.emit("transfer", bytes=self.bytes_sent, files=self.files_sent,
                               file=self.current_file, status="正在发送恢复数据")

    async def monitor_transfer(self):
        while True:
            await asyncio.sleep(30)
            self.reporter.emit("heartbeat", stage=self.stage, bytes=self.bytes_sent,
                               files=self.files_sent, file=self.current_file)

    async def download_files(self, message):
        self.stage = "发送文件数据"
        if len(message) < 2 or not isinstance(message[1], list):
            raise RestoreFailure("手机发送了无效的文件请求")
        for name in message[1]:
            if not isinstance(name, str):
                raise RestoreFailure("手机请求的文件名格式无效")
            path = self.source_path(name)
            self.current_file = str(PurePosixPath(name).relative_to(self.udid))
            self.report_transfer(force=self.files_sent < 4)
            encoded = name.encode("utf-8")
            await self._sendall(struct.pack(">I", len(encoded)) + encoded)
            try:
                with path.open("rb") as source:
                    while True:
                        chunk = source.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        await self._sendall(struct.pack(">IB", len(chunk) + 1, 0x0C) + chunk)
                        self.bytes_sent += len(chunk)
                        self.report_transfer()
            except OSError as exc:
                # Tell the device the file failed; never follow a partial file with SUCCESS.
                detail = b"Host cannot read the requested backup file"
                await self._sendall(struct.pack(">IB", len(detail) + 1, 0x06) + detail)
                raise RestoreFailure("无法读取备份文件：%s" % self.current_file) from exc
            await self._sendall(struct.pack(">IB", 1, 0))
            self.files_sent += 1
            self.report_transfer(force=self.files_sent < 4)
        await self._sendall(b"\0\0\0\0")
        self.stage = "发送文件批次确认"
        await self.status_response(0)
        self.reporter.emit("batch_sent", bytes=self.bytes_sent, files=self.files_sent,
                           file=self.current_file)

    async def contents_of_directory(self, message):
        path = self.source_path(message[1])
        # The inherited handler only reads. Validate the source boundary first.
        if not path.is_dir():
            raise RestoreFailure("手机请求的备份目录不存在")
        await super().contents_of_directory(message)

    async def receive_restore_message(self):
        # Never cancel a partially received frame merely to emit a heartbeat.
        self.stage = "等待手机协议消息"
        pending = asyncio.create_task(self.receive_message())
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=30)
                if done:
                    return pending.result()
                self.reporter.emit("waiting", bytes=self.bytes_sent, files=self.files_sent,
                                   file=self.current_file, status="等待手机处理或请求下一批文件")
        finally:
            if not pending.done():
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending

    async def restore_loop(self):
        while True:
            message = await self.receive_restore_message()
            if not isinstance(message, (list, tuple)) or not message or not isinstance(message[0], str):
                raise RestoreFailure("手机返回了无效的恢复协议消息")
            command = message[0]
            self.reporter.emit("protocol", command=command)
            if command == "DLMessageProcessMessage":
                if len(message) < 2 or not isinstance(message[1], dict):
                    raise RestoreFailure("手机缺少恢复结果")
                result = message[1]
                # Missing ErrorCode is never equivalent to success.
                if type(result.get("ErrorCode")) is not int or result["ErrorCode"] != 0:
                    description = str(result.get("ErrorDescription") or "手机未返回成功结果")[:1000]
                    raise RestoreFailure("手机拒绝恢复（%s）：%s" % (result.get("ErrorCode"), description))
                self.reporter.emit("protocol_complete", bytes=self.bytes_sent, files=self.files_sent)
                return
            if command == "DLMessageDownloadFiles":
                if len(message) > 3 and isinstance(message[3], (float, int)):
                    pct = float(message[3])
                    if math.isfinite(pct):
                        self.reporter.emit("progress", pct=max(0, min(99, pct)))
                await self.download_files(message)
            elif command == "DLContentsOfDirectory":
                await self.contents_of_directory(message)
            elif command == "DLMessageGetFreeDiskSpace":
                await self.get_free_disk_space(message)
            elif command == "DLMessagePurgeDiskSpace":
                await self.purge_disk_space(message)
            else:
                raise RestoreFailure("恢复要求了未支持的操作 %s；已停止以保护源备份" % command)


def validate_backup(root, udid, password):
    folder = Path(root) / udid
    info = plistlib.loads((folder / "Info.plist").read_bytes())
    status = plistlib.loads((folder / "Status.plist").read_bytes())
    manifest = plistlib.loads((folder / "Manifest.plist").read_bytes())
    if info.get("Unique Identifier") != udid or status.get("SnapshotState") != "finished":
        raise RestoreFailure("备份未完成或设备标识不匹配")
    if manifest.get("IsEncrypted"):
        if not password:
            raise RestoreFailure("这是加密备份，请输入备份密码")
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.keywrap import aes_key_unwrap
        from pyiosbackup.keybag import Keybag
        from pyiosbackup.manifest_plist import ManifestPlist
        try:
            parsed = ManifestPlist.from_path(folder / "Manifest.plist")
            keybag = Keybag.from_manifest(parsed, password)
            wrapped = parsed.manifest_key
            protection_class = struct.unpack("<I", wrapped[:4])[0]
            key = aes_key_unwrap(keybag.get_key(protection_class), wrapped[4:])
            with (folder / "Manifest.db").open("rb") as db:
                header = db.read(32)
            decryptor = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor()
            header = decryptor.update(header) + decryptor.finalize()
            if not header.startswith(b"SQLite format 3\0"):
                raise ValueError("invalid manifest header")
        except Exception as exc:
            raise RestoreFailure("备份密码校验失败，或加密清单损坏；恢复尚未开始") from exc
    else:
        with (folder / "Manifest.db").open("rb") as db:
            if db.read(16) != b"SQLite format 3\0":
                raise RestoreFailure("备份清单损坏，恢复尚未开始")
    return info, bool(manifest.get("IsEncrypted"))


async def capture_device_log(udid, reporter):
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "idevicesyslog", "-u", udid, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=256 * 1024)
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").strip()
            reporter.record_device(text)
    except (OSError, ValueError):
        reporter.emit("diagnostic", status="手机端日志暂不可读取，恢复协议日志仍会保存")
    finally:
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), 3)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()


def restore_options(version):
    options = {"RestoreSystemFiles": True, "RestorePreserveSettings": False,
               "RestoreDontCopyBackup": True, "RemoveItemsNotRestored": True}
    # Let iOS 27 select its reboot default; explicit reboot options changed there.
    # Full encrypted restores on iOS 27 still require real-device validation.
    if int(version.split(".")[0]) < 27:
        options["RestoreShouldReboot"] = True
    return options


@contextlib.asynccontextmanager
async def sync_lock(afc, notifications, reporter):
    """Release the host's sync resources even when the device reboots/disconnects."""
    await notifications.notify_post("com.apple.itunes-mobdev.syncWillStart")
    handle = await afc.fopen("/com.apple.itunes.lock_sync", "r+")
    if not handle:
        raise RestoreFailure("无法打开手机同步锁")
    acquired = False
    try:
        await notifications.notify_post("com.apple.itunes-mobdev.syncLockRequest")
        for _ in range(50):
            try:
                await afc.lock(handle, AFC_LOCK_EX)
                acquired = True
                break
            except AfcException as exc:
                if exc.status != AfcError.OP_WOULD_BLOCK:
                    raise
                await asyncio.sleep(0.2)
        if not acquired:
            raise RestoreFailure("手机正被其他同步或备份任务占用")
        await notifications.notify_post("com.apple.itunes-mobdev.syncDidStart")
        yield
    finally:
        # A reboot after a successful protocol reply can close AFC before cleanup.
        # Cleanup must not overwrite the original protocol outcome/error.
        for cleanup in ((lambda: afc.lock(handle, AFC_LOCK_UN)) if acquired else None,
                        lambda: afc.fclose(handle),
                        lambda: notifications.notify_post("com.apple.itunes-mobdev.syncDidFinish")):
            if cleanup:
                try:
                    await asyncio.wait_for(cleanup(), 3)
                except Exception:
                    reporter.emit("diagnostic", status="手机已关闭同步服务；同步锁清理连接不可用")


async def restore(root, udid, password, reporter, probe=False):
    reporter.emit("phase", status="正在校验备份密码与清单")
    info, encrypted = await asyncio.to_thread(validate_backup, root, udid, password)
    reporter.emit("phase", status="正在验证 USB 连接")
    lockdown = await asyncio.wait_for(create_using_usbmux(
        serial=udid, connection_type="USB", autopair=False), 30)
    async with lockdown:
        if lockdown.udid != udid or not lockdown.paired:
            raise RestoreFailure("USB 目标手机不匹配或尚未信任此 NAS")
        version = str(lockdown.product_version)
        def version_key(value):
            return tuple((list(map(int, str(value).split("."))) + [0, 0, 0])[:3])
        if version_key(version) < version_key(info.get("Product Version", "0")):
            raise RestoreFailure("手机 iOS 版本低于备份版本")
        try:
            find_my = await lockdown.get_value("com.apple.fmip", "IsAssociated")
        except Exception:
            find_my = None
        if find_my:
            raise RestoreFailure("请先关闭手机上的“查找我的 iPhone”")
        if probe:
            reporter.emit("probe_complete", version=version, paired=True)
            return
        log_task = asyncio.create_task(capture_device_log(udid, reporter))
        try:
            async with (Mobilebackup2Service(lockdown) as service,
                        NotificationProxyService(lockdown) as notifications,
                        AfcService(lockdown) as afc,
                        sync_lock(afc, notifications, reporter)):
                link = RestoreLink(service.service, Path(root), udid, reporter)
                await link.version_exchange()
                await service.version_exchange(link)
                options = restore_options(version)
                if encrypted:
                    options["Password"] = password
                reporter.emit("phase", status="正在请求手机恢复", version=version)
                await link.send_process_message({"MessageName": "Restore", "TargetIdentifier": udid,
                                                 "SourceIdentifier": udid, "Options": options})
                applications = info.get("Applications")
                if applications is not None:
                    await afc.makedirs("/iTunesRestore")
                    await afc.set_file_contents("/iTunesRestore/RestoreApplications.plist",
                                                plistlib.dumps(applications))
                reporter.emit("phase", status="等待手机请求恢复文件")
                monitor = asyncio.create_task(link.monitor_transfer())
                try:
                    await link.restore_loop()
                finally:
                    monitor.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await monitor
                # Zero protocol status confirms host transfer, not the on-device migration.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(link.disconnect(), 3)
            reporter.emit("completed", status="awaiting_device", bytes=link.bytes_sent,
                          files=link.files_sent,
                          message="恢复数据传输完成，请保持连接，等待手机完成重启并检查数据。")
        finally:
            log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await log_task


async def run(args, request, reporter):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        reporter.emit("engine", name="MobileBackup2 Python worker", version=package_version("pymobiledevice3"))
        await restore(args.backup_root, args.udid, request.get("password", ""), reporter, args.probe)
        return 0
    except asyncio.CancelledError:
        reporter.emit("cancelled", message="恢复连接已关闭，请留意手机是否需要重启。")
        return 130
    except Exception as exc:
        text = str(exc)
        if "password protected" in text.lower():
            text = "手机处于锁定状态，请输入手机锁屏密码后重试。"
        elif not isinstance(exc, RestoreFailure):
            text = "恢复通信异常（%s）；请检查手机屏幕和本次设备日志。" % type(exc).__name__
        reporter.emit("error", message=text[:1200], code=type(exc).__name__)
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--udid", required=True)
    parser.add_argument("--diagnostic")
    parser.add_argument("--probe", action="store_true", help="Validate only; never sends Restore")
    args = parser.parse_args()
    request = json.loads(sys.stdin.buffer.readline(4096))
    logging.disable(logging.CRITICAL)  # third-party debug output can contain request options
    diagnostic = device_log = None
    if args.diagnostic:
        diagnostic = RotatingLog(args.diagnostic)
        device_log = RotatingLog(args.diagnostic + ".device", limit=1024 * 1024)
    reporter = Reporter(request.get("password", ""), diagnostic, device_log)
    try:
        return asyncio.run(run(args, request, reporter))
    finally:
        if diagnostic:
            diagnostic.close()
        if device_log:
            device_log.close()


if __name__ == "__main__":
    sys.exit(main())
