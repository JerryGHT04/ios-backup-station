#!/usr/bin/env python3
"""Fake libimobiledevice command line tools for testing ios-backup-station without a phone.

Every tool reads and writes one JSON state file ($IBS_FAKE_DIR/phone.json), so a test can unplug the cable,
make usbmuxd "blind", lock the phone, make a backup fail, and so on. The output mimics the real tools,
including the ANSI cursor codes that idevicebackup2 uses for its progress display.
"""
import json, os, plistlib, random, signal, sys, time

D = os.environ["IBS_FAKE_DIR"]
STATE = os.path.join(D, "phone.json")
UDID = "00008999-0000000000AB001E"
DEFAULTS = {"usb": False, "net": True, "blind": False, "paired": True, "enc": True, "backup_password": "1234",
            "wifi_sync": True, "name": "Air", "ptype": "iPhone18,4", "version": "27.0.1", "usbmuxd_running": True,
            "locked_wait": 0.0, "backup_seconds": 8.0, "abort_delay": 1.0, "backup_fail": False,
            "second": None, "restarting_until": 0.0, "restarts": 0, "net_blind": False, "restart_fixes": True, "last_argv": [], "capacity": 256000000000, "free": 3400000000}


def load():
    try:
        with open(STATE) as f:
            s = dict(DEFAULTS); s.update(json.load(f)); return s
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE)


def out_plist(d):
    sys.stdout.buffer.write(plistlib.dumps(d)); sys.stdout.flush()


SECOND_UDID = "00008103-000A11B222C3D04E"


def second_visible(s, net):
    """The optional second device: s["second"] = {"conn": "usb" | "wifi", "name": ..., "ptype": ...}."""
    sec = s.get("second")
    return bool(sec) and sec.get("conn") == ("wifi" if net else "usb")


def target(a):
    return a[a.index("-u") + 1] if "-u" in a else UDID


def tool_idevice_id(a, s):
    if time.time() < s["restarting_until"]:
        print("ERROR: Unable to retrieve device list!"); return 255
    if "-n" in a:
        if s["net"] and not s["net_blind"]: print(UDID)
        if second_visible(s, True): print(SECOND_UDID)
    else:
        if s["usb"] and not s["blind"]: print(UDID)
        if second_visible(s, False): print(SECOND_UDID)
    return 0


def tool_ideviceinfo(a, s):
    net = "-n" in a
    if target(a) == SECOND_UDID:
        if not second_visible(s, net):
            print("ERROR: Device %s not found!" % SECOND_UDID, file=sys.stderr); return 255
        dom = a[a.index("-q") + 1] if "-q" in a else None
        sec = s["second"]
        if dom is None:
            out_plist({"DeviceName": sec.get("name", "iPad"), "ProductType": sec.get("ptype", "iPad13,1"), "ProductVersion": "18.6"})
        elif dom == "com.apple.mobile.backup":
            out_plist({"WillEncrypt": True})
        else:
            out_plist({})
        return 0
    if time.time() < s["restarting_until"] or (net and (not s["net"] or s["net_blind"])) or (not net and not (s["usb"] and not s["blind"])):
        print("ERROR: Device %s not found!" % UDID, file=sys.stderr); return 255
    if not s["paired"]:
        print("ERROR: Could not connect to lockdownd. Exiting.", file=sys.stderr); return 255
    dom = a[a.index("-q") + 1] if "-q" in a else None
    if dom is None:
        out_plist({"DeviceName": s["name"], "ProductType": s["ptype"], "ProductVersion": s["version"]})
    elif dom == "com.apple.mobile.backup":
        out_plist({} if s["enc"] is None else {"WillEncrypt": bool(s["enc"])})
    elif dom == "com.apple.disk_usage":
        out_plist({"TotalDiskCapacity": s["capacity"], "AmountDataAvailable": s["free"]})
    elif dom == "com.apple.mobile.wireless_lockdown":
        out_plist({"EnableWifiConnections": bool(s["wifi_sync"]), "SupportsWifiSyncing": True})
    else:
        out_plist({})
    return 0


def tool_idevicepair(a, s):
    cmd = "validate" if "validate" in a else ("pair" if "pair" in a else a[-1])   # options may come after the command
    if target(a) == SECOND_UDID:
        if cmd == "validate" and second_visible(s, "-n" in a): print("SUCCESS: Validated pairing with device " + SECOND_UDID); return 0
        print("ERROR: Device is not paired with this host"); return 1
    if cmd == "validate":
        if s["paired"]: print("SUCCESS: Validated pairing with device " + UDID); return 0
        print("ERROR: Device is not paired with this host"); return 1
    if cmd == "pair":
        if not s["usb"] or s["blind"]: print("ERROR: No device found"); return 1
        s["paired"] = True; save(s); print("SUCCESS: Paired with device " + UDID); return 0
    return 2


def tool_wifi_sync(a, s):
    if not (s["usb"] and not s["blind"]): print("ERROR: device not found over USB", file=sys.stderr); return 1
    if a[1] in ("on", "off"):
        s["wifi_sync"] = a[1] == "on"; save(s)
    print("EnableWifiConnections=" + ("true" if s["wifi_sync"] else "false")); return 0


def tool_pkill(a, s):
    if "usbmuxd" in a:
        s["restarts"] += 1; s["restarting_until"] = time.time() + 2.5
        if s["restart_fixes"]:
            s["blind"] = False; s["net_blind"] = False
        save(s)
    return 0


def tool_pgrep(a, s):
    return 0 if s["usbmuxd_running"] else 1


