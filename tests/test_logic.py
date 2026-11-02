#!/usr/bin/env python3
"""Fast tests of the pure logic (no server, no browser): schedule, watchdogs, progress parser, self-check.

    python3 -I tests/test_logic.py
"""
import collections, datetime as dt, importlib.util, os, plistlib, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("server", os.path.join(HERE, "..", "app", "server.py"))
server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)

U = "00008999-0000000000AB001E"
ts = lambda d: d.timestamp()
d = dt.datetime


class ScheduleDue(unittest.TestCase):
    S = {"enabled": True, "time": "20:00", "days": list(range(7)), "retryHours": 3}

    def due(self, now, hist=(), last=0, **over):
        return server.schedule_due(dict(self.S, **over), now, list(hist), U, last, ts(now))

    def test_window(self):
        self.assertIsNone(self.due(d(2026, 10, 28, 19, 59)), "before the start time")
        self.assertEqual(self.due(d(2026, 10, 28, 20, 0)), d(2026, 10, 28, 20, 0))
        self.assertEqual(self.due(d(2026, 10, 28, 22, 30)), d(2026, 10, 28, 20, 0), "inside the retry window")
        self.assertIsNone(self.due(d(2026, 10, 28, 23, 1)), "after the window")

    def test_window_across_midnight(self):
        self.assertEqual(self.due(d(2026, 10, 29, 1, 30), time="23:00"), d(2026, 10, 28, 23, 0))

    def test_done_detection(self):
        ok = lambda t, u=U, st="ok": {"udid": u, "status": st, "ts": ts(t)}
        self.assertIsNone(self.due(d(2026, 10, 28, 21, 0), [ok(d(2026, 10, 28, 20, 5))]), "finished in this window")
        self.assertEqual(self.due(d(2026, 10, 28, 21, 0), [ok(d(2026, 10, 28, 19, 0))]), d(2026, 10, 28, 20, 0), "a backup before the window does not count")
        self.assertEqual(self.due(d(2026, 10, 28, 21, 0), [ok(d(2026, 10, 28, 20, 5), st="failed")]), d(2026, 10, 28, 20, 0), "a failed run does not count")
        self.assertEqual(self.due(d(2026, 10, 28, 21, 0), [ok(d(2026, 10, 28, 20, 5), u="other")]), d(2026, 10, 28, 20, 0), "another device does not count")

    def test_retry_spacing(self):
        self.assertIsNone(self.due(d(2026, 10, 28, 20, 5), last=ts(d(2026, 10, 28, 20, 0))), "retry only after 10 minutes")
        self.assertEqual(self.due(d(2026, 10, 28, 20, 11), last=ts(d(2026, 10, 28, 20, 0))), d(2026, 10, 28, 20, 0))

    def test_disabled_and_days(self):
        self.assertIsNone(self.due(d(2026, 10, 28, 20, 1), enabled=False))
        self.assertIsNone(self.due(d(2026, 10, 28, 20, 1), days=[]))
        self.assertIsNone(self.due(d(2026, 10, 28, 20, 1), days=[0, 1]), "2026-10-28 is a Wednesday")
        self.assertEqual(self.due(d(2026, 10, 28, 20, 1), days=[2]), d(2026, 10, 28, 20, 0))
        self.assertEqual(self.due(d(2026, 10, 29, 1, 0), time="23:00", days=[2]), d(2026, 10, 28, 23, 0), "yesterday's window needs yesterday's weekday")
        self.assertIsNone(self.due(d(2026, 10, 29, 1, 0), time="23:00", days=[3]))

    def test_no_retry_means_a_five_minute_grace(self):
        self.assertEqual(self.due(d(2026, 10, 28, 20, 4), retryHours=0), d(2026, 10, 28, 20, 0))
        self.assertIsNone(self.due(d(2026, 10, 28, 20, 6), retryHours=0))


