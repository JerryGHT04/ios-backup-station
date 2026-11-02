"""Protocol tests with a fake connection. Never connects to or modifies an iPhone."""
import asyncio
import contextlib
import collections
import io
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import restore_worker as worker
import server

UDID = "00008120-001634243E42601E"


class FakeConnection:
    def __init__(self, messages=()):
        self.messages = iter(messages)
        self.frames = []
        self.plists = []

    async def sendall(self, data):
        self.frames.append(data)

    async def send_plist(self, value):
        self.plists.append(value)

    async def recv_plist(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise ConnectionError("device disconnected")


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / UDID
        self.source.mkdir()
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()

    def tearDown(self):
        self.redirect.__exit__(None, None, None)
        self.tmp.cleanup()

    def link(self, messages=()):
        connection = FakeConnection(messages)
        return worker.RestoreLink(connection, self.root, UDID, worker.Reporter()), connection

    async def test_file_frames_and_exact_payload_byte_count(self):
        payload = bytes(range(256)) * 300
        (self.source / "Manifest.db").write_bytes(payload)
        link, connection = self.link()
        await link.download_files(["DLMessageDownloadFiles", [UDID + "/Manifest.db"]])
        frames = connection.frames
        name = (UDID + "/Manifest.db").encode()
        self.assertEqual(frames[0], struct.pack(">I", len(name)) + name)
        chunks = []
        for frame in frames[1:-2]:
            size, code = struct.unpack(">IB", frame[:5])
            self.assertEqual(size, len(frame) - 4)
            self.assertEqual(code, 0x0C)
            self.assertLessEqual(len(frame) - 5, 32768)
            chunks.append(frame[5:])
        self.assertEqual(b"".join(chunks), payload)
        self.assertEqual(frames[-2:], [struct.pack(">IB", 1, 0), bytes(4)])
        self.assertEqual(link.bytes_sent, len(payload))
        self.assertEqual(link.files_sent, 1)
        self.assertEqual((self.source / "Manifest.db").read_bytes(), payload)

    async def test_zero_length_file_has_success_terminator(self):
        (self.source / "empty").touch()
        link, connection = self.link()
        await link.download_files(["DLMessageDownloadFiles", [UDID + "/empty"]])
        self.assertEqual(connection.frames[1:], [struct.pack(">IB", 1, 0), bytes(4)])
        self.assertEqual(link.bytes_sent, 0)

    async def test_file_read_failure_never_reports_success(self):
        link, connection = self.link()
        with self.assertRaises(worker.RestoreFailure):
            await link.download_files(["DLMessageDownloadFiles", [UDID + "/missing"]])
        self.assertEqual(connection.frames[-1][4], 0x06)
        self.assertEqual(connection.plists, [])
        self.assertEqual(link.files_sent, 0)

    async def test_traversal_other_device_and_symlink_are_rejected(self):
        outside = self.root / "private"
        outside.write_bytes(b"secret")
        (self.source / "link").symlink_to(outside)
        for path in (UDID + "/../private", "OTHER/Info.plist", "/etc/passwd", UDID + "/link"):
            link, connection = self.link()
            with self.subTest(path=path), self.assertRaises(worker.RestoreFailure):
                await link.download_files(["DLMessageDownloadFiles", [path]])
            self.assertEqual(connection.frames, [])

    async def test_host_write_request_cannot_modify_source(self):
        (self.source / "keep").write_bytes(b"original")
        link, connection = self.link([["DLMessageRemoveItems", [UDID + "/keep"]]])
        with self.assertRaises(worker.RestoreFailure):
            await link.restore_loop()
        self.assertEqual((self.source / "keep").read_bytes(), b"original")

    async def test_only_explicit_zero_error_code_completes_protocol(self):
        for response in ({}, {"ErrorCode": 1}, {"ErrorCode": False}, {"ErrorCode": 0.0}):
            link, _ = self.link([["DLMessageProcessMessage", response]])
            with self.subTest(response=response), self.assertRaises(worker.RestoreFailure):
                await link.restore_loop()
        self.assertNotIn('"event": "protocol_complete"', self.output.getvalue())
        link, _ = self.link([["DLMessageProcessMessage", {"ErrorCode": 0}]])
        await link.restore_loop()
        self.assertIn('"event": "protocol_complete"', self.output.getvalue())

    async def test_disconnect_after_100_percent_is_failure(self):
        (self.source / "Info.plist").write_bytes(b"data")
        link, _ = self.link([["DLMessageDownloadFiles", [UDID + "/Info.plist"], None, 100]])
        with self.assertRaises(ConnectionError):
            await link.restore_loop()
        events = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual(next(e["pct"] for e in events if e["event"] == "progress"), 99)
        self.assertNotIn("protocol_complete", [e["event"] for e in events])

    async def test_restore_handshake_and_completion_with_fake_device(self):
        password = "test restore secret"
        info = {"Product Version": "27.0", "Applications": {"example.app": {}}}
        (self.source / "Info.plist").write_bytes(plistlib.dumps(info))
        connection = FakeConnection([
            ["DLMessageVersionExchange", 400, 0], ["DLMessageDeviceReady"],
            ["DLMessageProcessMessage", {"ErrorCode": 0, "ProtocolVersion": 2.1}],
            ["DLMessageDownloadFiles", [UDID + "/Info.plist"], None, 50],
            ["DLMessageProcessMessage", {"ErrorCode": 0}],
        ])
        class Context:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        class Lockdown(Context):
            udid, paired, product_version = UDID, True, "27.0"
            async def get_value(self, *args): return False
        real_version_exchange = worker.Mobilebackup2Service.version_exchange
        class Service(Context):
            service = connection
            def __init__(self, *args): pass
            version_exchange = real_version_exchange
        class Afc(Context):
            def __init__(self, *args): pass
            async def fopen(self, *args): return 1
            async def lock(self, *args): pass
            async def fclose(self, *args): pass
            async def makedirs(self, *args): pass
            async def set_file_contents(self, *args): pass
        class Notifications(Context):
            def __init__(self, *args): pass
            async def notify_post(self, *args): pass
        with patch.object(worker, "create_using_usbmux", new_callable=AsyncMock, return_value=Lockdown()) as connect, \
             patch.object(worker, "validate_backup", return_value=(info, True)), \
             patch.object(worker, "Mobilebackup2Service", Service), patch.object(worker, "AfcService", Afc), \
             patch.object(worker, "NotificationProxyService", Notifications), \
             patch.object(worker, "capture_device_log", new_callable=AsyncMock):
            await worker.restore(self.root, UDID, password, worker.Reporter(password))
        self.assertEqual(connect.await_args.kwargs, {"serial": UDID, "connection_type": "USB", "autopair": False})
        request = next(p[1] for p in connection.plists if p[0] == "DLMessageProcessMessage" and p[1].get("MessageName") == "Restore")
        self.assertEqual(request["Options"]["Password"], password)
        self.assertNotIn("RestoreShouldReboot", request["Options"])
        events = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertEqual(events[-1]["status"], "awaiting_device")
        self.assertNotIn(password, self.output.getvalue())


class RestoreEtaTests(unittest.TestCase):
    def test_restore_eta_uses_overall_progress_and_elapsed_time(self):
        job = {"started": 100, "pct": 1.0, "eta": None, "totalEta": None,
               "lastRestoreSample": 0, "restoreSamples": collections.deque(maxlen=1000)}
        server.update_restore_eta(job, now=100)
        self.assertIsNone(job["eta"], "one progress sample is not enough to predict a duration")
        job["pct"] = 11.0
        server.update_restore_eta(job, now=700)
        self.assertEqual(job["eta"], 5340)
        self.assertEqual(job["totalEta"], 5940)

    def test_restore_eta_ignores_stalled_percentage(self):
        job = {"started": 100, "pct": 1.0, "eta": None, "totalEta": None,
               "lastRestoreSample": 0, "restoreSamples": collections.deque(maxlen=1000)}
        server.update_restore_eta(job, now=100)
        server.update_restore_eta(job, now=1000)
        self.assertIsNone(job["eta"], "a long wait without progress must not create an estimate")


class RestoreConfirmationTests(unittest.TestCase):
    def test_user_confirmation_marks_matching_history_and_result_successful(self):
        entry = {"kind": "restore", "udid": UDID, "status": "awaiting_device", "ts": 100}
        history = [entry]
        result = {"kind": "restore", "udid": UDID, "status": "awaiting_device", "ts": 100}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(server, "STATE_DIR", tmp), \
             patch.object(server, "HIST_FILE", str(Path(tmp) / "history.json")), \
             patch.object(server, "HISTORY", history), patch.object(server, "RESULT", result), \
             patch.object(server, "log"):
            code, payload = server.confirm_restore_success(UDID, 100)
            self.assertEqual((code, payload), (200, {"ok": True}))
            self.assertEqual(entry["status"], "ok")
            self.assertTrue(entry["confirmed"])
            self.assertEqual(result["status"], "ok")
            with open(Path(tmp) / "history.json") as f:
                self.assertEqual(json.load(f)[0]["status"], "ok")

    def test_confirmation_without_pending_restore_is_rejected(self):
        with patch.object(server, "HISTORY", []):
            code, payload = server.confirm_restore_success(UDID, 100)
        self.assertEqual(code, 404)
        self.assertIn("待确认", payload["error"])


class UsbMuxModeTests(unittest.TestCase):
    def test_switch_to_stock_waits_for_requested_usb_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            mode_file = Path(tmp) / "mode"
            running_file = Path(tmp) / "running"

            def fake_run(argv, **kwargs):
                if argv[:2] == ["pkill", "-x"]:
                    running_file.write_text("stock\n")
                    return 0, b""
                if argv[:2] == ["pgrep", "-x"]:
                    return 0, b"123\n"
                return 1, b""

            with patch.object(server, "USBMUXD_MODE_FILE", str(mode_file)), \
                 patch.object(server, "USBMUXD_RUNNING_FILE", str(running_file)), \
                 patch.object(server, "run", side_effect=fake_run), \
                 patch.object(server, "list_ids", return_value=[UDID]), \
                 patch.object(server.time, "sleep"):
                self.assertTrue(server.switch_usbmuxd_mode("stock", udid=UDID))
            self.assertEqual(mode_file.read_text().strip(), "stock")

    def test_restore_finalizer_returns_transport_to_wifi_daemon(self):
        job = {"started": 0, "cancel": True, "status": "cancelled", "protocolComplete": False,
               "restoreOutcome": None, "encrypted": True, "msg": "", "bytes": None}
        program = "import sys; sys.stdin.readline(); sys.exit(1)"
        with patch.object(server, "JOB", job), patch.object(server, "HISTORY", []), \
             patch.object(server, "save_history"), patch.object(server, "to_host", return_value="/backup"), \
             patch.object(server, "switch_usbmuxd_mode", return_value=True) as switch, \
             contextlib.redirect_stdout(io.StringIO()):
            server.run_restore([sys.executable, "-c", program], UDID, "/backup", "secret")
        switch.assert_called_once_with("usbmuxd2")


class ValidationTests(unittest.TestCase):
    def test_real_encrypted_manifest_accepts_correct_password_rejects_wrong_one(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.keywrap import aes_key_wrap
        password = "测试 backup password"
        salt, dpsl, wrapping, database_key = bytes(range(20)), bytes(range(16)), bytes(range(32)), bytes(range(32, 64))
        derived = hashlib.pbkdf2_hmac("sha256", password.encode(), dpsl, 100, 32)
        derived = hashlib.pbkdf2_hmac("sha1", derived, salt, 100, 32)
        def tlv(tag, value):
            if isinstance(value, int):
                value = struct.pack(">I", value)
            return tag + struct.pack(">I", len(value)) + value
        keybag = b"".join(tlv(tag, value) for tag, value in (
            (b"DPSL", dpsl), (b"DPIC", 100), (b"SALT", salt), (b"ITER", 100),
            (b"UUID", bytes(16)), (b"CLAS", 4), (b"WRAP", 2), (b"KTYP", 0),
            (b"WPKY", aes_key_wrap(derived, wrapping))))
        manifest = {"IsEncrypted": True, "BackupKeyBag": keybag,
                    "ManifestKey": struct.pack("<I", 4) + aes_key_wrap(wrapping, database_key),
                    "Lockdown": {"ProductVersion": "27.0"}}
        plaintext = b"SQLite format 3\0" + bytes(16)
        encryptor = Cipher(algorithms.AES(database_key), modes.CBC(bytes(16))).encryptor()
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / UDID
            folder.mkdir()
            for name, value in {"Info.plist": {"Unique Identifier": UDID},
                                "Status.plist": {"SnapshotState": "finished"},
                                "Manifest.plist": manifest}.items():
                (folder / name).write_bytes(plistlib.dumps(value))
            (folder / "Manifest.db").write_bytes(ciphertext)
            self.assertTrue(worker.validate_backup(tmp, UDID, password)[1])
            with self.assertRaisesRegex(worker.RestoreFailure, "密码校验失败"):
                worker.validate_backup(tmp, UDID, "wrong password")
            with patch.object(worker, "create_using_usbmux", new_callable=AsyncMock) as connect, \
                 contextlib.redirect_stdout(io.StringIO()), self.assertRaises(worker.RestoreFailure):
                asyncio.run(worker.restore(tmp, UDID, "wrong password", worker.Reporter()))
            connect.assert_not_awaited()
            (folder / "Manifest.db").write_bytes(bytes(32))
            with self.assertRaisesRegex(worker.RestoreFailure, "密码校验失败"):
                worker.validate_backup(tmp, UDID, password)
            self.assertEqual((folder / "Manifest.db").read_bytes(), bytes(32))

    def test_password_redacted_in_stdout_and_diagnostic(self):
        diagnostic, output = io.StringIO(), io.StringIO()
        password = '密"码\\123'
        reporter = worker.Reporter(password, diagnostic)
        with contextlib.redirect_stdout(output):
            reporter.emit("error", message="bad " + password, options={"Password": password})
        for value in (diagnostic.getvalue(), output.getvalue()):
            parsed = json.loads(value)
            self.assertEqual(parsed["message"], "bad [redacted]")
            self.assertNotIn("Password", parsed["options"])

    def test_incomplete_backup_rejected_before_any_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / UDID
            folder.mkdir()
            for name, value in {"Info.plist": {"Unique Identifier": UDID},
                                "Status.plist": {"SnapshotState": "unfinished"},
                                "Manifest.plist": {"IsEncrypted": False}}.items():
                (folder / name).write_bytes(plistlib.dumps(value))
            with self.assertRaises(worker.RestoreFailure):
                worker.validate_backup(tmp, UDID, "")

    def test_completed_event_without_protocol_ack_is_not_accepted(self):
        job = {"protocolComplete": False, "restoreOutcome": None}
        server.handle_restore_event(job, json.dumps({"event": "completed", "status": "awaiting_device"}))
        self.assertIsNone(job["restoreOutcome"])
        server.handle_restore_event(job, json.dumps({"event": "protocol_complete"}))
        server.handle_restore_event(job, json.dumps({"event": "completed", "status": "awaiting_device"}))
        self.assertEqual(job["restoreOutcome"], "awaiting_device")
        self.assertEqual(job["pct"], 99)

    def test_server_requires_worker_exit_zero_and_explicit_completion(self):
        # Exercise the actual pipe parser; no phone tools or network are used.
        cases = [(0, [], "failed"),
                 (0, [{"event": "protocol_complete"}, {"event": "completed", "status": "awaiting_device"}], "awaiting_device"),
                 (1, [{"event": "protocol_complete"}, {"event": "completed", "status": "awaiting_device"}], "failed"),
                 (0, [{"event": "protocol_complete"}, {"event": "completed", "status": "awaiting_device"},
                      {"event": "error", "message": "device rejected"}], "failed")]
        for code, events, expected in cases:
            job = {"started": 0, "cancel": False, "status": "", "protocolComplete": False,
                   "restoreOutcome": None, "encrypted": True, "msg": "", "bytes": None}
            program = "; ".join(["import sys", "sys.stdin.readline()"] + [
                "print(%r, flush=True)" % json.dumps(e) for e in events] + ["sys.exit(%d)" % code])
            with self.subTest(expected=expected, code=code), patch.object(server, "JOB", job), \
                 patch.object(server, "HISTORY", []), patch.object(server, "save_history"), \
                 patch.object(server, "to_host", return_value="/backup"), \
                 patch.object(server, "switch_usbmuxd_mode", return_value=True), \
                 contextlib.redirect_stdout(io.StringIO()):
                server.run_restore([sys.executable, "-c", program], UDID, "/backup", "secret")
                self.assertEqual(server.RESULT["status"], expected)

    def test_sync_cleanup_preserves_primary_error_after_disconnect(self):
        class Afc:
            async def fopen(self, *args): return 1
            async def lock(self, handle, operation):
                if operation == worker.AFC_LOCK_UN:
                    raise ConnectionError("device reboot")
            async def fclose(self, *args): raise ConnectionError("device reboot")
        class Notifications:
            async def notify_post(self, value):
                if value.endswith("syncDidFinish"):
                    raise ConnectionError("device reboot")
        async def attempt():
            async with worker.sync_lock(Afc(), Notifications(), worker.Reporter()):
                raise worker.RestoreFailure("original device error")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(worker.RestoreFailure, "original device error"):
            asyncio.run(attempt())


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_heartbeat_preserves_partial_receive(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            link = worker.RestoreLink(FakeConnection(), Path(tmp), UDID, worker.Reporter())
            ready = asyncio.Event()
            calls = 0
            async def receive():
                nonlocal calls
                calls += 1
                await ready.wait()
                return ["DLMessageProcessMessage", {"ErrorCode": 0}]
            original_wait = asyncio.wait
            waits = 0
            async def short_wait(tasks, timeout):
                nonlocal waits
                waits += 1
                if waits == 2:
                    ready.set()
                return await original_wait(tasks, timeout=0 if waits == 1 else 1)
            with patch.object(link, "receive_message", receive), patch.object(worker.asyncio, "wait", short_wait), contextlib.redirect_stdout(output):
                result = await link.receive_restore_message()
            self.assertEqual(calls, 1)
            self.assertEqual(result[1]["ErrorCode"], 0)
            self.assertIn('"waiting"', output.getvalue())

    async def test_cancel_closes_pending_receive(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = worker.RestoreLink(FakeConnection(), Path(tmp), UDID, worker.Reporter())
            entered, closed = asyncio.Event(), asyncio.Event()
            async def receive():
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            with patch.object(link, "receive_message", receive):
                task = asyncio.create_task(link.receive_restore_message())
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(closed.is_set())

    async def test_debug_flood_does_not_hide_terminal_or_device_fault(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            path = Path(tmp) / "protocol"
            log = worker.RotatingLog(path, limit=512)
            device = io.StringIO()
            reporter = worker.Reporter("secret", log, device)
            for _ in range(10000):
                reporter.record_device("BackupAgent2 <Debug>: MKBFileWrite secret")
            reporter.record_device("BackupAgent2 <Error>: restore failed secret")
            for _ in range(50):
                reporter.emit("protocol", command="a" * 80)
            reporter.emit("cancelled", message="secret")
            log.close()
            self.assertIn('"cancelled"', path.read_text())
            self.assertNotIn("secret", path.read_text() + device.getvalue())
            self.assertNotIn("MKBFileWrite", device.getvalue())
            self.assertIn("restore failed", device.getvalue())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(Path(tmp).iterdir())), 3)

    async def test_completed_batch_keeps_exact_final_byte_count(self):
        job = {"bytes": None, "transferSamples": collections.deque()}
        server.handle_restore_event(job, json.dumps({"event": "transfer", "bytes": 100, "file": "a"}))
        server.handle_restore_event(job, json.dumps({"event": "batch_sent", "bytes": 150, "file": "b"}))
        self.assertEqual(job["bytes"], 150)
        self.assertEqual(job["lastTransferBytes"], 150)
        with self.assertRaises(ValueError):
            server.handle_restore_event(job, json.dumps({"event": "batch_sent", "bytes": 149}))

    async def test_stale_rate_disappears_without_mutating_live_job(self):
        job = {"started": 100, "lastTransfer": 110, "speed": 30000000,
               "status": "正在发送恢复数据", "lastDeviceActivity": 125}
        self.assertEqual(server.restore_live_fields(job, now=115)["speed"], 30000000)
        fields = server.restore_live_fields(job, now=130)
        self.assertIsNone(fields["speed"])
        self.assertEqual(fields["transferQuietFor"], 20)
        self.assertEqual(fields["deviceQuietFor"], 5)
        self.assertIn("等待手机", fields["status"])
        self.assertEqual(job["speed"], 30000000)

    async def test_transport_restart_does_not_interrupt_job(self):
        with patch.object(server, "run") as run:
            server.busy.acquire()
            try:
                self.assertFalse(server.restart_usbmuxd("test"))
            finally:
                server.busy.release()
            run.assert_not_called()

    async def test_transport_restart_escalates_only_original_pidfd(self):
        with patch.object(server, "JOB", None), patch.object(server, "_last_mux_restart", 0), \
             patch.object(server, "run", side_effect=[(0, b"123\n"), (0, b"")]) as run, \
             patch.object(server.os, "pidfd_open", return_value=99), \
             patch.object(server.os, "close") as close, \
             patch.object(server.select, "select", return_value=([], [], [])), \
             patch.object(server.signal, "pidfd_send_signal") as kill, \
             patch.object(server, "log"):
            self.assertTrue(server.restart_usbmuxd("test"))
            kill.assert_called_once_with(99, server.signal.SIGKILL)
            close.assert_called_once_with(99)
            self.assertFalse(server.restart_usbmuxd("immediate duplicate"))
            self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
