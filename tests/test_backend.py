#!/usr/bin/env python3
"""API-level tests. They start the real server against a fake phone (see tests/fakephone), so no device is needed.

    python3 -I tests/test_backend.py            # about 5 minutes (one test waits for the scheduler to fire)
    python3 -I tests/test_backend.py -k Auth    # only the matching tests
"""
import datetime, hmac, hashlib, json, os, socket, sys, threading, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Api, SECOND_UDID, Server, UDID, wait_for  # noqa: E402

FRESH_PHONE = dict(usb=False, net=True, blind=False, net_blind=False, paired=True, enc=True, backup_password="1234", wifi_sync=True,
                   locked_wait=0.0, backup_seconds=4.0, abort_delay=1.0, backup_fail=False, restart_fixes=True, usbmuxd_running=True)


def raw_request(port, payload):
    s = socket.create_connection(("127.0.0.1", port), 5); s.sendall(payload)
    s.settimeout(5); data = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk: break
            data += chunk
    except socket.timeout:
        pass
    s.close()
    return data


class AuthTests(unittest.TestCase):
    """Every test gets its own server: failed logins are counted per IP address and would leak between tests."""
    PASSWORD = "päss 密码 \U0001F512"

    def setUp(self):
        self.srv = Server().start(); self.api = Api(self.srv)

    def tearDown(self): self.srv.stop()

    def with_password(self):
        code, _, _ = self.api.post("/api/auth/setup", {"password": self.PASSWORD, "hint": "the hint"}); self.assertEqual(code, 200)

    def test_01_unset_state_and_locked_api(self):
        self.assertEqual(self.api.get("/api/auth")[1], {"state": "unset"})
        for path in ("/api/status", "/api/log", "/api/browse?path="):
            self.assertEqual(self.api.get(path)[0], 401, path)
        self.assertEqual(self.api.post("/api/backup")[0], 401)
        self.assertEqual(self.api.get("/")[0], 200)          # the page itself is public, the data is not

    def test_02_setup_validation(self):
        self.assertEqual(self.api.post("/api/auth/setup", {"password": ""})[0], 400)
        self.assertEqual(self.api.post("/api/auth/setup", {"password": "x" * 201})[0], 400)
        self.assertEqual(self.api.post("/api/auth/setup", {})[0], 400)
        code, j, r = self.api.post("/api/auth/setup", {"password": self.PASSWORD, "hint": "h" * 500})
        self.assertEqual(code, 200)
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie); self.assertIn("SameSite=Lax", cookie); self.assertIn("Max-Age=2592000", cookie)
        with open(os.path.join(self.srv.work, "config", "auth.json")) as f: a = json.load(f)
        self.assertLessEqual(len(a["hint"]), 100)
        self.assertNotIn("päss", json.dumps(a), "the password must not be stored in clear text")
        self.assertEqual(oct(os.stat(os.path.join(self.srv.work, "config", "auth.json")).st_mode & 0o777), "0o600")
        self.assertEqual(self.api.get("/api/status")[0], 200)

    def test_03_setup_cannot_be_hijacked(self):
        self.with_password()
        self.assertEqual(self.api.post("/api/auth/setup", {"password": "evil"}, cookie=False)[0], 403)
        self.assertEqual(self.api.post("/api/auth/login", {"password": "evil"}, cookie=False)[0], 401)

    def test_04_wrong_password_hint_and_reset(self):
        self.with_password(); other = Api(self.srv)
        c1, j1, _ = other.post("/api/auth/login", {"password": "nope"}); self.assertEqual((c1, j1.get("fails"), "hint" in j1), (401, 1, False))
        c2, j2, _ = other.post("/api/auth/login", {"password": "nope2"}); self.assertEqual((c2, j2.get("fails")), (401, 2)); self.assertIn("hint", j2)
        self.assertEqual(other.get("/api/auth")[1]["state"], "locked"); self.assertIn("hint", other.get("/api/auth")[1])
        c3, j3, _ = other.post("/api/auth/login", {"password": self.PASSWORD}); self.assertEqual(c3, 200)
        self.assertEqual(other.get("/api/auth")[1], {"state": "ok"})
        other.post("/api/auth/logout"); self.assertEqual(other.get("/api/auth")[1]["state"], "locked")
        self.assertIsNone(other.get("/api/auth")[1].get("hint"), "failure counter must be reset by a successful login")

    def test_05_forged_and_expired_cookies(self):
        self.with_password()
        for bad in ("ib_session=garbage", "ib_session=9999999999.deadbeef", "ib_session=", "ib_session=1.2.3", "x=y"):
            self.assertEqual(Api(self.srv).get("/api/status", headers={"Cookie": bad}, cookie=False)[0], 401, bad)
        with open(os.path.join(self.srv.work, "config", "auth.json")) as f: secret = bytes.fromhex(json.load(f)["secret"])
        def token(exp, key=secret):
            return "%d.%s" % (exp, hmac.new(key, str(exp).encode(), "sha256").hexdigest())
        ok = Api(self.srv).get("/api/status", headers={"Cookie": "ib_session=" + token(int(time.time()) + 100)}, cookie=False)[0]
        self.assertEqual(ok, 200, "a correctly signed token must work (sanity check of this test)")
        self.assertEqual(Api(self.srv).get("/api/status", headers={"Cookie": "ib_session=" + token(int(time.time()) - 5)}, cookie=False)[0], 401, "expired")
        self.assertEqual(Api(self.srv).get("/api/status", headers={"Cookie": "ib_session=" + token(int(time.time()) + 100, b"wrong key")}, cookie=False)[0], 401, "wrong key")

    def test_06_malformed_requests_never_crash_the_server(self):
        self.with_password(); port = self.srv.port
        for body in (b"{not json", b"[]", b'"str"', b"null", b"123", b"\xff\xfe"):
            r = raw_request(port, b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
            code = int(r.split(b" ", 2)[1]) if r else 0
            self.assertIn(code, (400, 401), "body %r -> %r" % (body, r[:60]))
        for cl in (b"abc", b"-5", b"1e3", b""):
            r = raw_request(port, b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nContent-Length: " + cl + b"\r\nConnection: close\r\n\r\n")
            code = int(r.split(b" ", 2)[1]) if r else 0
            self.assertIn(code, (400, 401), "Content-Length %r -> %r" % (cl, r[:60]))
        big = b'{"password":"' + b"a" * 5000 + b'"}'
        r = raw_request(port, b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(big) + big)
        self.assertEqual(int(r.split(b" ", 2)[1]), 413)
        r = raw_request(port, b"GET /%00%ff%fe HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"); self.assertTrue(r.startswith(b"HTTP/1."))
        r = raw_request(port, b"BREW /pot HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"); self.assertTrue(r.startswith(b"HTTP/1."))
        self.assertEqual(self.api.get("/api/auth")[0], 200, "server must still be alive")

    def test_07_routes_and_headers(self):
        self.with_password()
        self.assertEqual(self.api.get("/api/nope")[0], 404)
        self.assertEqual(self.api.post("/api/nope")[0], 404)
        self.assertEqual(self.api.get("/api/backup")[0], 404, "POST-only routes must not answer GET")
        self.assertEqual(self.api.get("/etc/passwd")[0], 404)
        self.assertEqual(self.api.get("/../../etc/passwd")[0], 404)
        for path in ("/", "/api/auth", "/m"):
            h = self.api.get(path)[2]
            self.assertEqual(h.getheader("X-Content-Type-Options"), "nosniff", path)
            self.assertEqual(h.getheader("X-Frame-Options"), "DENY", path)
            self.assertEqual(h.getheader("Cache-Control"), "no-store", path)

    def test_08_device_routing_by_user_agent(self):
        phone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
        mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15"
        droid = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
        is_mobile = lambda ua, path="/": b"apple-mobile-web-app-capable" in Api(self.srv, ua).get(path, cookie=False)[1]
        self.assertTrue(is_mobile(phone)); self.assertTrue(is_mobile(droid))
        self.assertFalse(is_mobile(mac)); self.assertFalse(is_mobile("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"))
        self.assertFalse(is_mobile(phone, "/?ui=desktop")); self.assertTrue(is_mobile(mac, "/?ui=mobile")); self.assertTrue(is_mobile(mac, "/m"))

    def test_99_lockout_after_ten_wrong_attempts(self):
        self.with_password(); a = Api(self.srv); codes = [a.post("/api/auth/login", {"password": "bad%d" % i})[0] for i in range(11)]
        self.assertEqual(codes[:10], [401] * 10); self.assertEqual(codes[10], 429)
        self.assertEqual(a.post("/api/auth/login", {"password": self.PASSWORD})[0], 429, "even the right password is refused while locked")


class FlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Server(keep=True).start(); cls.api = Api(cls.srv)
        assert cls.api.post("/api/auth/setup", {"password": "pw"})[0] == 200
        cls.srv.phone(**FRESH_PHONE)

    @classmethod
    def tearDownClass(cls): cls.srv.stop(); __import__("shutil").rmtree(cls.srv.work, ignore_errors=True)

    # ---- helpers
    def status(self): return self.api.get("/api/status")[1]

    def phone(self, **kw): self.srv.phone(**kw)

    def settle(self, conn="wifi", paired=True, timeout=20):
        wait_for(lambda: (lambda d: d["conn"] == conn and d["paired"] == paired)(self.status()["device"]), timeout, msg="device conn=%s paired=%s" % (conn, paired))

    def idle(self):
        if self.status()["job"]: self.api.post("/api/cancel")
        wait_for(lambda: self.status()["job"] is None, 30, msg="no running job")

    def setUp(self):
        self.idle(); self.phone(**FRESH_PHONE)
        wait_for(lambda: self.status()["device"]["conn"] == "wifi" and self.status()["device"]["paired"] is True, 25, msg="fresh phone")

    def folder(self):
        root = self.api.get("/api/browse?path=")[1]["volumes"][0]["path"]
        self.api.post("/api/mkdir", {"parent": root, "name": "iphone_backups"})
        code, j, _ = self.api.post("/api/config", {"path": root + "/iphone_backups"}); self.assertEqual(code, 200, j)
        return root + "/iphone_backups"

    def run_backup(self, timeout=40):
        code, j, _ = self.api.post("/api/backup"); self.assertEqual(code, 200, j)
        wait_for(lambda: self.status()["job"] is not None, 8, 0.1, msg="job to appear")
        wait_for(lambda: self.status()["job"] is None, timeout, msg="job to finish")
        return self.status()

    # ---- tests
    def test_01_status_shape_and_device_info(self):
        s = self.status(); d = s["device"]
        for k in ("device", "config", "job", "result", "last", "storage", "history", "paths", "schedule", "health", "now"): self.assertIn(k, s)
        self.assertEqual((d["name"], d["product"], d["ptype"], d["version"], d["encrypted"], d["wifiSync"], d["connected"], d["conn"]),
                         ("Air", "iPhone Air", "iPhone18,4", "27.0.1", True, True, True, "wifi"))
        self.assertEqual(d["udid"], UDID); self.assertEqual(s["schedule"]["time"], "20:00"); self.assertFalse(s["schedule"]["enabled"])
        self.assertIn("mac", s["paths"]); self.assertIn("windows", s["paths"])
        self.assertTrue(any(h["id"].startswith("mount-") for h in s["health"]), "the fake NAS folder is not a mount point: the check must say so")
        for h in s["health"]: self.assertTrue(h["title"]["zh"] and h["title"]["en"] and h["fix"]["zh"] and h["fix"]["en"])

    def test_02_preconditions_before_a_folder_is_chosen(self):
        code, j, _ = self.api.post("/api/backup"); self.assertEqual(code, 409); self.assertIn("备份保存位置", j["error"])
        self.assertEqual(self.api.post("/api/encryption", {"enable": True, "password": "x"})[0], 409)
        self.assertFalse(self.status()["config"]["configured"])

    def test_03_folder_api_security_and_validation(self):
        code, j, _ = self.api.get("/api/browse?path="); self.assertEqual(code, 200); vol = j["volumes"][0]["path"]
        self.assertEqual(j["volumes"][0]["host"], "/vol1/1000"); self.assertIsNotNone(j["suggest"])
        self.assertEqual(sorted(self.api.get("/api/browse?path=" + vol)[1]["dirs"]), ["Drive", "Scores", "photos"])
        for bad in ("/etc", "/", "/proc", vol + "/../../..", vol + "/../", "relative", "/nonexistent", vol + "/Drive/../../../etc", "%00"):
            self.assertEqual(self.api.get("/api/browse?path=" + bad)[0], 400, bad)
        outside = os.path.join(self.srv.work, "outside"); os.makedirs(outside)
        os.symlink(outside, vol + "/escape")
        self.assertEqual(self.api.post("/api/config", {"path": vol + "/escape"})[0], 400, "a symlink out of the shared folders must be refused")
        self.assertEqual(self.api.post("/api/mkdir", {"parent": vol + "/escape", "name": "x"})[0], 400)
        os.remove(vol + "/escape")
        for name in ("", " ", ".", "..", ".hidden", "@sys", "a/b", "a\\b", "x" * 65, "\x00", "ok\x00bad"):
            code, j, _ = self.api.post("/api/mkdir", {"parent": vol, "name": name}); self.assertEqual(code, 400, repr(name)); self.assertIn("error", j)
        self.assertEqual(self.api.post("/api/mkdir", {"parent": vol, "name": "备份 \U0001F4F1 ok"})[0], 200)
        self.assertEqual(self.api.post("/api/mkdir", {"parent": vol, "name": "备份 \U0001F4F1 ok"})[0], 200, "creating an existing folder is not an error")
        self.assertEqual(self.api.post("/api/mkdir", {"parent": "/etc", "name": "x"})[0], 400)
        if os.geteuid() != 0:
            ro = os.path.join(vol, "readonly"); os.makedirs(ro); os.chmod(ro, 0o555)
            code, j, _ = self.api.post("/api/config", {"path": ro}); self.assertEqual(code, 400, "read-only folders must be refused"); os.chmod(ro, 0o755)
        self.assertEqual(self.api.post("/api/config", {"path": ""})[0], 400)
        self.assertEqual(self.api.post("/api/config", {})[0], 400)
        self.folder(); s = self.status(); self.assertTrue(s["config"]["configured"]); self.assertEqual(s["config"]["dir"], "/vol1/1000/iphone_backups")

    def test_04_backup_preconditions(self):
        self.folder()
        self.phone(net=False, usb=False); self.settle(conn=None, paired=None)
        code, j, _ = self.api.post("/api/backup"); self.assertEqual(code, 409); self.assertIn("未检测", j["error"])
        self.phone(net=True, paired=False); self.settle(conn="wifi", paired=False)
        code, j, _ = self.api.post("/api/backup"); self.assertEqual(code, 409); self.assertIn("配对", j["error"])
        self.assertEqual(self.api.post("/api/cancel")[0], 409, "nothing to cancel")

    def test_05_full_backup_over_wifi(self):
        self.folder(); self.phone(backup_seconds=6.0)
        self.assertEqual(self.api.post("/api/backup")[0], 200)
        wait_for(lambda: self.status()["job"], 8, 0.1, msg="job")
        pcts = []; seen = set()
        end = time.time() + 40
        while time.time() < end:
            j = self.status()["job"]
            if j is None: break
            pcts.append(j["pct"]); seen.add(j["way"]); self.assertTrue(j["running"]); time.sleep(0.3)
        self.assertEqual(seen, {"wifi"}); self.assertGreater(len(pcts), 5)
        self.assertEqual(pcts, sorted(pcts), "the progress must never go backwards (the per-file bar must be ignored)")
        self.assertGreater(max(pcts), 50)
        s = wait_for(lambda: (lambda s: s if s["history"] and s["history"][0]["size"] is not None else None)(self.status()), 20, msg="size")
        h = s["history"][0]
        self.assertEqual((h["status"], h["source"] if "source" in h else "manual", h["way"], h["encrypted"]), ("ok", "manual", "wifi", True))
        self.assertGreater(h["size"], 0); self.assertEqual(s["last"]["state"], "finished"); self.assertTrue(s["last"]["encrypted"])
        self.assertGreater(s["storage"]["dir"], 0)
        self.assertIn("-n", self.srv.phone_state()["last_argv"], "no cable: it must have used the network")
        self.assertEqual(s["result"]["status"], "ok")

    def test_06_usb_is_preferred(self):
        self.folder(); self.phone(usb=True, net=True); self.settle(conn="usb")
        s = self.run_backup(); self.assertEqual(s["history"][0]["way"], "usb"); self.assertNotIn("-n", self.srv.phone_state()["last_argv"])

    def test_07_cable_plugged_in_a_moment_ago(self):
        """The poller can be seconds behind. Starting right after plugging in must still use USB."""
        self.folder(); self.phone(usb=False, net=True); self.settle(conn="wifi")
        self.phone(usb=True)
        code, _, _ = self.api.post("/api/backup"); self.assertEqual(code, 200)
        wait_for(lambda: self.status()["job"] is None, 40, msg="finish")
        self.assertNotIn("-n", self.srv.phone_state()["last_argv"]); self.assertEqual(self.status()["history"][0]["way"], "usb")

    def test_08_cancel_then_backup_again(self):
        self.folder(); self.phone(backup_seconds=60.0, abort_delay=1.5)
        self.assertEqual(self.api.post("/api/backup")[0], 200); wait_for(lambda: self.status()["job"], 8, 0.1)
        time.sleep(1.2)
        self.assertEqual(self.api.post("/api/cancel")[0], 200)
        self.assertTrue(self.status()["job"]["cancelling"])
        wait_for(lambda: self.status()["job"] is None, 20, msg="cancel to finish")
        s = self.status(); self.assertEqual(s["history"][0]["status"], "cancelled"); self.assertEqual(s["result"]["status"], "cancelled")
        time.sleep(1.5); self.assertIsNone(self.status()["history"][0]["size"], "a cancelled run must not show the size of the previous backup")
        self.assertEqual(self.api.post("/api/cancel")[0], 409)
        self.phone(backup_seconds=2.0); s = self.run_backup(); self.assertEqual(s["history"][0]["status"], "ok", "a backup right after a cancel must work")

    def test_08b_written_counts_this_run_only(self):
        """A second backup overwrites the files of the first. 'Written' must start near zero, not at the folder size."""
        self.folder(); self.phone(backup_seconds=9.0); self.run_backup(60)
        before = wait_for(lambda: self.status()["storage"]["dir"], 15, msg="folder size")
        self.assertGreater(before, 400_000)
        self.phone(backup_seconds=20.0)
        self.assertEqual(self.api.post("/api/backup")[0], 200); wait_for(lambda: self.status()["job"], 8, 0.1)
        time.sleep(2.0)
        j = wait_for(lambda: (lambda j: j if j and j["bytes"] is not None else None)(self.status()["job"]), 10, 0.3, msg="job.bytes")
        self.assertLess(j["bytes"], before * 0.5, "written %d bytes after 2 s although the folder already holds %d bytes" % (j["bytes"], before))
        b1 = j["bytes"]; time.sleep(4); b2 = self.status()["job"]["bytes"]
        self.assertGreater(b2, b1, "the counter must grow while the tool writes")
        self.assertLess(abs(self.status()["storage"]["dir"] - before), 50_000, "the folder size stays put while old files are overwritten (that is the point of this test)")
        self.api.post("/api/cancel"); wait_for(lambda: self.status()["job"] is None, 20)

    def test_08c_speed_is_reported(self):
        self.folder(); self.phone(backup_seconds=30.0)       # the fake tool writes about 80 kB per second
        self.assertEqual(self.api.post("/api/backup")[0], 200); wait_for(lambda: self.status()["job"], 8, 0.1)
        self.assertIsNone(self.status()["job"]["speed"], "no speed before there is enough to measure")
        j = wait_for(lambda: (lambda j: j if j and j["speed"] else None)(self.status()["job"]), 20, 0.5, msg="a speed")
        self.assertGreater(j["speed"], 20_000); self.assertLess(j["speed"], 400_000, "implausible speed %r" % j["speed"])
        self.api.post("/api/cancel"); wait_for(lambda: self.status()["job"] is None, 20)
        self.assertIsNone(self.status()["job"], "")

    def test_09_only_one_backup_at_a_time(self):
        path = self.folder(); self.phone(backup_seconds=30.0)
        codes = []; ts = [threading.Thread(target=lambda: codes.append(self.api.post("/api/backup")[0])) for _ in range(6)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sorted(codes), [200, 409, 409, 409, 409, 409]); self.assertEqual(self.api.post("/api/backup")[0], 409)
        self.assertEqual(self.api.post("/api/config", {"path": path})[0], 409, "the folder cannot change during a backup")
        self.assertEqual(self.api.post("/api/encryption", {"enable": False, "password": "1234"})[0], 409)

    def test_10_failed_backup_is_recorded_and_the_lock_is_released(self):
        self.folder(); self.phone(backup_fail=True, backup_seconds=20.0); s = self.run_backup(20)
        h = s["history"][0]; self.assertEqual(h["status"], "failed"); self.assertIn("disconnected", h["error"]); self.assertEqual(s["result"]["status"], "failed")
        time.sleep(1.5); self.assertIsNone(self.status()["history"][0]["size"], "a failed run must not show the size of the previous backup")
        self.phone(backup_fail=False, backup_seconds=2.0); self.assertEqual(self.run_backup()["history"][0]["status"], "ok")

    def test_11_phone_waiting_for_its_passcode(self):
        self.folder(); self.phone(locked_wait=3.0, backup_seconds=2.0)
        self.assertEqual(self.api.post("/api/backup")[0], 200); wait_for(lambda: self.status()["job"], 8, 0.1)
        j = wait_for(lambda: (lambda j: j if j and "passcode" in (j["status"] or "").lower() else None)(self.status()["job"]), 6, 0.2, msg="passcode status")
        self.assertEqual(j["pct"], 0)
        wait_for(lambda: self.status()["job"] is None, 40); self.assertEqual(self.status()["history"][0]["status"], "ok")

    def test_12_encryption_endpoint(self):
        self.assertEqual(self.api.post("/api/encryption", {"enable": True, "password": ""})[0], 400) if self.status()["config"]["configured"] else None
        self.folder()
        self.assertEqual(self.api.post("/api/encryption", {"enable": True, "password": ""})[0], 400)
        self.assertTrue(self.api.post("/api/encryption", {"enable": False, "password": "1234"})[1]["ok"]); self.assertFalse(self.status()["device"]["encrypted"])
        self.assertTrue(self.api.post("/api/encryption", {"enable": True, "password": "S3cretPW"})[1]["ok"]); self.assertTrue(self.status()["device"]["encrypted"])
        self.assertEqual(self.srv.phone_state()["backup_password"], "S3cretPW", "the phone must end up with exactly the password that was typed")
        code, j, _ = self.api.post("/api/encryption", {"enable": False, "password": "wrong"}); self.assertEqual(code, 200); self.assertFalse(j["ok"]); self.assertTrue(j["error"])
        self.assertTrue(self.status()["device"]["encrypted"], "a wrong password must not turn encryption off")
        self.assertTrue(self.api.post("/api/encryption", {"enable": False, "password": "S3cretPW"})[1]["ok"]); self.assertFalse(self.status()["device"]["encrypted"])
        self.phone(paired=False); self.settle(paired=False, timeout=45)      # the poller refreshes every 30 s while paired
        self.assertEqual(self.api.post("/api/encryption", {"enable": True, "password": "x"})[0], 409)

    def test_13_pairing_and_wifi_sync(self):
        self.phone(usb=False, net=True); self.settle(conn="wifi")
        self.assertEqual(self.api.post("/api/pair")[0], 409, "pairing needs USB"); self.assertEqual(self.api.post("/api/wifi-sync", {"enable": True})[0], 409)
        self.phone(usb=True, net=False, paired=False, wifi_sync=False); self.settle(conn="usb", paired=False)
        self.assertEqual(self.api.post("/api/wifi-sync", {"enable": True})[0], 409, "needs a paired device")
        code, j, _ = self.api.post("/api/pair"); self.assertEqual((code, j["ok"]), (200, True)); self.assertTrue(self.status()["device"]["paired"])
        code, j, _ = self.api.post("/api/wifi-sync", {"enable": True}); self.assertEqual((code, j["ok"]), (200, True)); self.assertTrue(self.status()["device"]["wifiSync"])

    def test_14_schedule_validation(self):
        ok = {"enabled": True, "time": "21:30", "days": [0, 2, 4], "retryHours": 6}
        for bad in ({**ok, "time": "25:00"}, {**ok, "time": "9:30"}, {**ok, "time": ""}, {**ok, "time": "12:60"}, {**ok, "days": []}, {**ok, "days": [9, -1, "x", 1.5]}):
            self.assertEqual(self.api.post("/api/schedule", bad)[0], 400, bad)
        self.assertEqual(self.api.post("/api/schedule", {**ok, "retryHours": 2})[0], 200); self.assertEqual(self.status()["schedule"]["retryHours"], 3, "an invalid retry time falls back to 3")
        self.assertEqual(self.api.post("/api/schedule", {**ok, "retryHours": "x"})[0], 200)
        self.assertEqual(self.api.post("/api/schedule", ok)[0], 200)
        sc = self.status()["schedule"]; self.assertEqual((sc["enabled"], sc["time"], sc["days"], sc["retryHours"]), (True, "21:30", [0, 2, 4], 6)); self.assertTrue(sc["nextStr"])
        nxt = datetime.datetime.strptime(sc["nextStr"], "%Y-%m-%d %H:%M"); self.assertEqual((nxt.hour, nxt.minute), (21, 30)); self.assertIn(nxt.weekday(), (0, 2, 4)); self.assertGreater(nxt, datetime.datetime.now())
        self.assertEqual(self.api.post("/api/schedule", {**ok, "enabled": False, "days": []})[0], 200); self.assertIsNone(self.status()["schedule"]["nextStr"])

    def test_15_scheduler_really_starts_a_backup(self):
        self.folder(); self.phone(backup_seconds=2.0)
        now = datetime.datetime.now(); start = (now + datetime.timedelta(minutes=1 if now.second < 35 else 2)).replace(second=0, microsecond=0)
        self.assertEqual(self.api.post("/api/schedule", {"enabled": True, "time": start.strftime("%H:%M"), "days": list(range(7)), "retryHours": 1})[0], 200)
        def fired():
            s = self.status(); return s if any(h.get("source") == "schedule" for h in s["history"]) else None
        s = wait_for(fired, (start - now).total_seconds() + 75, 1.0, msg="the scheduled backup")
        h = [h for h in s["history"] if h.get("source") == "schedule"][0]; self.assertEqual(h["status"], "ok")
        time.sleep(35)
        self.assertEqual(len([h for h in self.status()["history"] if h.get("source") == "schedule"]), 1, "a window that is done must not run again")
        self.api.post("/api/schedule", {"enabled": False, "time": "20:00", "days": list(range(7)), "retryHours": 3})

    def test_16_usbmuxd_blind_to_a_plugged_in_phone(self):
        self.phone(usb=True, net=False, blind=True); r0 = self.srv.phone_state()["restarts"]
        t0 = time.time(); self.settle(conn="usb", timeout=30); dt = time.time() - t0
        self.assertGreater(self.srv.phone_state()["restarts"], r0, "the watchdog must have restarted usbmuxd2"); self.assertLess(dt, 16, "took %.1fs" % dt)
        print("   (USB blind -> recovered in %.1f s)" % dt)

    def test_17_cable_pulled_falls_back_to_wifi_quickly(self):
        self.phone(usb=True, net=True); self.settle(conn="usb"); r0 = self.srv.phone_state()["restarts"]
        self.phone(usb=False, net_blind=True)             # usbmuxd forgot the network entry, the phone is still on the LAN
        t0 = time.time(); self.settle(conn="wifi", timeout=30); dt = time.time() - t0
        self.assertGreater(self.srv.phone_state()["restarts"], r0); self.assertLess(dt, 14, "took %.1fs" % dt)
        print("   (cable pulled -> Wi-Fi again in %.1f s)" % dt)

    def test_18_restarts_back_off_when_they_do_not_help(self):
        self.phone(usb=False, net=True, net_blind=True, restart_fixes=False); r0 = self.srv.phone_state()["restarts"]
        time.sleep(26); n = self.srv.phone_state()["restarts"] - r0
        self.assertLessEqual(n, 2, "restarted %d times in 26 s even though it does not help" % n); self.assertGreaterEqual(n, 1)
        self.phone(net_blind=False, restart_fixes=True)

    def test_19_no_secrets_in_logs_or_files(self):
        self.api.post("/api/encryption", {"enable": True, "password": "Sup3rSecretBackupPW"})
        blob = json.dumps(self.api.get("/api/log")[1]) + self.srv.log()
        for root, _, files in os.walk(os.path.join(self.srv.work, "config")):
            for f in files:
                with open(os.path.join(root, f), errors="replace") as fh: blob += fh.read()
        self.assertNotIn("Sup3rSecretBackupPW", blob); self.assertNotIn("pw", [l for l in [json.dumps(json.load(open(os.path.join(self.srv.work, "config", "auth.json"))))] if '"pw"' in l])

    def test_20_many_requests(self):
        t0 = time.time(); [self.status() for _ in range(40)]; self.assertLess(time.time() - t0, 10)
        res = []; ts = [threading.Thread(target=lambda: [res.append(self.api.get("/api/status")[0]) for _ in range(5)]) for _ in range(20)]
        [t.start() for t in ts]; [t.join() for t in ts]; self.assertEqual(res, [200] * 100)

    def test_21_delete_backup(self):
        d = self.folder(); target = os.path.join(d, UDID)
        if os.path.isdir(target):          # earlier tests share this server and already left a backup behind
            self.assertEqual(self.api.post("/api/delete-backup", {"confirm": UDID})[0], 200)
            wait_for(lambda: not self.status()["deleting"] and not os.path.exists(target), 20, msg="the leftover backup to go")
        self.assertEqual(self.api.post("/api/delete-backup", {"confirm": UDID})[0], 404, "nothing to delete yet")
        self.run_backup(); self.assertTrue(os.path.isdir(target))
        self.assertGreater(len(self.status()["history"]), 0)
        self.assertEqual(self.api.post("/api/delete-backup", {})[0], 400, "no confirmation")
        self.assertEqual(self.api.post("/api/delete-backup", {"confirm": "wrong"})[0], 400, "wrong confirmation")
        self.assertTrue(os.path.isdir(target), "a refused request must not delete anything")
        other = os.path.join(d, "keep-me"); os.makedirs(other); open(os.path.join(other, "f"), "w").close()
        # while a backup runs, deletion is refused
        self.phone(backup_seconds=12.0)
        self.assertEqual(self.api.post("/api/backup")[0], 200)
        wait_for(lambda: self.status()["job"] is not None, 8, 0.1, msg="job")
        code, j, _ = self.api.post("/api/delete-backup", {"confirm": UDID}); self.assertEqual(code, 409, j)
        self.assertTrue(os.path.isdir(target)); self.idle(); self.phone(backup_seconds=4.0)
        # the real thing
        code, j, _ = self.api.post("/api/delete-backup", {"confirm": UDID}); self.assertEqual((code, j.get("ok")), (200, True), j)
        wait_for(lambda: not self.status()["deleting"], 20, msg="deletion to finish")
        self.assertFalse(os.path.exists(target)); self.assertEqual([n for n in os.listdir(d) if "deleting" in n], [])
        self.assertTrue(os.path.exists(os.path.join(other, "f")), "other folders must be left alone")
        s = self.status(); self.assertEqual(s["history"], []); self.assertIsNone(s["job"])
        self.assertEqual(self.api.post("/api/delete-backup", {"confirm": UDID})[0], 404)
        # a folder that does not look like a backup is refused
        os.makedirs(target); open(os.path.join(target, "notes.txt"), "w").close()
        self.assertEqual(self.api.post("/api/delete-backup", {"confirm": UDID})[0], 409); self.assertTrue(os.path.exists(os.path.join(target, "notes.txt")))
        # a symlink in its place is never followed
        os.remove(os.path.join(target, "notes.txt")); os.rmdir(target); os.symlink(other, target)
        self.assertEqual(self.api.post("/api/delete-backup", {"confirm": UDID})[0], 404); self.assertTrue(os.path.exists(os.path.join(other, "f")))
        os.remove(target); __import__("shutil").rmtree(other)
        self.run_backup(); self.assertTrue(os.path.isdir(target), "a new backup works after a deletion")

    def test_22_switch_between_two_devices(self):
        self.folder()
        self.assertEqual(len(self.status()["devices"]), 1, "one device: nothing to switch")
        self.phone(second={"conn": "usb", "name": "Sam’s iPad", "ptype": "iPad13,1"})      # the iPad is on USB, the iPhone on Wi-Fi
        wait_for(lambda: len(self.status()["devices"]) == 2, 20, msg="both devices listed")
        wait_for(lambda: self.status()["device"]["udid"] == SECOND_UDID, 20, msg="USB device is selected by default")
        wait_for(lambda: {d["udid"]: d["name"] for d in self.status()["devices"]}.get(UDID) == "Air", 90, msg="the name of the other device")
        names = {d["udid"]: (d["name"], d["conn"], d["selected"]) for d in self.status()["devices"]}
        self.assertEqual(names[SECOND_UDID], ("Sam’s iPad", "usb", True)); self.assertEqual(names[UDID], ("Air", "wifi", False))
        self.assertEqual(self.api.post("/api/select", {"udid": "00000000-0000000000000000"})[0], 404, "an unknown device is refused")
        self.assertEqual(self.api.post("/api/select", {})[0], 404)
        code, j, _ = self.api.post("/api/select", {"udid": UDID}); self.assertEqual((code, j.get("ok")), (200, True), j)
        wait_for(lambda: self.status()["device"]["udid"] == UDID and self.status()["device"]["name"] == "Air", 20, msg="the page follows the choice")
        self.assertEqual(self.status()["device"]["conn"], "wifi")
        self.assertEqual([d["selected"] for d in self.status()["devices"] if d["udid"] == UDID], [True])
        # a task blocks switching
        self.phone(backup_seconds=15.0); self.assertEqual(self.api.post("/api/backup")[0], 200)
        wait_for(lambda: self.status()["job"] is not None, 8, 0.1, msg="job")
        code, j, _ = self.api.post("/api/select", {"udid": SECOND_UDID}); self.assertEqual(code, 409, j)
        self.assertEqual(self.status()["device"]["udid"], UDID); self.idle(); self.phone(backup_seconds=4.0)
        # the choice survives a restart, and a vanished device falls back to the one that is left
        self.srv.proc.terminate(); self.srv.proc.wait(5); self.srv.start()
        wait_for(lambda: self.api.get("/api/status")[0] == 200 and self.status()["device"]["udid"] == UDID, 25, msg="the choice after a restart")
        self.phone(second=None)
        wait_for(lambda: len(self.status()["devices"]) == 1, 20, msg="the iPad is unplugged")
        self.assertEqual(self.status()["device"]["udid"], UDID)
        self.phone(second={"conn": "usb", "name": "Sam’s iPad", "ptype": "iPad13,1"}, net=False)
        wait_for(lambda: self.status()["device"]["udid"] == SECOND_UDID, 20, msg="fall back to the device that is left")
        self.phone(second=None, net=True)

    def test_99_state_survives_a_restart(self):
        self.folder()
        while len(self.status()["history"]) <= 3: self.run_backup()      # earlier tests may have emptied the history
        s0 = self.status(); n_hist = len(s0["history"])
        self.api.post("/api/schedule", {"enabled": True, "time": "03:15", "days": [1], "retryHours": 1})
        self.srv.proc.terminate(); self.srv.proc.wait(5); self.srv.start()
        code, j, _ = self.api.get("/api/status"); self.assertEqual(code, 200, "the login cookie must survive a restart")
        wait_for(lambda: self.status()["device"]["connected"], 20)
        s1 = self.status()
        self.assertEqual(len(s1["history"]), n_hist); self.assertTrue(s1["config"]["configured"]); self.assertEqual(s1["config"]["dir"], s0["config"]["dir"])
        self.assertEqual((s1["schedule"]["time"], s1["schedule"]["days"]), ("03:15", [1]))
        self.assertEqual(Api(self.srv).post("/api/auth/login", {"password": "pw"})[0], 200, "the password must survive a restart")
        self.assertIsNone(s1["job"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