class Watchdogs(unittest.TestCase):
    IP = ["192.0.2.24"]

    def test_wifi_watchdog(self):
        w = server.watchdog_step
        self.assertEqual(w(None, 100.0, [], 0, None), (False, None), "phone not on the LAN")
        self.assertEqual(w(None, 100.0, self.IP, 0, None), (False, 100.0), "first sighting only starts the clock")
        self.assertFalse(w(100.0, 105.0, self.IP, 0, None)[0], "5 s: still waiting")
        self.assertTrue(w(100.0, 109.0, self.IP, 0, None)[0], "9 s: restart")
        self.assertTrue(w(100.0, 101.5, self.IP, 0, 98.0)[0], "cable just pulled: 1 s is enough")
        self.assertFalse(w(100.0, 100.5, self.IP, 0, 98.0)[0])
        self.assertFalse(w(100.0, 101.5, self.IP, 98.5, 98.0)[0], "cooldown after a restart")
        self.assertFalse(w(100.0, 103.0, self.IP, 0, 40.0)[0], "pulled long ago: normal wait")

    def test_usb_watchdog(self):
        w = server.usb_watchdog_step
        self.assertEqual(w(None, 100.0, False, 0), (False, None))
        self.assertEqual(w(None, 100.0, True, 0), (False, 100.0))
        self.assertFalse(w(100.0, 102.0, True, 0)[0]); self.assertTrue(w(100.0, 104.0, True, 0)[0])
        self.assertFalse(w(100.0, 104.0, True, 99.0)[0], "cooldown")
        self.assertEqual(w(100.0, 104.0, False, 0), (False, None), "visible again: the timer resets")

    def test_back_off(self):
        c = server.restart_cooldown
        self.assertEqual(c([], 1000.0, 30.0), 30.0)
        self.assertEqual(c([990.0, 980.0], 1000.0, 30.0), 30.0)
        self.assertEqual(c([999.0, 990.0, 980.0, 970.0, 960.0], 1000.0, 30.0), 300.0, "5 restarts in 10 minutes: back off")
        self.assertEqual(c([100.0, 110.0, 120.0, 130.0, 140.0], 1000.0, 30.0), 30.0, "old restarts do not count")


class ProgressParser(unittest.TestCase):
    def feed(self, lines):
        job = {"pct": 0.0, "msg": "", "status": "", "file": "", "samples": collections.deque(maxlen=400), "eta": None, "first_pct_at": None, "pass_since": None}
        for l in lines:
            s = server.ANSI_RE.sub("", l).strip()
            if s: server.handle_line(job, s, collections.deque(maxlen=8))
        return job

    def test_overall_progress_only(self):
        self.assertEqual(self.feed(["Backup     [######....]  42%", "[=====>   ]  15.2%   1.0 MB / 2.0 MB"])["pct"], 42.0)
        self.assertEqual(self.feed(["[=====>   ]  99.0%"])["pct"], 0.0, "the per-file bar is not the overall progress")

    def test_never_goes_backwards(self):
        self.assertEqual(self.feed(["Backup     [#####.....]  50%", "Backup     [###.......]  30%"])["pct"], 50.0)

    def test_frames_glued_together_and_ansi(self):
        self.assertEqual(self.feed(["Status     Receiving filesBackup     [######....]  42%"])["pct"], 42.0)
        self.assertEqual(self.feed(["\x1b[2K\rBackup     [######....]  42%"])["pct"], 42.0)

    def test_status_and_file(self):
        j = self.feed(["Receiving  Library/a.db", "Status     Waiting for passcode to be entered on the device"])
        self.assertEqual((j["file"], "passcode" in j["status"].lower()), ("Library/a.db", True)); self.assertIsNotNone(j["pass_since"])
        self.assertIsNone(self.feed(["Status     Waiting for passcode", "Status     Receiving files"])["pass_since"], "no longer waiting")


