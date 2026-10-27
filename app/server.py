#!/usr/bin/env python3
"""iPhone backup web UI backend. Wraps libimobiledevice CLI tools; stdlib only."""
import calendar
import collections
import datetime
import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import select
import shutil
import signal
import socket
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PORT = int(os.environ.get("PORT", "8899"))
STATE_DIR = os.environ.get("STATE_DIR", "/config")
HIST_FILE = os.path.join(STATE_DIR, "history.json")
CONF_FILE = os.path.join(STATE_DIR, "settings.json")
AUTH_FILE = os.path.join(STATE_DIR, "auth.json")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# NAS_ROOTS="backups=/path/on/the/nas": name=host path (shown in the UI); mounted in the container at /nas/<name>.
ROOTS = {}
for _item in os.environ.get("NAS_ROOTS", "").split(","):
    if "=" in _item:
        _n, _h = _item.split("=", 1)
        _n = _n.strip()
        _m = re.fullmatch(r"vol(\d+)", _n)
        ROOTS[_n] = {"name": _n, "cpath": "/nas/" + _n, "host": _h.strip(),
                     "label": ("存储空间 " + _m.group(1)) if _m else ("备份文件夹" if _n == "backups" else _n)}

MOBILE_UA = re.compile(r"iPhone|iPod|Android.*Mobile", re.I)   # iPads are told apart in the browser (they pose as Macs)
PRODUCT_NAMES = {"iPhone18,4": "iPhone Air", "iPhone19,4": "iPhone Duo"}
UDID_RE = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}|[0-9a-f]{40}")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PROGRESS_RE = re.compile(r"Backup\s+\[[#.\s]*\]\s+(\d{1,3})\s*%")   # searched, not anchored: tolerate frames glued together
XFER_BAR_RE = re.compile(r"^\[[=> ]*\]\s+\d+(\.\d+)?\s*%")

LOG = collections.deque(maxlen=500)
lock = threading.RLock()          # guards DEV, JOB, RESULT, HISTORY, DIRSIZE, BACKUP_DIR
busy = threading.Lock()           # one backup / encryption change at a time

CONF = {"dirs": {}, "schedules": {}, "selected": None}  # dirs: UDID -> backup folder; schedules: UDID -> schedule; selected: the device chosen in the UI
AUTH = {}                         # salt, hash, hint, secret for the web password
FAILS = {}                        # client ip -> {"n": failed attempts, "until": lock timestamp}
DEV = {"udid": None, "conn": None, "paired": None, "name": None, "product": None,
       "version": None, "capacity": None, "free": None, "encrypted": None,
       "wifiSync": None, "ptype": None, "usbIds": [], "netIds": []}
KNOWN = {}                        # UDID -> {"name", "ptype"} of every device seen, for the device list
JOB = None
RESULT = None
HISTORY = []
DIRSIZE = {}                      # UDID -> bytes used by that device's backup folder


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    LOG.append(line)
    print(line, flush=True)


def run(argv, env=None, timeout=15):
    """Run a command, return (returncode, stdout+stderr bytes). 124 on timeout."""
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=env, timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 124, b""
    except FileNotFoundError:
        return 127, b""


# ---------------------------------------------------------------- folders / settings
def root_of(real):
    for r in ROOTS.values():
        if real == r["cpath"] or real.startswith(r["cpath"] + os.sep):
            return r
    return None


def safe_dir(cpath):
    """Resolve a container path; only directories inside one of the NAS roots are allowed."""
    if not cpath or any(ord(c) < 32 for c in cpath):
        return None
    real = os.path.realpath(cpath)
    return real if root_of(real) and os.path.isdir(real) else None


def to_host(cpath):
    r = root_of(cpath) if cpath else None
    return (r["host"] + cpath[len(r["cpath"]):]) if r else cpath


def volume_info(cpath):
    r = root_of(cpath)
    if not r:
        return None
    try:
        du = shutil.disk_usage(r["cpath"])
        return {"name": r["name"], "label": r["label"], "host": r["host"],
                "total": du.total, "used": du.used, "free": du.free}
    except OSError:
        return None


def backup_dir(udid):
    d = CONF["dirs"].get(udid) if udid else None
    return safe_dir(d) if d else None


def load_config():
    try:
        with open(CONF_FILE) as f:
            data = json.load(f)
        dirs = data.get("dirs", {})
        CONF["dirs"] = {k: v for k, v in dirs.items() if isinstance(v, str)}
        CONF["schedules"] = {k: v for k, v in data.get("schedules", {}).items() if isinstance(v, dict)}
        sel = data.get("selected")
        CONF["selected"] = sel if isinstance(sel, str) else None
    except Exception:
        CONF["dirs"], CONF["schedules"], CONF["selected"] = {}, {}, None


def save_config():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = CONF_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"dirs": CONF["dirs"], "schedules": CONF["schedules"], "selected": CONF["selected"]}, f)
        os.replace(tmp, CONF_FILE)
    except Exception as e:
        log("cannot save settings: %r" % e)


def suggest_name():
    with lock:
        p = (DEV["product"] or DEV["name"] or "").lower()
    return "ipad_backups" if "ipad" in p else "iphone_backups"


def browse(cpath):
    if not cpath:
        vols = []
        for r in ROOTS.values():
            v = volume_info(r["cpath"])
            if v:
                vols.append({"label": r["label"], "path": r["cpath"], "host": r["host"],
                             "total": v["total"], "free": v["free"]})
        best = max(vols, key=lambda v: v["free"]) if vols else None
        sug = None
        if best:
            nm = suggest_name()
            sug = {"path": best["path"] + "/" + nm, "host": best["host"] + "/" + nm}
        return 200, {"path": "", "host": "", "parent": None, "volumes": vols, "dirs": [], "suggest": sug}
    real = safe_dir(cpath)
    if not real:
        return 400, {"error": "这个位置不可用"}
    r = root_of(real)
    try:
        names = sorted(n for n in os.listdir(real)
                       if not n.startswith((".", "@")) and os.path.isdir(os.path.join(real, n)))
    except OSError as e:
        return 400, {"error": "无法读取这个文件夹：%s" % e.strerror}
    parent = "" if real == r["cpath"] else os.path.dirname(real)
    return 200, {"path": real, "host": to_host(real), "parent": parent, "volumes": [],
                 "dirs": names, "volume": volume_info(real)}