def tool_avahi_browse(a, s):
    """Stream resolved/removed events like 'avahi-browse -rp' does; also answers one-shot dumps."""
    def line(up):
        name = "aa:bb:cc:dd:ee:01@fe80::a8bb:ccff:fedd:ee01-supportsRP-26"
        return ("=;enp0s31f6;IPv4;%s;_apple-mobdev2._tcp;local;Test.local;192.0.2.24;32498;\"x\"" % name) if up else \
               ("-;enp0s31f6;IPv4;%s;_apple-mobdev2._tcp;local" % name)
    last = None
    while True:
        up = bool(load()["net"])
        if up != last and (up or last is not None):
            print(line(up), flush=True); last = up
        elif last is None:
            last = up
        if "-t" in "".join(a):
            return 0
        time.sleep(0.4)


def tool_idevicebackup2(a, s):
    argv = a[:]
    s["last_argv"] = argv; save(s)
    net = "-n" in argv
    if (net and (not s["net"] or s["net_blind"])) or (not net and not (s["usb"] and not s["blind"])):
        print("ERROR: No device found with udid %s." % UDID); return 1
    if "encryption" in argv:
        # Mirrors the real tool: the word after on/off is the password itself (not a backup folder). Without it the
        # environment is used: BACKUP_PASSWORD_NEW to turn encryption on, BACKUP_PASSWORD (the current one) to turn it off.
        i = argv.index("encryption"); want = argv[i + 1] == "on"
        given = argv[i + 2] if len(argv) > i + 2 else None
        if want:
            pw = given if given is not None else os.environ.get("BACKUP_PASSWORD_NEW")
            if s["enc"]:
                print("ERROR: Backup encryption is already enabled. Aborting."); return 1
            if not pw:
                print("No backup password given. Aborting."); return 1
            s["backup_password"] = pw
        else:
            pw = given if given is not None else os.environ.get("BACKUP_PASSWORD", "")
            if pw != s["backup_password"]:
                print("Status     ErrorCode 207: Invalid password when restoring encrypted backup"); return 49
        s["enc"] = want; save(s); print("Backup encryption is now " + ("enabled" if want else "disabled")); return 0
    if "backup" not in argv:
        return 2
    dest = os.path.join(argv[-1], UDID); os.makedirs(dest, exist_ok=True)
    state = {"abort": False}
    signal.signal(signal.SIGINT, lambda *x: state.update(abort=True))
    signal.signal(signal.SIGTERM, lambda *x: state.update(abort=True))
    w = lambda t: (sys.stdout.write(t), sys.stdout.flush())
    w("Backup directory is \"%s\"\n" % argv[-1]); w("Started \"com.apple.mobilebackup2\" service on port 1234.\n")
    w("Backup will be encrypted.\n" if s["enc"] else "Backup will not be encrypted.\n")
    w("Requesting backup from device...\n"); w("Incremental backup mode.\n")
    t0 = time.time()
    while time.time() - t0 < s["locked_wait"]:
        w("\033[2K\rStatus     *** Waiting for passcode to be entered on the device ***\n")
        if state["abort"]: break
        time.sleep(0.5)
    n = 0; total = max(1.0, float(s["backup_seconds"])); t1 = time.time(); aborting_at = None
    w("\n\n\n\033[s")
    while not state["abort"] or (aborting_at and time.time() - aborting_at < s["abort_delay"]):
        if state["abort"] and aborting_at is None:
            aborting_at = time.time(); w("\033[u\033[1A\033[1A\033[1A\033[2K\r\nStatus     Abort requested, hold on...\n")
        if aborting_at is None:
            frac = min(1.0, (time.time() - t1) / total); pct = int(frac * 100)
            # a per-file transfer bar that keeps restarting, like the real tool
            fpct = (time.time() * 90) % 100
            w("\033[u\033[1A\033[1A\033[1A\033[2K\rBackup     [%s%s] %3d%%\n\033[2K\rReceiving  Library/file%d.dat\n\033[2K\r           [%s>%s] %5.1f%%   1.0 MB / 2.0 MB\n\033[2K\rStatus     Receiving files\033[s"
              % ("#" * (pct // 4), "." * (25 - pct // 4), pct, n, "=" * int(fpct // 4), " " * (24 - int(fpct // 4)), fpct))
            with open(os.path.join(dest, "f%04d" % n), "wb") as f:
                f.write(os.urandom(20000))
            n += 1
            if s["backup_fail"] and time.time() - t1 > 1.5:
                w("\nERROR: Backup failed: device disconnected\n"); return 1
            if frac >= 1.0:
                with open(os.path.join(dest, "Status.plist"), "wb") as f:
                    plistlib.dump({"Date": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(tzinfo=None), "SnapshotState": "finished", "IsFullBackup": False}, f)
                with open(os.path.join(dest, "Manifest.plist"), "wb") as f:
                    plistlib.dump({"IsEncrypted": bool(s["enc"])}, f)
                w("\nReceived %d files from device.\nBackup Successful.\n" % n); return 0
        time.sleep(0.25)
    w("\nReceived %d files from device.\nBackup Aborted.\n" % n); return 255


TOOLS = {"idevice_id": tool_idevice_id, "ideviceinfo": tool_ideviceinfo, "idevicepair": tool_idevicepair, "wifi-sync": tool_wifi_sync,
         "pkill": tool_pkill, "pgrep": tool_pgrep, "avahi-browse": tool_avahi_browse, "idevicebackup2": tool_idevicebackup2}

if __name__ == "__main__":
    name = sys.argv[1]
    sys.exit(TOOLS[name](sys.argv[2:], load()))