class BackupRecoveryFeedback(unittest.TestCase):
    def test_passcode_wait_copy_changes_to_neutral_preparation_after_a_pause(self):
        job = {"status": "Waiting for passcode to be entered on the device", "pass_since": 100.0}
        self.assertIn("waiting for passcode", server.backup_display_status(job, now=110).lower())
        status = server.backup_display_status(job, now=120)
        self.assertIn("如已输入", status)
        self.assertIn("如未输入", status)

    def test_cancelled_device_is_blocked_for_two_minutes(self):
        old = server.CANCEL_COOLDOWNS.get(U)
        try:
            server.CANCEL_COOLDOWNS[U] = 220.0
            self.assertIn("80 秒", server.cancel_cooldown_error(U, now=140.0))
            self.assertIsNone(server.cancel_cooldown_error(U, now=220.0))
            self.assertNotIn(U, server.CANCEL_COOLDOWNS)
        finally:
            if old is None:
                server.CANCEL_COOLDOWNS.pop(U, None)
            else:
                server.CANCEL_COOLDOWNS[U] = old

    def test_backup_start_returns_cooldown_instead_of_calling_device(self):
        old = server.CANCEL_COOLDOWNS.get(U)
        try:
            server.CANCEL_COOLDOWNS[U] = __import__("time").monotonic() + 90
            device = {"udid": U, "conn": "usb", "paired": True}
            from unittest.mock import patch
            with patch.object(server, "device_state", return_value=device), \
                 patch.object(server, "backup_dir", return_value="/backup"), \
                 patch.object(server, "_launch_backup") as launch:
                code, result = server.start_backup(udid=U)
            self.assertEqual(code, 409)
            self.assertIn("取消备份", result["error"])
            launch.assert_not_called()
        finally:
            if old is None:
                server.CANCEL_COOLDOWNS.pop(U, None)
            else:
                server.CANCEL_COOLDOWNS[U] = old


