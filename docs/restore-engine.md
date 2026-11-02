# USB restore engine

The server launches `app/restore_worker.py` in the pinned `/opt/restore` Python environment.
The worker uses pymobiledevice3's lockdown, AFC, notification and DeviceLink primitives; it does not
run idevicebackup2. It selects the exact device over USB and never automatically retries a restore.

The password travels once through stdin, not command arguments or environment variables. Before
starting any device service, the worker unlocks the encrypted backup keybag and verifies the first
SQLite block of Manifest.db. The original backup is read-only to the protocol host: unexpected
commands asking to upload, move, remove or copy source files fail explicitly. Ordinary restore
download, directory listing, free-space and purge negotiation requests are supported.

RestoreSystemFiles and RemoveItemsNotRestored are true; RestorePreserveSettings is false (restore the
backup's settings). RestoreDontCopyBackup is true. Below iOS 27, RestoreShouldReboot is true. On iOS 27
that optional key is omitted to use the device default. This version profile is experimental: reports
about tiny supervision seed restores do not prove complete encrypted device restores work.

File data is sent in 32 KiB protocol frames. Events report actual payload bytes successfully sent over
the connection, not backup folder size. Sending bytes does not mean the device has applied them.
Only an explicit DLMessageProcessMessage with ErrorCode 0 completes the host protocol. Even then the
UI reports `awaiting_device`, asking the user to wait for restart and check their data; neither a
disconnect nor a 100% progress event is treated as successful device recovery.

Each attempt writes a rotating, password-redacted JSONL diagnostic in `/config/restore-logs/`, mode 0600.
Protocol events retain three 2 MiB files; device notices/faults use three separate 1 MiB files.
Per-block Debug lines are counted as device activity but never fill the protocol log. Thirty-second
heartbeats record whether the host is sending data or waiting for a device message. Waiting does not
cancel a partially received frame. A stale transfer rate disappears after ten seconds without bytes.
Device log capture starts before the Restore request. Passwords and plist payloads are never logged. Logs can include device
paths and should be kept private. SIGINT cancels the worker and unwinds its service/sync-lock contexts;
it does not issue EraseDevice, a reboot or another restore.

Local checks use a fake transport: framing, exact byte counts, file errors, source boundary enforcement,
terminal replies, password verification and redaction. No unit test communicates with a phone.
An import check and a read-only USB connection check are deployment smoke checks, not restoration tests.
## Real-device validation

On 2026-11-02, this engine completed a restore of a 123.6 GB encrypted NAS backup to the same iPhone 15 running iOS 27.0. The worker received the terminal `DLMessageProcessMessage`, recorded `protocol_complete` and exited successfully; after the phone rebooted, the user confirmed it reached the home screen. The run used upstream stock usbmuxd over USB.

This validates that specific device, OS version, backup and transport combination. It does not establish support for every iPhone, iPad, iOS release or backup. The earlier investigations below remain historical records; their uncertainty about the earlier stalled attempts is unchanged.

## Investigation: 2026-11-01 cancelled attempt

The host sent about 1.9 GB, then reported no further transfer events; it never received explicit
protocol completion. Cancellation exited with code 130. The phone returned to its home screen.
The old combined log hit its 8 MiB cap early, so it cannot establish the eventual stall location.
A BackupAgent2 CPU-resource report covers the initial 139 seconds and explicitly says "Action taken:
none"; it is not proof of a crash or memory termination. Its free-space measurement was about 242 GB.
No matching Jetsam report was collected for this attempt.

After cancellation, usbmuxd2 remained alive but unresponsive to normal termination. Restarting that
daemon restored USB enumeration. This is an observed transport defect, but the available records do
not show whether it caused the earlier restore stall. Idle recovery now holds the job exclusion lock,
tries normal termination, then signals only the original daemon via pidfd if it remains stuck. A shared
cooldown prevents competing discovery watchdogs from immediately restarting its replacement.

These diagnostic and recovery fixes do not establish that complete encrypted iOS 27 restoration works.

## Investigation: second attempt, started 00:49 on 2026-11-01

After the first ~1.17 GB of metadata, BackupAgent2 reported `Calculating expected duration`.
This preparation took several minutes; it was not by itself evidence of failure. The phone then
requested real backup files and reached 1.6664%, 24,114 files and 2,421,644,029 host payload bytes.
The later stall was different: the worker heartbeat remained in `发送文件数据`, on a 3,760,048-byte
source file, rather than waiting for another protocol request.

A read-only debugger snapshot of usbmuxd2 showed its sending thread in `TCP::send_data`, waiting
at TCP.cpp:214 for `_canSendEvent`. All three USB receiver threads were waiting for transfers,
not blocked in their input handlers. Numerical state of the affected connection was:

```
seq=4158333184 seqAcked=4158320638 inWin=12544
ack=1969042 acked=1969042 pktForwarded=1969042
sport=24 dport=49294 state=CONN_CONNECTED
```

Outstanding bytes (12,546) exceeded the last advertised window (12,544), so the host sender waited
for another device ACK/window update. This locates the flow-control wait; it does not by itself
prove which endpoint caused it. Device enumeration remained responsive. No data buffers, password
memory, or process core dumps were read. The debugger detached without modifying the process.

For a controlled comparison, the image now includes standard upstream usbmuxd at revision
`3ded00c9985a5108cfc7591a309f9a23d57a8cba` in `/opt/usbmuxd-stock`. Selecting
`USBMUXD_IMPLEMENTATION=stock` uses that daemon; the default remains usbmuxd2 with Wi-Fi discovery.
The standard daemon is USB-only here. This is a diagnostic comparison, not yet a validated fix.
Restore protocol options and the encrypted source backup remain the same between transports.

Relevant primary sources:
- https://github.com/tihmstar/usbmuxd2/blob/744c46f/usbmuxd2/TCP.cpp
- https://github.com/libimobiledevice/usbmuxd/blob/3ded00c9985a5108cfc7591a309f9a23d57a8cba/src/device.c
- https://github.com/libimobiledevice/libimobiledevice/issues/1664

A separate report about CDTunnel TLS-record thresholds (frida-core issue 1223) concerns
`remotepairingd`, not this direct MobileBackup2 connection; it is not proof of the same defect.