def make_folder(parent, name):
    real = safe_dir(parent)
    name = (name or "").strip()
    if not real:
        return 400, {"error": "这个位置不可用"}
    if not name or len(name) > 64 or "/" in name or "\\" in name or name.startswith((".", "@")) or any(ord(c) < 32 for c in name):
        return 400, {"error": "文件夹名称不可用"}
    try:
        os.makedirs(os.path.join(real, name), exist_ok=True)
    except OSError as e:
        return 400, {"error": "无法新建：%s" % e.strerror}
    return 200, {"ok": True, "path": os.path.join(real, name)}


def set_backup_dir(udid, cpath):
    if not udid:
        return 409, {"error": "请先连接设备，再为它选择备份位置"}
    if JOB is not None or busy.locked():
        return 409, {"error": "有任务在运行，稍后再更改"}
    real = safe_dir(cpath)
    if not real:
        return 400, {"error": "这个位置不可用"}
    probe = os.path.join(real, ".write-test-%d" % os.getpid())
    try:
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
    except OSError as e:
        return 400, {"error": "这个文件夹不能写入：%s" % e.strerror}
    with lock:
        CONF["dirs"][udid] = real
        DIRSIZE.pop(udid, None)
        save_config()
    log("backup folder for %s: %s" % (udid, real))
    threading.Thread(target=refresh_dirsize, args=(udid,), daemon=True).start()
    return 200, {"ok": True}


# ---------------------------------------------------------------- device polling
def list_ids(network):
    rc, out = run(["idevice_id", "-n" if network else "-l"], timeout=4)
    return UDID_RE.findall(out.decode("utf-8", "replace")) if rc == 0 else []


def pick_device(usb, net, selected):
    """Which device the UI works with: the one the user chose if it is still visible, otherwise USB first, then Wi-Fi."""
    seen = list(dict.fromkeys(list(usb) + list(net)))
    udid = selected if selected in seen else (usb[0] if usb else (net[0] if net else None))
    return udid, (None if not udid else ("usb" if udid in usb else "wifi"))


def query(udid, network, domain=None):
    argv = ["ideviceinfo", "-u", udid, "-x"] + (["-n"] if network else [])
    if domain:
        argv += ["-q", domain]
    rc, out = run(argv, timeout=15)
    if rc != 0 or not out.strip():
        return None
    try:
        return plistlib.loads(out)
    except Exception:
        return None


def refresh_info(udid, network):
    rc, _ = run(["idevicepair", "-u", udid, "validate"] + (["-n"] if network else []), timeout=10)
    paired = rc == 0
    info = {"paired": paired}
    if paired:
        base = query(udid, network) or {}
        bk = query(udid, network, "com.apple.mobile.backup") or {}
        du = query(udid, network, "com.apple.disk_usage") or {}
        wl = query(udid, network, "com.apple.mobile.wireless_lockdown") or {}
        info.update(
            name=base.get("DeviceName"),
            ptype=base.get("ProductType"),
            product=PRODUCT_NAMES.get(base.get("ProductType")),
            version=base.get("ProductVersion"),
            capacity=du.get("TotalDiskCapacity") or du.get("TotalDataCapacity"),
            free=du.get("AmountDataAvailable"),
            encrypted=bk.get("WillEncrypt") if "WillEncrypt" in bk else None,
            wifiSync=bool(wl.get("EnableWifiConnections")),
        )
    with lock:
        DEV.update(info)


def learn_device(udid, network):
    """Read just the name and model of a device, so that it can be listed in the device switcher."""
    base = query(udid, network) or {}
    if base.get("DeviceName"):
        with lock:
            KNOWN[udid] = {"name": base.get("DeviceName"), "ptype": base.get("ProductType")}


def select_device(udid):
    with lock:
        visible = list(dict.fromkeys(list(DEV["usbIds"]) + list(DEV["netIds"])))
        if JOB is not None or DELETING is not None or busy.locked():
            return 409, {"error": "有任务在运行，稍后再切换设备"}
        if udid not in visible:
            return 404, {"error": "这台设备现在不在线"}
        CONF["selected"] = udid
        save_config()
    MDNS_EVENT.set()                   # wake the poller so that the page follows quickly
    log("device selected: %s" % udid)
    return 200, {"ok": True}


MDNS = {}                          # "<iface>/<service name>" -> IPv4 of iOS devices on the LAN
MDNS_EVENT = threading.Event()     # set whenever the host avahi announces/resolves a device