class RemainingTime(unittest.TestCase):
    """The real idevicebackup2 redraws its progress many times per second. The estimate must still appear."""

    class Clock:
        now = 1_000_000.0
        def time(self): return self.now
        def __getattr__(self, name): return getattr(__import__("time"), name)

    def simulate(self, lines_per_second, seconds, start_pct=0.0, pct_per_second=0.03):
        """A backup that advances pct_per_second percent per second, with the given redraw rate. Returns the job."""
        clock = self.Clock(); real = server.time; server.time = clock
        try:
            job = {"pct": 0.0, "msg": "", "status": "", "file": "", "samples": collections.deque(maxlen=1000), "eta": None, "first_pct_at": None, "pass_since": None}
            tail = collections.deque(maxlen=8); steps = int(seconds * lines_per_second)
            for i in range(steps):
                clock.now += 1.0 / lines_per_second
                pct = int(start_pct + (i / lines_per_second) * pct_per_second)      # the tool prints whole percents
                server.handle_line(job, "Backup     [%s%s] %3d%%" % ("#" * (pct // 4), "." * (25 - pct // 4), pct), tail)
            return job, clock
        finally:
            server.time = real

    def test_estimate_appears_even_if_the_tool_redraws_50_times_a_second(self):
        job, _ = self.simulate(lines_per_second=50, seconds=240, start_pct=1.0)
        self.assertIsNotNone(job["eta"], "no estimate after 4 minutes of steady progress (the samples did not span enough time)")
        true_left = (100 - job["pct"]) / 0.03                                  # seconds, at the simulated speed
        self.assertAlmostEqual(job["eta"] / true_left, 1.0, delta=0.35, msg="estimate %.0fs vs real %.0fs" % (job["eta"], true_left))

    def test_estimate_appears_at_a_slow_redraw_rate_too(self):
        job, _ = self.simulate(lines_per_second=4, seconds=240, start_pct=1.0)
        self.assertIsNotNone(job["eta"])

    def test_no_estimate_before_there_is_progress(self):
        job, _ = self.simulate(lines_per_second=50, seconds=240, start_pct=0.0, pct_per_second=0.0)
        self.assertIsNone(job["eta"], "a backup that reports 0% must not invent an estimate")

    def test_slow_progress_of_one_percent_every_few_minutes(self):
        job, _ = self.simulate(lines_per_second=30, seconds=1500, start_pct=1.0, pct_per_second=0.005)
        self.assertIsNotNone(job["eta"], "with whole-percent steps minutes apart the estimate needs a long window")


class SelfCheck(unittest.TestCase):
    def check(self, root):
        server.ROOTS = {"backups": {"name": "backups", "cpath": root, "host": "/vol1/ios-backups", "label": "x"}} if root else {}
        return {i["id"]: i for i in server.run_health()}

    def test_folder_that_is_not_a_mount_is_an_error_with_a_fix(self):
        with tempfile.TemporaryDirectory() as t:
            i = self.check(t)["mount-backups"]
        self.assertEqual(i["level"], "error"); self.assertIn("/vol1/ios-backups:" + t, i["fix"]["en"]); self.assertTrue(i["fix"]["zh"])

    def test_real_mount_is_fine(self):
        ids = self.check("/dev/shm")
        self.assertFalse([k for k in ids if k.startswith(("mount-", "write-"))])

    def test_no_folder_at_all(self):
        self.assertIn("roots", self.check(None))

    def test_every_message_is_translated(self):
        for i in self.check("/nonexistent-folder").values():
            for part in ("title", "fix"): self.assertTrue(i[part]["zh"] and i[part]["en"], i)


class DevicePick(unittest.TestCase):
    A, B = "00008150-000B22C333D4E05F", "00008103-000A11B222C3D04E"

    def test_without_a_choice_it_is_usb_first_then_wifi(self):
        self.assertEqual(server.pick_device([], [], None), (None, None))
        self.assertEqual(server.pick_device([], [self.A], None), (self.A, "wifi"))
        self.assertEqual(server.pick_device([self.B], [self.A], None), (self.B, "usb"))

    def test_the_chosen_device_wins_while_it_is_visible(self):
        self.assertEqual(server.pick_device([self.B], [self.A], self.A), (self.A, "wifi"))
        self.assertEqual(server.pick_device([self.B, self.A], [self.A], self.A), (self.A, "usb"), "on both buses USB is used")

    def test_a_chosen_device_stays_chosen_while_it_is_offline(self):
        self.assertEqual(server.pick_device([self.B], [], self.A), (self.A, None), "another device must not take over")
        self.assertEqual(server.pick_device([], [], self.A), (self.A, None))

    def test_pairing_records_seed_the_device_list_and_forgotten_ones_stay_out(self):
        with tempfile.TemporaryDirectory() as t:
            for n in (self.A + ".plist", self.B + ".plist", "SystemConfiguration.plist", "notes.txt"): open(os.path.join(t, n), "w").close()
            server.LOCKDOWN_DIR = t; server.save_config = lambda: None
            server.CONF.update(devices={}, forgotten=[self.B])
            server.seed_devices()
            self.assertEqual(sorted(server.CONF["devices"]), [self.A])


    def test_an_offline_device_is_named_after_its_backup_folder(self):
        with tempfile.TemporaryDirectory() as t:
            u = self.B; os.makedirs(os.path.join(t, u))
            with open(os.path.join(t, u, "Info.plist"), "wb") as f: plistlib.dump({"Device Name": "Sam’s iPad", "Product Type": "iPad13,1"}, f)
            server.CONF["dirs"] = {u: t}; server.ROOTS = {"v": {"name": "v", "cpath": t, "host": "/h", "label": "x"}}
            self.assertEqual(server.backup_identity(u), ("Sam’s iPad", "iPad13,1"))
            self.assertEqual(server.backup_identity(self.A), (None, None), "no folder configured for this one")
            os.remove(os.path.join(t, u, "Info.plist")); self.assertEqual(server.backup_identity(u), (None, None))


class Helpers(unittest.TestCase):
    def test_folder_names_and_paths_with_control_characters_are_refused(self):
        with tempfile.TemporaryDirectory() as t:
            server.ROOTS = {"v": {"name": "v", "cpath": t, "host": "/h", "label": "x"}}
            for p in ("\x00", t + "/\x00x", t + "\n"): self.assertEqual(server.browse(p)[0], 400, repr(p))
            for n in ("a\x00b", "a\nb", "", ".", "..", "a/b", "@x", ".x", "x" * 65): self.assertEqual(server.make_folder(t, n)[0], 400, repr(n))
            self.assertEqual(server.make_folder(t, "备份 ok")[0], 200)

    def test_password_hash_and_token(self):
        server.AUTH.update(secret="ab" * 32)
        tok = server.make_token(); tampered = tok[:-1] + ("0" if tok[-1] != "0" else "1")
        self.assertTrue(server.check_token(tok)); self.assertFalse(server.check_token(tampered)); self.assertFalse(server.check_token("1.x"))
        self.assertEqual(server.hash_pw("x", "cd" * 16), server.hash_pw("x", "cd" * 16)); self.assertNotEqual(server.hash_pw("x", "cd" * 16), server.hash_pw("y", "cd" * 16))


class RegressionFixes(unittest.TestCase):
    def test_refresh_does_not_mix_devices_after_selection_changes(self):
        from unittest.mock import patch
        with patch.object(server, "DEV", {"udid": "A", "name": "Phone A"}):
            def query(*args):
                server.DEV.update(udid="B", name="Phone B")
                return {"DeviceName": "Phone A"}
            with patch.object(server, "run", return_value=(0, b"")), patch.object(server, "query", side_effect=query):
                server.refresh_info("A", False)
            self.assertEqual(server.DEV, {"udid": "B", "name": "Phone B"})

    def test_cleanup_requires_recorded_directory_identity(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as t:
            root = os.path.join(t, "backups"); os.mkdir(root)
            with patch.object(server, "STATE_DIR", t), patch.object(server, "ROOTS", {"v": {"cpath": root}}):
                unrelated = os.path.join(root, "familyphotos.deleting-keep"); os.mkdir(unrelated)
                target = os.path.join(root, U); os.mkdir(target)
                trash = target + ".deleting-" + "a" * 32
                record = server.record_deletion(target, trash)
                os.rename(target, trash)
                server.sweep_trash()
                self.assertTrue(os.path.isdir(unrelated))
                self.assertFalse(os.path.exists(trash))
                self.assertFalse(os.path.exists(record))
                # A replacement at the same path must not be removed.
                os.mkdir(target)
                record = server.record_deletion(target, trash)
                os.rename(target, target + ".saved")
                os.mkdir(trash)
                server.sweep_trash()
                self.assertTrue(os.path.isdir(trash))
                self.assertTrue(os.path.exists(record))

    def test_malformed_status_date_is_ignored(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as t:
            os.mkdir(os.path.join(t, U))
            with open(os.path.join(t, U, "Status.plist"), "wb") as f:
                plistlib.dump({"Date": "broken", "SnapshotState": "finished"}, f)
            with patch.object(server, "backup_dir", return_value=t):
                self.assertIsNone(server.read_last(U)["ts"])

    def test_job_lock_released_when_finalization_fails(self):
        from unittest.mock import patch
        import threading, time
        for failure in ("metadata", "history"):
            job = {"cancel": False, "way": "usb", "started": time.time()}
            mutex = threading.Lock(); mutex.acquire()
            with patch.object(server, "JOB", job), patch.object(server, "busy", mutex), patch.object(server, "HISTORY", []), patch.object(server, "RESULT", None), patch.object(server.subprocess, "Popen", side_effect=OSError("failed to start")), patch.object(server, "refresh_dirsize", return_value=0), patch.object(server, "read_last", side_effect=ValueError("bad metadata") if failure == "metadata" else None, return_value=None), patch.object(server, "save_history", side_effect=OSError("disk full") if failure == "history" else None):
                if failure == "history":
                    with self.assertRaises(OSError):
                        server.run_backup([], U, "/tmp")
                else:
                    server.run_backup([], U, "/tmp")
                    self.assertEqual(server.RESULT["status"], "failed")
                self.assertIsNone(server.JOB)
                self.assertFalse(mutex.locked())


if __name__ == "__main__":
    unittest.main(verbosity=1)
