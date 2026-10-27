#!/usr/bin/env python3
"""Run the real ios-backup-station server code against the fake phone. Listens on 127.0.0.1 only.

    python3 tests/run_fake_server.py PORT WORKDIR

WORKDIR gets: fake/phone.json (state of the fake phone, edit it while the server runs), vol1/ (a fake NAS volume),
config/ (settings, web password, history). Nothing outside WORKDIR is touched and no real device is contacted.
"""
import json, os, sys, threading
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
port, work = int(sys.argv[1]), os.path.abspath(sys.argv[2])
fake = os.path.join(work, "fake")
os.makedirs(fake, exist_ok=True)
os.environ["IBS_FAKE_DIR"] = fake
os.environ["PATH"] = os.path.join(HERE, "fakephone", "bin") + os.pathsep + os.environ["PATH"]
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import server  # noqa: E402

def phone():
    try:
        with open(os.path.join(fake, "phone.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

vol = os.path.join(work, "vol1")
for d in ("Drive", "photos", "Scores"):
    os.makedirs(os.path.join(vol, d), exist_ok=True)
server.ROOTS = {"vol1": {"name": "vol1", "cpath": vol, "host": "/vol1/1000", "label": "存储空间 1"}}
server.STATE_DIR = os.path.join(work, "config")
server.HIST_FILE = os.path.join(server.STATE_DIR, "history.json")
server.CONF_FILE = os.path.join(server.STATE_DIR, "settings.json")
server.AUTH_FILE = os.path.join(server.STATE_DIR, "auth.json")
server.apple_usb_present = lambda: bool(phone().get("usb"))                      # stands in for /sys/bus/usb
server.tcp_open = lambda ip, port=62078, timeout=2.0: bool(phone().get("net"))   # the fake IP is not reachable
os.makedirs(server.STATE_DIR, exist_ok=True)
server.load_history(); server.load_config(); server.load_auth()

if os.environ.get("IBS_DEMO"):
    # ---- demo data: a believable install, for screenshots, the video and trying the interface without a phone
    import datetime, plistlib, secrets, time
    UDID = "00008999-0000000000AB001E"
    for d in os.listdir(vol): os.rmdir(os.path.join(vol, d)) if not os.listdir(os.path.join(vol, d)) else None
    for d in ("Documents", "Media", "Photos", "iOS Backups"): os.makedirs(os.path.join(vol, d), exist_ok=True)
    dest = os.path.join(vol, "iOS Backups", UDID); os.makedirs(dest, exist_ok=True)
    when = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(hours=19)
    with open(os.path.join(dest, "Status.plist"), "wb") as f: plistlib.dump({"Date": when, "SnapshotState": "finished", "IsFullBackup": False}, f)
    with open(os.path.join(dest, "Manifest.plist"), "wb") as f: plistlib.dump({"IsEncrypted": True}, f)
    salt = secrets.token_hex(16)
    server.AUTH.update(salt=salt, hash=server.hash_pw(os.environ.get("IBS_DEMO_PASSWORD", "demo"), salt), hint="the demo password is demo", secret=secrets.token_hex(32)); server.save_auth()
    server.CONF["dirs"][UDID] = os.path.join(vol, "iOS Backups")
    server.CONF["schedules"][UDID] = {"enabled": True, "time": "20:00", "days": [0, 1, 2, 3, 4, 5, 6], "retryHours": 3}
    server.save_config()
    now = int(time.time()); H = 3600
    server.HISTORY[:] = list(reversed([      # the server appends in time order, oldest first
        {"ts": now - 19 * H, "udid": UDID, "way": "wifi", "duration": 412, "status": "ok", "source": "schedule", "encrypted": True, "size": 71_600_000_000},
        {"ts": now - 43 * H, "udid": UDID, "way": "wifi", "duration": 233, "status": "ok", "source": "schedule", "encrypted": True, "size": 71_500_000_000},
        {"ts": now - 67 * H, "udid": UDID, "way": "usb", "duration": 2342, "status": "ok", "source": "manual", "encrypted": True, "size": 70_900_000_000},
        {"ts": now - 91 * H, "udid": UDID, "way": "wifi", "duration": 301, "status": "failed", "source": "schedule", "encrypted": True, "size": None,
         "error": "手机锁屏，需要输入锁屏密码，已放弃这次定时备份"},
        {"ts": now - 115 * H, "udid": UDID, "way": "wifi", "duration": 96, "status": "cancelled", "source": "manual", "encrypted": True, "size": None},
    ]))
    server.save_history()
    server.DIRSIZE[UDID] = 71_600_000_000
    server.refresh_dirsize = lambda udid: (server.DIRSIZE.__setitem__(udid, 71_700_000_000) or 71_700_000_000)
    server.volume_info = lambda cpath: {"name": "vol1", "label": "存储空间 1", "host": "/vol1/1000", "total": 3_000_000_000_000, "used": 1_700_000_000_000, "free": 1_300_000_000_000}
    if os.environ.get("IBS_DEMO_SHOW_HEALTH"):
        server.mounted_paths = lambda: {"/config"}          # pretend the backup folder is not mounted: shows the Setup check card
        _real_health = server.run_health
        def _health_for_docs():                              # show the path a real install has instead of this temporary folder
            items = _real_health()
            for it in items:
                for part in ("title", "fix"):
                    for lang in ("zh", "en"): it[part][lang] = it[part][lang].replace(vol, "/nas/backups")
            return items
        server.run_health = _health_for_docs
    else:
        server.run_health = lambda: []
    with open(os.path.join(fake, "phone.json"), "w") as f:
        json.dump({"name": "Alex's iPhone", "ptype": "iPhone18,4", "version": "27.0.1", "enc": True, "wifi_sync": True, "usb": False, "net": True,
                   "backup_seconds": float(os.environ.get("IBS_DEMO_BACKUP_SECONDS", "40")), "capacity": 256_000_000_000, "free": 38_000_000_000}, f)
for target in (server.mdns_watch, server.poller, server.scheduler):
    threading.Thread(target=target, daemon=True).start()
print("READY %d" % port, flush=True)
ThreadingHTTPServer(("127.0.0.1", port), server.Handler).serve_forever()