def mdns_watch():
    """Stream _apple-mobdev2._tcp announcements from the host avahi (event driven, no polling)."""
    while True:
        try:
            p = subprocess.Popen(["avahi-browse", "-rp", "_apple-mobdev2._tcp"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
            for line in p.stdout:
                f = line.rstrip("\n").split(";")
                if line.startswith("=") and len(f) > 7 and f[2] == "IPv4":
                    MDNS[f[1] + "/" + f[3]] = f[7]
                    MDNS_EVENT.set()
                elif line.startswith("-") and len(f) > 3:
                    MDNS.pop(f[1] + "/" + f[3], None)
                    MDNS_EVENT.set()
            p.wait()
        except Exception as e:
            log("mdns watcher error: %r" % e)
        time.sleep(5)


def mdns_phone_ips():
    return sorted(set(MDNS.values()))


def restart_usbmuxd(why):
    log("%s; restarting usbmuxd2" % why)
    run(["pkill", "-x", "usbmuxd"])


def tcp_open(ip, port=62078, timeout=2.0):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def apple_usb_present():
    """True if an iPhone, iPad or iPod is on the USB bus. Read from sysfs, so it works even when usbmuxd2 is blind."""
    try:
        for name in os.listdir("/sys/bus/usb/devices"):
            base = "/sys/bus/usb/devices/" + name
            try:
                with open(base + "/idVendor") as f:
                    vendor = f.read().strip()
                with open(base + "/product") as f:
                    product = f.read().strip()
            except OSError:
                continue
            if vendor == "05ac" and re.match(r"(iPhone|iPad|iPod)", product):
                return True
    except OSError:
        pass
    return False


def restart_cooldown(times, now, base):
    """Normal cooldown between usbmuxd2 restarts; 5 minutes if it already restarted 5 times in the last 10 minutes
    (then restarting evidently does not help, and every restart interrupts the Wi-Fi connection)."""
    recent = [t for t in times if now - t < 600]
    return base if len(recent) < 5 else 300.0


def usb_watchdog_step(missing_since, now, blind, last_restart, cooldown=10.0):
    """blind = a device is on the USB bus but usbmuxd2 does not list it. Restart usbmuxd2 after 3 s of that.
    (USB hotplug events do not always reach usbmuxd2 inside a container; a restart makes it rescan the bus.)"""
    if not blind:
        return False, None
    if missing_since is None:
        return False, now
    if now - missing_since > 3.0 and now - last_restart > cooldown:
        return True, None
    return False, missing_since


def watchdog_step(missing_since, now, ips, last_restart, unplugged_at, cooldown_scale=None):
    """Decide whether usbmuxd2 must be restarted. Returns (restart, new missing_since).
    Normally the phone must be reachable-but-unlisted for 8 s. Right after a USB cable was pulled the wait is
    1 s, so the phone shows up again over Wi-Fi within a few seconds."""
    if not ips:
        return False, None
    if missing_since is None:
        return False, now
    wait = 1.0 if (unplugged_at is not None and now - unplugged_at < 20) else 8.0
    cooldown = cooldown_scale if cooldown_scale is not None else (8.0 if wait < 8.0 else 30.0)
    if now - missing_since > wait and now - last_restart > cooldown:
        return True, None
    return False, missing_since


def poller():
    last_info = 0.0
    last_udid = None          # the last device that was selected, to tell "another device" from "the same one back"
    last_others = 0.0         # when the names of the devices that are not selected were last read
    missing_since = None      # phone reachable on the LAN but not listed by usbmuxd2
    last_restart = 0.0
    unplugged_at = None       # when the USB cable was last pulled
    usb_blind_since = None    # an iPhone/iPad is on the USB bus but usbmuxd2 does not list it
    last_usb_restart = 0.0
    restart_times = []        # every automatic usbmuxd2 restart, for the back-off
    while True:
        try:
            usb, net = list_ids(False), list_ids(True)
            with lock:
                udid, conn = pick_device(usb, net, CONF["selected"])
                changed = (udid, conn) != (DEV["udid"], DEV["conn"])
                if DEV["conn"] == "usb" and conn != "usb":
                    unplugged_at = time.time()
                if udid and last_udid and udid != last_udid:      # a different device, not the same one coming back after a drop
                    DEV.update(paired=None, name=None, product=None, version=None, capacity=None, free=None,
                               encrypted=None, wifiSync=None, ptype=None)
                DEV.update(udid=udid, conn=conn, usbIds=usb, netIds=net)
                running = JOB is not None
                if udid:
                    last_udid = udid
            if changed:
                log("device: %s via %s" % (udid or "none", conn or "-"))
                if udid and udid not in DIRSIZE and backup_dir(udid):
                    threading.Thread(target=refresh_dirsize, args=(udid,), daemon=True).start()
            with lock:
                paired_now = DEV["paired"]
            # every 30 s normally, every 5 s while unpaired so that tapping "Trust" on the phone shows up quickly
            if udid and not running and (changed or time.time() - last_info > (30 if paired_now else 5)):
                refresh_info(udid, conn == "wifi")
                last_info = time.time()
            if not udid:
                with lock:
                    DEV["paired"] = None
            if not running and time.time() - last_others > 60:
                last_others = time.time()
                for other in dict.fromkeys(usb + net):
                    if other != udid:
                        learn_device(other, other in net and other not in usb)
            blind = (not usb) and (not running) and apple_usb_present()
            go_usb, usb_blind_since = usb_watchdog_step(usb_blind_since, time.time(), blind, last_usb_restart,
                                                        restart_cooldown(restart_times, time.time(), 10.0))
            if go_usb:
                restart_usbmuxd("an iPhone/iPad is plugged in over USB but usbmuxd2 does not see it")
                last_usb_restart = time.time()
                restart_times.append(last_usb_restart)
            # Watchdog: usbmuxd2 only re-adds a Wi-Fi device when avahi announces it as *new*. After the
            # phone switches band / network it can stay unlisted although it is reachable. Restart usbmuxd2.
            if not udid and not running:
                ips = [ip for ip in mdns_phone_ips() if tcp_open(ip)]
                now = time.time()
                quick = unplugged_at is not None and now - unplugged_at < 20
                base_cd = 8.0 if quick else 30.0
                go, missing_since = watchdog_step(missing_since, now, ips, last_restart, unplugged_at,
                                                  restart_cooldown(restart_times, now, base_cd))
                if go:
                    restart_usbmuxd("phone is reachable at %s but usbmuxd2 does not list it%s" % (ips[0], " (USB cable was pulled)" if unplugged_at and now - unplugged_at < 20 else ""))
                    last_restart = now
                    restart_times.append(now)
            else:
                missing_since = None
        except Exception as e:  # keep the poller alive
            log("poller error: %r" % e)
        MDNS_EVENT.wait(3)             # wake up early when the phone announces itself
        MDNS_EVENT.clear()


# ---------------------------------------------------------------- backup state on disk
def read_last(udid):
    base = backup_dir(udid)
    if not base:
        return None
    d = os.path.join(base, udid)
    try:
        with open(os.path.join(d, "Status.plist"), "rb") as f:
            st = plistlib.load(f)
    except Exception:
        return None
    date = st.get("Date")
    ts = calendar.timegm(date.utctimetuple()) if date else None
    enc = None
    try:
        with open(os.path.join(d, "Manifest.plist"), "rb") as f:
            enc = bool(plistlib.load(f).get("IsEncrypted"))
    except Exception:
        pass
    return {"ts": ts, "state": st.get("SnapshotState"), "full": st.get("IsFullBackup"), "encrypted": enc}


def dir_bytes(path, timeout=3600):
    rc, out = run(["du", "-sb", path], timeout=timeout)
    try:
        return int(out.split()[0]) if rc == 0 else None
    except Exception:
        return None


def refresh_dirsize(udid):
    base = backup_dir(udid)
    if not base:
        return None
    d = os.path.join(base, udid)
    n = dir_bytes(d) if os.path.isdir(d) else 0
    with lock:
        if backup_dir(udid) == base:
            DIRSIZE[udid] = n
    return n


def load_history():
    global HISTORY
    try:
        with open(HIST_FILE) as f:
            HISTORY = json.load(f)
    except Exception:
        HISTORY = []


def save_history():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = HIST_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(HISTORY[-200:], f, ensure_ascii=False)
        os.replace(tmp, HIST_FILE)
    except Exception as e:
        log("cannot save history: %r" % e)


# ---------------------------------------------------------------- backup job
def start_backup(source="manual"):
    with lock:
        d = dict(DEV)
    if not d["udid"]:
        return 409, {"error": "未检测到设备"}
    if not d["paired"]:
        return 409, {"error": "尚未配对"}
    base = backup_dir(d["udid"])
    if not base:
        return 409, {"error": "请先选择这台设备的备份保存位置"}
    if not busy.acquire(blocking=False):
        return 409, {"error": "已有任务在运行"}
    try:
        return _launch_backup(d, base, source)
    except Exception as e:           # never keep the lock if something unexpected happens before the job thread exists
        global_reset_job()
        log("could not start the backup: %r" % e)
        return 500, {"error": "无法开始备份：%s" % e.__class__.__name__}


def global_reset_job():
    global JOB
    with lock:
        JOB = None
    if busy.locked():
        try:
            busy.release()
        except RuntimeError:
            pass


def _launch_backup(d, base, source):
    global JOB
    udid = d["udid"]
    # USB always wins: look again right now, because the poller can be a few seconds behind a freshly plugged-in cable.
    on_usb = udid in list_ids(False)
    net = (not on_usb) and d["conn"] == "wifi"
    if on_usb and d["conn"] != "usb":
        with lock:
            DEV["conn"] = "usb"
        log("USB cable detected, backing up over USB")
    # stdbuf -o0: without it the tool block-buffers stdout when piped and progress never reaches us.
    argv = ["stdbuf", "-o0", "idevicebackup2", "-u", udid] + (["-n"] if net else []) + ["backup", base]
    with lock:
        JOB = {"kind": "backup", "pct": 0.0, "msg": "", "status": "", "file": "", "bytes": None,
               "started": time.time(), "way": "wifi" if net else "usb", "cancel": False,
               "proc": None, "samples": collections.deque(maxlen=1000), "eta": None, "first_pct_at": None,
               "source": source, "pass_since": None, "reason": None}
    threading.Thread(target=run_backup, args=(argv, udid, base), daemon=True).start()
    return 200, {"ok": True}


def written_bytes(pid):
    """Bytes the process has written so far (from /proc). Counts overwritten files too, unlike the folder size."""
    try:
        with open("/proc/%d/io" % pid) as f:
            for line in f:
                if line.startswith("wchar:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def io_sampler(job):
    """Keeps job["bytes"] = what THIS backup run has written. It must not be the folder size: an interrupted
    backup leaves its files behind, a new run overwrites them, and the folder would show the old backup's size."""
    samples = collections.deque(maxlen=40)        # (time, bytes): the speed is measured over the last ~30 s
    while JOB is job and job.get("proc") is not None and job["proc"].poll() is None:
        n = written_bytes(job["proc"].pid)
        if n is not None:
            now = time.time()
            job["bytes"] = n
            samples.append((now, n))
            while len(samples) > 2 and now - samples[0][0] > 30:
                samples.popleft()
            dt = now - samples[0][0]
            job["speed"] = (n - samples[0][1]) / dt if dt >= 4 else None     # bytes per second
        time.sleep(2)


def passcode_guard(job):
    """Unattended (scheduled) runs give up when the locked phone keeps waiting for its passcode."""
    while JOB is job and job.get("proc") is not None and job["proc"].poll() is None:
        ps = job.get("pass_since")
        if job.get("source") == "schedule" and ps and time.time() - ps > 300 and not job["cancel"]:
            job["reason"] = "手机锁屏，需要输入锁屏密码，已放弃这次定时备份"
            log(job["reason"])
            cancel_job()
            return
        time.sleep(5)


def update_eta(job):
    """Smoothed time left, from the overall percentage.

    The real idevicebackup2 redraws its progress many times per second, so a sample is taken at most once per
    second: a fixed number of samples must stand for a fixed stretch of time (a per-line sample list covered
    only seconds and the estimate never appeared). The percentage has whole-percent steps, which can be minutes
    apart on a big backup, so the window is long (15 minutes)."""
    now = time.time()
    if now - job.get("last_sample", 0.0) >= 1.0:
        job["samples"].append((now, job["pct"]))
        job["last_sample"] = now
    if job["pct"] < 1 or job["first_pct_at"] is None or now - job["first_pct_at"] < 60:
        job["eta"] = None
        return
    old = None
    for t, p in job["samples"]:
        if now - t <= 900:
            old = (t, p)
            break
    if not old or now - old[0] < 45 or job["pct"] <= old[1]:
        return
    rate = (job["pct"] - old[1]) / (now - old[0])        # percent per second
    eta = (100 - job["pct"]) / rate
    first = job["eta"] is None
    job["eta"] = eta if first else 0.7 * job["eta"] + 0.3 * eta
    if first:
        log("time-left estimate available: about %d min" % round(eta / 60))


def handle_line(job, s, tail):
    m = PROGRESS_RE.search(s)
    if m:
        p = min(100.0, float(m.group(1)))
        if p > job["pct"]:                     # never go backwards
            if int(p) != int(job["pct"]):
                log("progress %d%%" % int(p))      # one line per whole percent: shows in the log whether the phone reports progress
            job["pct"] = p
            if job["first_pct_at"] is None and p > 0:
                job["first_pct_at"] = time.time()
        update_eta(job)
        return
    if XFER_BAR_RE.match(s):
        return                                  # per-file bar, not overall progress
    m = re.match(r"^(Receiving|Sending)\s+(.+)$", s)
    if m:
        job["file"] = m.group(2)[-80:]
        return
    m = re.match(r"^Status\s+(.*)$", s)
    if m:
        txt = m.group(1).strip()
        if txt and txt != job["status"]:
            job["status"] = txt
            job["pass_since"] = time.time() if "passcode" in txt.lower() else None
            log("status: " + txt)
        return
    if s == "Status" or s == "Backup":
        return
    job["msg"] = s
    tail.append(s)
    log(s)


def run_backup(argv, udid, base):
    global JOB, RESULT
    job = JOB
    log("backup start -> %s" % base)
    tail = collections.deque(maxlen=8)
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        job["proc"] = proc
        threading.Thread(target=io_sampler, args=(job,), daemon=True).start()
        threading.Thread(target=passcode_guard, args=(job,), daemon=True).start()
        buf = b""
        fd = proc.stdout.fileno()
        while True:
            # Do not wait for the pipe to close: if the tool is gone (even with a stray child holding the pipe) we are done.
            ready, _, _ = select.select([fd], [], [], 1.0)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]+", buf)
            buf = parts.pop()
            for part in parts:
                s = ANSI_RE.sub("", part.decode("utf-8", "replace")).strip()
                if s:
                    handle_line(job, s, tail)
        rc = proc.wait()
    except Exception as e:
        rc = -1
        tail.append(repr(e))
    ended = time.time()
    cancelled = job["cancel"]
    ok = rc == 0 and not cancelled
    last = read_last(udid)
    reason = job.get("reason")
    status = "failed" if reason else ("cancelled" if cancelled else ("ok" if ok else "failed"))
    entry = {"ts": int(ended), "udid": udid, "way": job["way"], "duration": int(ended - job["started"]),
             "status": status, "source": job.get("source", "manual"),
             "encrypted": last["encrypted"] if last else None, "size": None, "dir": to_host(base),
             "error": reason or (None if ok or cancelled else " / ".join(list(tail)[-3:]))}
    log("backup %s (rc=%s, %ds)" % (entry["status"], rc, entry["duration"]))
    with lock:
        HISTORY.append(entry)
        save_history()
        RESULT = {"ts": int(ended), "kind": "backup", "status": entry["status"], "error": entry["error"]}
        JOB = None
    busy.release()
    size = refresh_dirsize(udid)                 # keeps the storage bar up to date
    if entry["status"] == "ok":                  # an interrupted or failed run has no "backup size": the folder still holds the previous backup
        with lock:
            entry["size"] = size
            save_history()


# ---------------------------------------------------------------- delete the backup
DELETING = None                      # {"started": ts} while a backup folder is being removed
UDID_FOLDER_RE = re.compile(r"^[0-9A-Za-z-]{8,64}$")


def delete_backup(confirm):
    """Remove this device's backup folder. Only <backup dir>/<UDID> is ever touched, and only if it looks like a backup."""
    global DELETING
    with lock:
        d = dict(DEV)
    udid = d["udid"]
    if not udid or not UDID_FOLDER_RE.match(udid):
        return 409, {"error": "未检测到设备"}
    if confirm != udid:
        return 400, {"error": "确认信息不匹配"}
    base = backup_dir(udid)
    if not base:
        return 409, {"error": "还没有选择备份保存位置"}
    target = os.path.join(os.path.realpath(base), udid)
    if os.path.islink(target) or not os.path.isdir(target) or os.path.realpath(target) != target:
        return 404, {"error": "这台设备在备份位置里没有备份"}
    if not any(os.path.exists(os.path.join(target, n)) for n in ("Manifest.plist", "Info.plist", "Status.plist")):
        return 409, {"error": "这个文件夹看起来不是备份，为了安全没有删除"}
    if not busy.acquire(blocking=False):
        return 409, {"error": "正在备份或删除，请稍后再试"}
    trash = "%s.deleting-%d" % (target, int(time.time()))
    try:
        os.rename(target, trash)         # atomic: from here on nothing looks like a backup any more
    except OSError as e:
        busy.release()
        log("cannot remove the backup: %r" % e)
        return 500, {"error": "无法删除：%s" % e.__class__.__name__}
    with lock:
        DELETING = {"started": time.time()}

    def worker():
        global DELETING
        try:
            shutil.rmtree(trash, ignore_errors=True)
            if os.path.isdir(trash):     # something refused to go away; the next start tries again
                log("backup folder only partly removed: %s" % trash)
            with lock:
                HISTORY[:] = [h for h in HISTORY if h.get("udid") not in (None, udid)]
                save_history()
                DIRSIZE.pop(udid, None)
            log("backup of %s deleted" % udid)
        finally:
            with lock:
                DELETING = None
            busy.release()
            refresh_dirsize(udid)
    threading.Thread(target=worker, daemon=True).start()
    log("deleting the backup of %s" % udid)
    return 200, {"ok": True}


def sweep_trash():
    """Finish deletions that a restart or power cut interrupted."""
    for base in list(CONF["dirs"].values()):
        real = safe_dir(base)
        if not real:
            continue
        for n in os.listdir(real):
            if ".deleting-" in n and UDID_FOLDER_RE.match(n.split(".deleting-")[0]):
                p = os.path.join(real, n)
                if os.path.isdir(p) and not os.path.islink(p):
                    log("removing leftover %s" % n)
                    shutil.rmtree(p, ignore_errors=True)


def cancel_job():
    with lock:
        job = JOB
    if not job or not job.get("proc"):
        return 409, {"error": "没有正在运行的备份"}
    job["cancel"] = True
    proc = job["proc"]
    proc.send_signal(signal.SIGINT)

    def killer():
        time.sleep(15)
        if proc.poll() is None:
            proc.terminate()
    threading.Thread(target=killer, daemon=True).start()
    log("cancel requested")
    return 200, {"ok": True}


# ---------------------------------------------------------------- other actions
def set_encryption(enable, password):
    with lock:
        d = dict(DEV)
    if not d["udid"] or not d["paired"]:
        return 409, {"error": "请先连接并配对设备"}
    if not backup_dir(d["udid"]):
        return 409, {"error": "请先选择这台设备的备份保存位置"}
    if not password:
        return 400, {"error": "请输入密码"}
    if not busy.acquire(blocking=False):
        return 409, {"error": "已有任务在运行"}
    try:
        net = d["conn"] == "wifi"
        # The tool takes whatever follows on/off as the password, so never put a folder there. The password goes in the
        # environment instead: BACKUP_PASSWORD_NEW sets one, BACKUP_PASSWORD (the current one) removes it.
        argv = ["idevicebackup2", "-u", d["udid"]] + (["-n"] if net else []) + ["encryption", "on" if enable else "off"]
        env = dict(os.environ, **({"BACKUP_PASSWORD_NEW": password} if enable else {"BACKUP_PASSWORD": password}))
        log("encryption %s requested" % ("on" if enable else "off"))
        rc, out = run(argv, env=env, timeout=120)
        refresh_info(d["udid"], net)
        with lock:
            now = DEV["encrypted"]
        text = ANSI_RE.sub("", out.decode("utf-8", "replace")).strip().splitlines()
        if now == enable:
            log("encryption is now %s" % ("on" if enable else "off"))
            return 200, {"ok": True}
        msg = text[-1] if text else "手机没有确认，请检查手机屏幕后重试"
        log("encryption change failed: %s" % msg)
        return 200, {"ok": False, "error": msg}
    finally:
        busy.release()


def do_pair():
    with lock:
        d = dict(DEV)
    if d["conn"] != "usb":
        return 409, {"error": "配对需要 USB 连接"}
    rc, out = run(["idevicepair", "-u", d["udid"], "pair"], timeout=90)
    text = out.decode("utf-8", "replace").strip()
    refresh_info(d["udid"], False)
    log("pair: %s" % text)
    return 200, {"ok": rc == 0, "message": text}


def do_wifi_sync(enable):
    with lock:
        d = dict(DEV)
    if d["conn"] != "usb" or not d["paired"]:
        return 409, {"error": "开启 Wi-Fi 同步需要 USB 连接并已配对"}
    rc, out = run(["wifi-sync", d["udid"], "on" if enable else "off"], timeout=20)
    refresh_info(d["udid"], False)
    text = out.decode("utf-8", "replace").strip()
    log("wifi-sync %s: %s" % ("on" if enable else "off", text))
    return 200, {"ok": rc == 0, "message": text}


def do_probe():
    """Check connectivity; if the phone is on the LAN but usbmuxd2 misses it, reconnect right away."""
    usb, net = list_ids(False), list_ids(True)
    restarted = False
    if not usb and not net and JOB is None:
        ips = [ip for ip in mdns_phone_ips() if tcp_open(ip)]
        if ips:
            restart_usbmuxd("probe: phone is reachable at %s but usbmuxd2 does not list it" % ips[0])
            restarted = True
            for _ in range(8):
                time.sleep(1.5)
                net = list_ids(True)
                if net:
                    break
    return 200, {"usb": usb, "net": net, "restarted": restarted}


# ---------------------------------------------------------------- schedule
def sched_for(udid):
    s = {"enabled": False, "time": "20:00", "days": [0, 1, 2, 3, 4, 5, 6], "retryHours": 3}
    if udid:
        s.update(CONF["schedules"].get(udid, {}))
    return s


def sched_info(udid):
    s = sched_for(udid)
    now = datetime.datetime.now()
    info = dict(s, next=None, nextStr=None,
                serverTime=now.strftime("%Y-%m-%d %H:%M"),
                tz="UTC%s:%s" % (time.strftime("%z")[:3], time.strftime("%z")[3:]))
    if udid and s["enabled"]:
        h, m = (int(x) for x in s["time"].split(":"))
        for add in range(8):
            st = (now + datetime.timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
            if st.weekday() in s["days"] and st > now:
                info["next"] = int(st.timestamp())
                info["nextStr"] = st.strftime("%Y-%m-%d %H:%M")
                break
    return info


def set_schedule(udid, data):
    if not udid:
        return 409, {"error": "请先连接手机，再设置定时备份"}
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(data.get("time", "")))
    if not m:
        return 400, {"error": "时间格式不对"}
    days = sorted({x for x in (data.get("days") or []) if isinstance(x, int) and 0 <= x <= 6})
    enabled = bool(data.get("enabled"))
    if enabled and not days:
        return 400, {"error": "请至少选择一天"}
    try:
        rh = int(data.get("retryHours", 3))
    except (TypeError, ValueError):
        rh = 3
    if rh not in (0, 1, 3, 6):
        rh = 3
    with lock:
        CONF["schedules"][udid] = {"enabled": enabled, "time": m.group(0), "days": days, "retryHours": rh}
        save_config()
    log("schedule for %s: %s" % (udid, CONF["schedules"][udid]))
    return 200, {"ok": True}


def schedule_due(s, now, hist, udid, last_attempt_ts, now_ts):
    """The start time of the window we are in and still owe a backup for, or None.
    now is a naive local datetime. A backup (manual or scheduled) that finished since the window began counts as done.
    Attempts are at least 10 minutes apart; the window is the retry time (at least 5 minutes)."""
    if not s or not s.get("enabled") or not s.get("days"):
        return None
    h, m = (int(x) for x in s["time"].split(":"))
    today = now.replace(hour=h, minute=m, second=0, microsecond=0)
    window = datetime.timedelta(seconds=max(300, int(s["retryHours"]) * 3600))
    for st in (today, today - datetime.timedelta(days=1)):
        if st.weekday() not in s["days"] or not (st <= now <= st + window):
            continue
        done = any(x.get("udid") in (None, udid) and x.get("status") == "ok" and x.get("ts", 0) >= st.timestamp() for x in hist)
        if done or now_ts - last_attempt_ts < 600:
            return None
        return st
    return None


def scheduler():
    last_attempt = {}
    while True:
        try:
            with lock:
                d = dict(DEV)
                running = JOB is not None
                hist = list(HISTORY)
            udid = d["udid"]
            s = sched_for(udid) if udid else None
            if s and s["enabled"] and not running and d["paired"] and backup_dir(udid):
                st = schedule_due(s, datetime.datetime.now(), hist, udid, last_attempt.get(udid, 0), time.time())
                if st:
                    last_attempt[udid] = time.time()
                    log("scheduled backup starting (%s)" % st.strftime("%Y-%m-%d %H:%M"))
                    code, obj = start_backup(source="schedule")
                    if code != 200:
                        log("scheduled backup could not start: %s" % obj.get("error"))
        except Exception as e:
            log("scheduler error: %r" % e)
        time.sleep(30)


# ---------------------------------------------------------------- web password
def load_auth():
    global AUTH
    try:
        with open(AUTH_FILE) as f:
            AUTH = json.load(f)
    except Exception:
        AUTH = {}


def save_auth():
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = AUTH_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(AUTH, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH_FILE)


def hash_pw(pw, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt_hex), 200000).hex()


def make_token():
    payload = str(int(time.time()) + 30 * 86400)
    sig = hmac.new(bytes.fromhex(AUTH["secret"]), payload.encode(), "sha256").hexdigest()
    return payload + "." + sig


def check_token(tok):
    try:
        payload, sig = tok.split(".", 1)
        good = hmac.new(bytes.fromhex(AUTH["secret"]), payload.encode(), "sha256").hexdigest()
        return hmac.compare_digest(sig, good) and int(payload) > time.time()
    except Exception:
        return False


def is_authed(handler):
    if not AUTH.get("hash"):
        return False
    try:
        c = SimpleCookie(handler.headers.get("Cookie", ""))
        return check_token(c["ib_session"].value) if "ib_session" in c else False
    except Exception:
        return False


def cookie_header(value, max_age):
    return {"Set-Cookie": "ib_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (value, max_age)}


def auth_state(handler):
    ip = handler.client_address[0]
    if not AUTH.get("hash"):
        return {"state": "unset"}
    if is_authed(handler):
        return {"state": "ok"}
    n = FAILS.get(ip, {}).get("n", 0)
    return {"state": "locked", "fails": n, "hint": AUTH.get("hint") if n >= 2 and AUTH.get("hint") else None}


def auth_setup(handler, data):
    if AUTH.get("hash") and not is_authed(handler):
        return 403, {"error": "已经设置过密码"}, None
    pw = str(data.get("password") or "")
    if not pw or len(pw) > 200:
        return 400, {"error": "请输入密码"}, None
    salt = secrets.token_hex(16)
    AUTH.update(salt=salt, hash=hash_pw(pw, salt), hint=str(data.get("hint") or "")[:100], secret=secrets.token_hex(32))
    save_auth()
    FAILS.clear()
    log("web password set")
    return 200, {"ok": True}, cookie_header(make_token(), 30 * 86400)


def auth_login(handler, data):
    ip = handler.client_address[0]
    f = FAILS.setdefault(ip, {"n": 0, "until": 0})
    if f["until"] > time.time():
        return 429, {"error": "尝试次数太多，请一分钟后再试"}, None
    pw = str(data.get("password") or "")
    if AUTH.get("hash") and hmac.compare_digest(hash_pw(pw, AUTH["salt"]), AUTH["hash"]):
        FAILS.pop(ip, None)
        return 200, {"ok": True}, cookie_header(make_token(), 30 * 86400)
    f["n"] += 1
    if f["n"] % 10 == 0:
        f["until"] = time.time() + 60
    time.sleep(0.6)
    log("wrong web password from %s (%d)" % (ip, f["n"]))
    out = {"error": "密码不对", "fails": f["n"]}
    if f["n"] >= 2 and AUTH.get("hint"):
        out["hint"] = AUTH["hint"]
    return 401, out, None


# ---------------------------------------------------------------- environment self-check
def mounted_paths():
    out = set()
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                p = line.split()
                if len(p) > 4:
                    out.add(p[4].replace("\\040", " ").replace("\\011", "\t"))
    except OSError:
        pass
    return out


def run_health():
    """Problems that make the service unsafe or unable to work. Each item has zh/en text."""
    items = []

    def add(id_, level, zh, en, fix_zh="", fix_en=""):
        items.append({"id": id_, "level": level, "title": {"zh": zh, "en": en}, "fix": {"zh": fix_zh, "en": fix_en}})

    mounts = mounted_paths()
    if not ROOTS:
        add("roots", "error", "没有配置备份文件夹", "No backup folder is configured",
            "在 .env 里设置 BACKUP_LOCATION，然后重新运行 docker compose up -d。",
            "Set BACKUP_LOCATION in .env, then run docker compose up -d again.")
    for r in ROOTS.values():
        c, host = r["cpath"], r["host"]
        if c not in mounts:
            add("mount-" + r["name"], "error", "备份文件夹没有挂载到 NAS：" + host, "The backup folder is not mounted from the NAS: " + host,
                "现在备份会写进容器内部，容器一删就全丢。请在 compose 里加上一行 “- %s:%s” 并重新运行 docker compose up -d。" % (host, c),
                "Backups would be written inside the container and lost when it is removed. Add the line \"- %s:%s\" to the compose file and run docker compose up -d again." % (host, c))
            continue
        probe = os.path.join(c, ".write-test-%d" % os.getpid())
        try:
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
        except OSError as e:
            add("write-" + r["name"], "error", "备份文件夹不能写入：" + host, "The backup folder is not writable: " + host,
                "检查 NAS 上这个文件夹的权限（%s）。" % e.strerror, "Check the folder permissions on the NAS (%s)." % e.strerror)
    if "/config" not in mounts:
        add("config", "warn", "设置和访问密码没有保存到 NAS", "Settings and the web password are not stored on the NAS",
            "重建容器后密码和设置会丢失。请在 compose 里挂载 /config。", "They will be lost when the container is recreated. Mount /config in the compose file.")
    if "/var/lib/lockdown" not in mounts:
        add("lockdown", "warn", "手机的配对记录没有保存到 NAS", "Device pairing records are not stored on the NAS",
            "重建容器后需要重新配对。请在 compose 里挂载 /var/lib/lockdown。", "You would have to pair again after the container is recreated. Mount /var/lib/lockdown in the compose file.")
    if not os.path.isdir("/dev/bus/usb"):
        add("usb", "error", "容器里看不到 USB 设备", "The container cannot see USB devices",
            "compose 里需要 privileged: true，并挂载 /dev/bus/usb。", "The compose file needs privileged: true and the /dev/bus/usb mount.")
    rc, _ = run(["pgrep", "-x", "usbmuxd"], timeout=3)
    if rc != 0:
        add("usbmuxd", "error", "USB 多路复用服务（usbmuxd）没有运行", "The usbmuxd service is not running",
            "到“运行日志”里看原因，或者重启容器。", "Check the logs for the reason, or restart the container.")
    if not (os.path.exists("/run/avahi-daemon/socket") and os.path.exists("/run/dbus/system_bus_socket")):
        add("avahi", "warn", "找不到 NAS 上的 avahi，无线备份发现不了手机", "The NAS avahi service was not found, so the device cannot be discovered over Wi-Fi",
            "USB 备份不受影响。要用无线备份，请在 NAS 上安装并启动 avahi-daemon，并在 compose 里挂载 /run/dbus 和 /run/avahi-daemon。",
            "USB backups still work. For Wi-Fi backups install and start avahi-daemon on the NAS and mount /run/dbus and /run/avahi-daemon in the compose file.")
    return items


HEALTH = {"t": 0.0, "v": []}


def get_health():
    if time.time() - HEALTH["t"] > 20:
        try:
            HEALTH["v"] = run_health()
        except Exception as e:
            log("health check error: %r" % e)
        HEALTH["t"] = time.time()
    return HEALTH["v"]


# ---------------------------------------------------------------- status
def build_status():
    with lock:
        d = dict(DEV)
        job = JOB
        res = RESULT
        hist = [h for h in HISTORY if h.get("udid") in (None, d["udid"])][-50:]
        dirbytes = DIRSIZE.get(d["udid"])
        base = backup_dir(d["udid"])
    j = None
    if job:
        elapsed = time.time() - job["started"]
        j = {"running": True, "kind": job["kind"], "pct": job["pct"], "msg": job["msg"],
             "status": job["status"], "file": job["file"], "bytes": job["bytes"], "speed": job.get("speed"),
             "elapsed": int(elapsed), "eta": int(job["eta"]) if job["eta"] is not None else None,
             "way": job["way"], "cancelling": job["cancel"]}
    devices = []
    for u in dict.fromkeys(list(d["usbIds"]) + list(d["netIds"])):
        k = KNOWN.get(u, {})
        name, pt = (d["name"], d["ptype"]) if u == d["udid"] else (k.get("name"), k.get("ptype"))
        devices.append({"udid": u, "name": name or "", "ptype": pt or "", "conn": "usb" if u in d["usbIds"] else "wifi",
                        "selected": u == d["udid"]})
    vol = volume_info(base) if base else None
    storage = {"total": vol["total"] if vol else None, "used": vol["used"] if vol else None,
               "free": vol["free"] if vol else None, "dir": dirbytes,
               "label": vol["label"] if vol else None}
    _, root = browse("")
    udid = d["udid"]
    hostdir = to_host(base) if base else None
    return {
        "device": {"connected": bool(udid), "udid": udid, "conn": d["conn"], "paired": d["paired"],
                   "name": d["name"], "product": d["product"], "version": d["version"],
                   "capacity": d["capacity"], "free": d["free"], "encrypted": d["encrypted"],
                   "wifiSync": d["wifiSync"], "ptype": d["ptype"], "usbIds": d["usbIds"], "netIds": d["netIds"]},
        "config": {"configured": bool(base), "dir": hostdir, "suggest": root.get("suggest")},
        "devices": devices,
        "job": j,
        "deleting": DELETING is not None,
        "result": res,
        "last": read_last(udid),
        "storage": storage,
        "history": list(reversed(hist)),
        "paths": {"nas": (hostdir + ("/" + udid if udid else "")) if hostdir else None,
                  "windows": "%APPDATA%\\Apple Computer\\MobileSync\\Backup\\" + (udid or "<UDID>"),
                  "mac": "~/Library/Application Support/MobileSync/Backup/" + (udid or "<UDID>")},
        "schedule": sched_info(udid),
        "health": get_health(),
        "now": int(time.time()),
    }


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "ios-backup-station"

    def log_message(self, fmt, *args):
        pass

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def _json(self, code, obj, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._security_headers()
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._do_get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:           # odd paths (null bytes, bad encodings...) must not kill the handler thread silently
            log("error in GET %s: %r" % (self.path[:80], e))
            try:
                self._json(500, {"error": "内部错误：%s" % e.__class__.__name__})
            except Exception:
                pass

    def _do_get(self):
        u = urlsplit(self.path)
        if u.path == "/api/auth":
            return self._json(200, auth_state(self))
        if u.path.startswith("/api/") and not is_authed(self):
            return self._json(401, {"error": "unauthorized"})
        if u.path == "/api/status":
            return self._json(200, build_status())
        if u.path == "/api/log":
            return self._json(200, {"lines": list(LOG)[-200:]})
        if u.path == "/api/browse":
            code, obj = browse((parse_qs(u.query).get("path") or [""])[0])
            return self._json(code, obj)
        if u.path in ("/m", "/m.html"):
            return self._page("mobile.html")
        if u.path in ("/", "/index.html"):
            ui = (parse_qs(u.query).get("ui") or [""])[0]
            phone = bool(MOBILE_UA.search(self.headers.get("User-Agent", "")))
            return self._page("mobile.html" if (phone and ui != "desktop") or ui == "mobile" else "index.html")
        self._json(404, {"error": "not found"})

    def _page(self, name):
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                body = f.read()
        except OSError:
            return self._json(500, {"error": name + " missing"})
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json(400, {"error": "bad content-length"})
        if n < 0:
            return self._json(400, {"error": "bad content-length"})
        if n > 4096:
            return self._json(413, {"error": "too large"})
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})
        if not isinstance(data, dict):
            return self._json(400, {"error": "expected a JSON object"})
        path = self.path.split("?", 1)[0]
        if path == "/api/auth/setup":
            code, obj, hdr = auth_setup(self, data)
            return self._json(code, obj, hdr)
        if path == "/api/auth/login":
            code, obj, hdr = auth_login(self, data)
            return self._json(code, obj, hdr)
        if path == "/api/auth/logout":
            return self._json(200, {"ok": True}, cookie_header("", 0))
        if not is_authed(self):
            return self._json(401, {"error": "unauthorized"})
        routes = {
            "/api/backup": lambda: start_backup(),
            "/api/cancel": lambda: cancel_job(),
            "/api/select": lambda: select_device(str(data.get("udid") or "")),
            "/api/delete-backup": lambda: delete_backup(str(data.get("confirm") or "")),
            "/api/encryption": lambda: set_encryption(bool(data.get("enable")), str(data.get("password") or "")),
            "/api/pair": lambda: do_pair(),
            "/api/wifi-sync": lambda: do_wifi_sync(bool(data.get("enable", True))),
            "/api/probe": lambda: do_probe(),
            "/api/schedule": lambda: set_schedule(DEV["udid"], data),
            "/api/config": lambda: set_backup_dir(DEV["udid"], str(data.get("path") or "")),
            "/api/mkdir": lambda: make_folder(str(data.get("parent") or ""), str(data.get("name") or "")),
        }
        fn = routes.get(path)
        if not fn:
            return self._json(404, {"error": "not found"})
        try:
            code, obj = fn()
        except Exception as e:           # a bad request must never kill the handler thread silently
            log("error in %s: %r" % (path, e))
            return self._json(500, {"error": "内部错误：%s" % e.__class__.__name__})
        self._json(code, obj)


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    load_history()
    load_config()
    load_auth()
    log("web UI on port %d, %d device folder(s) configured" % (PORT, len(CONF["dirs"])))
    threading.Thread(target=sweep_trash, daemon=True).start()
    threading.Thread(target=mdns_watch, daemon=True).start()
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()

    def prime():  # measure the existing backup size once the first device shows up
        for _ in range(30):
            time.sleep(4)
            with lock:
                u = DEV["udid"]
            if u:
                refresh_dirsize(u)
                return
    threading.Thread(target=prime, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
