#!/usr/bin/env python3
"""Browser tests: real Chrome (Playwright) against the real server and the fake phone (tests/fakephone).

    pip install playwright        # uses the Chrome that is already installed, no browser download
    python3 tests/test_ui.py
"""
import os, re, sys, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Api, SECOND_UDID, Server, wait_for  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

UA_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
UA_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
UA_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TOUCH5 = "Object.defineProperty(navigator,'maxTouchPoints',{get:()=>5})"   # a real iPad reports 5 touch points
FRESH = dict(usb=False, net=True, blind=False, net_blind=False, paired=True, enc=True, wifi_sync=True, locked_wait=0.0, backup_seconds=5.0,
             abort_delay=1.0, backup_fail=False, restart_fixes=True, name="Air")


class UI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)
        cls.srv = Server().start(); cls.api = Api(cls.srv); cls.base = "http://127.0.0.1:%d" % cls.srv.port
        assert cls.api.post("/api/auth/setup", {"password": "pw", "hint": "two letters"})[0] == 200
        cls.token = cls.api.cookie.split("=", 1)[1]

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.srv.stop()

    def setUp(self):
        self.errors = []
        self.srv.phone(**FRESH)
        self.idle()
        wait_for(lambda: self.api.get("/api/status")[1]["device"]["conn"] == "wifi", 20)

    def idle(self):
        if self.api.get("/api/status")[1]["job"]: self.api.post("/api/cancel")
        wait_for(lambda: self.api.get("/api/status")[1]["job"] is None, 30)

    # ---- browser contexts
    def context(self, kind, logged_in=True, lang="zh-CN", base=None):
        base = base or self.base
        o = dict(locale=lang)
        if kind == "desktop": o.update(viewport={"width": 1280, "height": 800}, user_agent=UA_WIN)
        elif kind == "phone": o.update(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, user_agent=UA_IPHONE)
        elif kind == "ipad": o.update(viewport={"width": 820, "height": 1180}, screen={"width": 820, "height": 1180}, is_mobile=True, has_touch=True, user_agent=UA_MAC)
        elif kind == "phone_desktop_site": o.update(viewport={"width": 980, "height": 1500}, screen={"width": 390, "height": 844}, has_touch=True, user_agent=UA_MAC)
        c = self.browser.new_context(**o)
        if kind in ("ipad", "phone_desktop_site"): c.add_init_script(TOUCH5)
        if logged_in: c.add_cookies([{"name": "ib_session", "value": self.token, "url": base}])
        return c

    def page(self, ctx, path="/"):
        p = ctx.new_page()
        p.on("console", lambda m: self.errors.append("console: " + m.text) if m.type == "error" and "status of 401" not in m.text else None)
        p.on("pageerror", lambda e: self.errors.append("pageerror: %s" % e))
        p.goto(self.base + path); return p

    def tearDown(self):
        self.assertEqual(self.errors, [], "JavaScript errors in the browser")

    # ------------------------------------------------------------------ routing
    def test_01_the_right_interface_for_each_device(self):
        with self.context("desktop") as c:
            p = self.page(c); expect(p.locator(".win")).to_be_visible(); self.assertEqual(p.url, self.base + "/")
        with self.context("phone") as c:
            p = self.page(c); expect(p.locator("meta[name=apple-mobile-web-app-capable]")).to_have_count(1); self.assertEqual(p.url, self.base + "/")
        with self.context("ipad") as c:
            p = self.page(c); p.wait_for_url("**/m", timeout=8000); expect(p.locator("#cards")).to_be_attached()
        with self.context("phone_desktop_site") as c:
            p = self.page(c); time.sleep(1.5); expect(p.locator(".win")).to_be_visible(); self.assertEqual(p.url, self.base + "/", "an iPhone that asked for the desktop site must stay on it")
        with self.context("desktop") as c:
            p = self.page(c, "/?ui=mobile"); expect(p.locator("#cards")).to_be_attached()

    # ------------------------------------------------------------------ password screens
    def test_02_password_screens(self):
        for kind in ("desktop", "phone"):
            srv = Server().start()
            try:
                base = "http://127.0.0.1:%d" % srv.port
                with self.browser.new_context(**({"viewport": {"width": 1280, "height": 800}, "user_agent": UA_WIN} if kind == "desktop" else
                                                 {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True, "user_agent": UA_IPHONE}), locale="zh-CN") as c:
                    p = c.new_page(); p.on("pageerror", lambda e: self.errors.append("pageerror: %s" % e)); p.goto(base)
                    box = p.locator("#lockBox"); expect(box).to_be_visible(); expect(box.locator("code")).to_have_text("ios-backup-station")
                    p1 = box.locator("input[type=password]").nth(0); p2 = box.locator("input[type=password]").nth(1)
                    box.get_by_role("button", name=re.compile("设置密码")).click(); expect(box.locator(".err")).to_contain_text("请输入密码")
                    p1.fill("a"); p2.fill("b"); box.get_by_role("button", name=re.compile("设置密码")).click(); expect(box.locator(".err")).to_contain_text("不一致")
                    hint_input = box.locator("input[type=text]"); expect(hint_input).to_be_hidden()
                    box.get_by_text("添加提示词").click(); expect(hint_input).to_be_visible(); hint_input.fill("my hint")
                    p2.fill("a"); box.get_by_role("button", name=re.compile("设置密码")).click()
                    expect(p.locator("#lock")).to_be_hidden(timeout=8000)
                    expect(p.locator("#dlg" if kind == "desktop" else "#scrim")).to_be_visible(timeout=15000)   # first run: choose the backup folder
                    p.keyboard.press("Escape"); expect(p.locator("#dlg" if kind == "desktop" else "#scrim")).to_be_hidden()
                    # sign out and back in
                    if kind == "desktop": p.locator("#logoutBtn").click()
                    else:
                        p.get_by_role("button", name="更多", exact=True).click(); p.get_by_text("退出登录").first.click()
                    expect(p.locator("#lock")).to_be_visible(); box = p.locator("#lockBox"); expect(box.locator("input[type=password]")).to_have_count(1)
                    box.locator("input[type=password]").fill("x"); box.get_by_role("button", name=re.compile("进入")).click()
                    expect(box.locator(".err")).to_contain_text("密码不对"); expect(box.get_by_text("提示：my hint")).to_have_count(0)
                    box.get_by_role("button", name=re.compile("进入")).click(); expect(box.get_by_text("提示：my hint")).to_be_visible()
                    box.locator("input[type=password]").fill("a"); box.get_by_role("button", name=re.compile("进入")).click()
                    expect(p.locator("#lock")).to_be_hidden(timeout=8000)
                    p.reload(); expect(p.locator("#lock")).to_be_hidden(timeout=8000)           # the cookie keeps you signed in
            finally:
                srv.stop()

    def test_03_language_switch_on_the_phone_password_screen(self):
        srv = Server().start()
        try:
            base = "http://127.0.0.1:%d" % srv.port
            with self.browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, user_agent=UA_IPHONE, locale="en-US") as c:
                p = c.new_page(); p.on("pageerror", lambda e: self.errors.append("pageerror: %s" % e)); p.goto(base)
                expect(p.locator("#lockBox .lead")).to_have_text("Set a password")          # follows the browser language
                p.get_by_role("button", name="Language").click(); expect(p.locator(".langmenu")).to_be_visible()
                p.locator(".langmenu").get_by_text("简体中文").click(); expect(p.locator("#lockBox .lead")).to_have_text("设置访问密码")
                p.reload(); expect(p.locator("#lockBox .lead")).to_have_text("设置访问密码")      # remembered
                p.get_by_role("button", name="语言").click(); p.locator(".langmenu").get_by_text("English").click()
                expect(p.locator("#lockBox .lead")).to_have_text("Set a password")
                expect(p.get_by_placeholder("Confirm password", exact=True)).to_be_visible(); expect(p.get_by_text("Forgot password?")).to_be_visible()
                p.get_by_placeholder("Password", exact=True).fill("a"); p.get_by_placeholder("Confirm password", exact=True).fill("b")
                p.get_by_role("button", name="Set Password").click(); expect(p.locator("#lockBox .err")).to_have_text("The passwords don’t match.")
        finally:
            srv.stop()

    # ------------------------------------------------------------------ desktop
    def test_04_desktop_everything(self):
        with self.context("desktop") as c:
            p = self.page(c)
            expect(p.locator("#hTitle")).to_have_text("Air", timeout=15000)
            expect(p.locator("#lcdT")).to_have_text("请选择备份保存位置"); expect(p.locator("#devName")).to_have_count(0)   # nothing chosen yet on a fresh install
            expect(p.locator("#dlg")).to_be_visible(timeout=15000); p.keyboard.press("Escape"); expect(p.locator("#dlg")).to_be_hidden()   # first run opens the folder picker by itself
            expect(p.locator(".lcd #dot")).to_have_count(1); expect(p.locator("#uiBtn")).to_have_count(0)
            expect(p.locator("#healthCard")).to_be_visible(); expect(p.locator("#hcT")).to_contain_text("环境自检")
            for tab, pane in (("备份记录", "p-bak"), ("定时备份", "p-sch"), ("连接与配对", "p-con"), ("运行日志", "p-log"), ("摘要", "p-sum")):
                p.locator(".side button", has_text=tab).click(); expect(p.locator("#" + pane)).to_be_visible()
            # no folder yet: the picker opens, the suggestion creates and selects the folder
            p.locator("#goBackup").click(); expect(p.locator("#dlg")).to_be_visible(); expect(p.locator("#dBox h2")).to_have_text("选择备份文件夹")
            p.locator(".sugg").get_by_role("button", name="使用推荐位置").click()
            expect(p.locator("#dlg")).to_be_hidden(timeout=8000); expect(p.locator("#destPath")).to_have_text("/vol1/1000/iphone_backups", timeout=8000)
            # start, watch the progress, cancel with the two-step confirmation
            self.srv.phone(backup_seconds=40.0); p.locator("#goBackup").click()
            expect(p.locator("#prog")).to_be_visible(timeout=8000); expect(p.locator("#goCancel")).to_have_text("取消备份")
            expect(p.locator("#pElapsed")).to_contain_text(re.compile(r"[KM]B/s"), timeout=20000)      # the speed shows up
            vals = []
            for _ in range(10):
                vals.append(int(p.locator("#pPct").inner_text().rstrip("%"))); time.sleep(0.6)
            self.assertEqual(vals, sorted(vals), "progress shown to the user went backwards: %r" % vals); self.assertGreater(vals[-1], vals[0])
            p.locator("#goCancel").click(); expect(p.locator("#goCancel")).to_have_text("再点一次确认取消"); expect(p.locator("#lcdX")).to_have_text("确定取消？")
            expect(p.locator("#goCancel")).to_have_text("取消备份", timeout=8000)          # the confirmation expires by itself
            p.locator("#pX").click(); p.locator("#pX").click()
            expect(p.locator("#prog")).to_be_hidden(timeout=25000); expect(p.locator("#lcdT")).to_have_text("备份已取消")
            # a complete backup
            self.srv.phone(backup_seconds=4.0); p.locator("#goBackup").click()
            expect(p.locator("#lcdT")).to_have_text("备份完成", timeout=30000); expect(p.locator("#lbTime")).not_to_have_text("尚未备份"); expect(p.locator("#lbEnc")).to_have_text("是")
            p.locator(".side button", has_text="备份记录").click()
            rows = p.locator("#rows tr"); self.assertGreaterEqual(rows.count(), 2); expect(rows.nth(0)).to_contain_text("成功"); expect(rows.nth(1)).to_contain_text("已取消")
            # schedule
            p.locator(".side button", has_text="定时备份").click()
            self.assertEqual(p.locator("#schTime").input_value(), "20:00")
            p.locator("#schOn").check(); p.locator("#schTime").fill("21:45"); p.locator("#schD5").uncheck(); p.locator("#schRetry").select_option("6")
            p.locator("#schSave").click(); expect(p.locator("#schMsg")).to_have_text("已保存"); expect(p.locator("#schNext")).to_contain_text("21:45")
            p.reload(); p.locator(".side button", has_text="定时备份").click()
            self.assertEqual((p.locator("#schTime").input_value(), p.locator("#schD5").is_checked(), p.locator("#schRetry").input_value()), ("21:45", False, "6"))
            # connection page, log page
            p.locator(".side button", has_text="连接与配对").click(); expect(p.locator("#s1")).to_have_class(re.compile("done")); expect(p.locator("#s4")).to_have_class(re.compile("done"))
            p.locator("#probe").click(); expect(p.locator("#probeOut")).to_contain_text("无线", timeout=15000)
            p.locator(".side button", has_text="运行日志").click(); expect(p.locator("#logBox")).to_contain_text("backup", timeout=8000)
            # encryption dialog: wrong password is reported, Escape closes it
            p.locator(".side button", has_text="摘要").click(); p.locator("#encChk").click(); expect(p.locator("#dBox h2")).to_have_text("关闭备份加密")
            p.locator("#pw1").fill("wrong"); p.get_by_role("button", name="关闭加密").click()
            expect(p.locator("#dErr")).to_contain_text("正在向手机设置"); expect(p.locator("#dErr")).to_contain_text("password", timeout=20000)   # the phone's answer replaces the waiting text
            p.keyboard.press("Escape"); expect(p.locator("#dlg")).to_be_hidden()
            expect(p.locator("#encChk")).to_be_checked()

    # ------------------------------------------------------------------ phone
    def test_05_phone_everything(self):
        self.api.post("/api/auth/login", {"password": "pw"})
        root = self.api.get("/api/browse?path=")[1]["volumes"][0]["path"]; self.api.post("/api/mkdir", {"parent": root, "name": "iphone_backups"})
        self.api.post("/api/config", {"path": root + "/iphone_backups"})
        with self.context("phone") as c:
            p = self.page(c)
            expect(p.locator("h1.ttl")).to_have_text("Air", timeout=15000); expect(p.locator(".desc")).to_contain_text(re.compile("已连接，正在待命|备份完成"))   # "done" shows for 90 s after a backup
            titles = p.locator("#cards .acard b").all_inner_texts()
            self.assertEqual([t for t in titles if t != "环境自检"], ["立即备份", "定时备份", "连接与配对", "运行日志"])
            p.locator("#cards .acard", has_text="立即备份").first.click()
            expect(p.locator("h1.ttl")).to_have_text("立即备份")
            self.assertEqual(p.locator("#cards .acard b").all_inner_texts(), ["开始备份", "加密备份", "备份位置", "删除备份"])
            expect(p.get_by_text("备份记录", exact=True)).to_be_visible()                  # the history sits under the cards
            self.srv.phone(backup_seconds=40.0)
            p.locator("#cards .acard", has_text="开始备份").click()
            expect(p.locator("#rpct")).to_be_visible(timeout=10000)
            expect(p.locator(".desc")).to_contain_text(re.compile(r"[KM]B/s"), timeout=20000)
            vals = []
            for _ in range(8):
                vals.append(int(p.locator("#rpct").inner_text().rstrip("%"))); time.sleep(0.7)
            self.assertEqual(vals, sorted(vals), "ring percentage went backwards: %r" % vals)
            expect(p.locator("#cards .acard", has_text="加密备份")).to_have_class(re.compile("dis"))      # settings are locked during a backup
            p.locator("#cards .acard", has_text="取消备份").click(); expect(p.locator("#cards .acard b").first).to_have_text("再点一次确认取消")
            expect(p.locator("#cards .acard b").first).to_have_text("取消备份", timeout=8000)
            p.locator("#cards .acard", has_text="取消备份").click(); p.locator("#cards .acard", has_text="再点一次确认取消").click()
            expect(p.locator("#rpct")).to_have_count(0, timeout=25000); expect(p.locator(".desc")).to_contain_text("已取消")
            self.srv.phone(backup_seconds=4.0); p.locator("#cards .acard", has_text="开始备份").click()
            expect(p.locator(".desc")).to_contain_text("备份完成", timeout=30000)
            self.assertGreaterEqual(p.locator("#extra .li").count(), 2)
            # sheets
            p.locator("#cards .acard", has_text="加密备份").locator(".go").click(); expect(p.locator("#shT")).to_have_text("关闭备份加密")
            p.keyboard.press("Escape"); expect(p.locator("#scrim")).to_be_hidden()
            p.locator("#cards .acard", has_text="备份位置").get_by_role("button", name="了解更多…").click(); expect(p.locator("#shT")).to_have_text("备份位置"); p.get_by_role("button", name="完成").click()
            p.locator("#cards .acard", has_text="备份位置").locator(".go").click(); expect(p.locator("#shT")).to_have_text("选择备份文件夹")
            expect(p.locator("#sheet")).to_contain_text("存储空间 1")           # the picker starts at the list of shared volumes
            p.get_by_role("button", name=re.compile("存储空间 1")).click(); expect(p.get_by_role("button", name=re.compile("上一级"))).to_be_visible(); p.get_by_role("button", name="完成").click()
            p.get_by_role("button", name="返回").click(); expect(p.locator("h1.ttl")).to_have_text("Air")
            p.locator("#cards .acard", has_text="定时备份").locator(".go").click(); expect(p.locator("#shT")).to_have_text("定时备份")
            p.locator("#sheet input[type=time]").fill("22:10"); p.locator("#sheet .days button").nth(6).click(); p.get_by_role("button", name="保存").click()
            expect(p.locator(".toast")).to_have_text("已保存")
            p.locator("#cards .acard", has_text="连接与配对").locator(".go").click(); expect(p.locator("#shT")).to_have_text("连接与配对")
            p.get_by_text("为什么需要关闭“私有无线局域网地址”？").click(); expect(p.get_by_text("它不是让手机换 IP。")).to_be_visible(); p.get_by_role("button", name="完成").click()
            p.locator("#cards .acard", has_text="运行日志").locator(".go").click(); expect(p.locator("#sheet pre")).to_contain_text("backup", timeout=8000); p.get_by_role("button", name="完成").click()
            # language
            p.get_by_role("button", name="更多", exact=True).click(); p.get_by_text("语言").first.click()
            expect(p.locator("#cards .acard", has_text="Back Up Now")).to_be_visible(); expect(p.locator("html")).to_have_attribute("lang", "en")
            self.assertEqual(p.locator("#cards .acard b").all_inner_texts()[-3:], ["Scheduled Backup", "Connection & Pairing", "Logs"])
            self.assertIn("Setup check", p.locator("#cards .acard b").all_inner_texts())          # the health card is translated too

    # ------------------------------------------------------------------ states, escaping, layout, accessibility
    def test_06_states_offline_and_unpaired(self):
        with self.context("desktop") as d, self.context("phone") as m:
            pd, pm = self.page(d), self.page(m)
            expect(pd.locator("#hTitle")).to_have_text("Air", timeout=15000)
            self.srv.phone(net=False, usb=False)
            expect(pd.locator("#lcdT")).to_have_text("未检测到设备", timeout=20000); expect(pm.locator(".desc")).to_contain_text("未检测到设备", timeout=20000)
            expect(pm.locator("#cards .acard", has_text="检测连接")).to_be_visible()
            self.srv.phone(net=True, paired=False)
            expect(pd.locator("#lcdT")).to_have_text("尚未配对", timeout=20000); expect(pm.locator(".desc")).to_contain_text("尚未配对", timeout=20000)
            expect(pm.locator("#cards .acard", has_text="去配对")).to_be_visible()
            self.srv.phone(paired=True)
            expect(pd.locator("#lcdT")).not_to_have_text(re.compile("未检测|尚未配对"), timeout=20000); expect(pm.locator(".desc")).not_to_contain_text(re.compile("未检测|尚未配对"), timeout=20000)

    def test_07_hostile_text_is_never_run_as_html(self):
        evil_name = '<img src=x onerror="window.__xss=1"> <b id="pwned">x</b>'
        root = self.api.get("/api/browse?path=")[1]["volumes"][0]["path"]
        self.assertEqual(self.api.post("/api/mkdir", {"parent": root, "name": '<i id=evilfolder>z'})[0], 200)
        self.srv.phone(name=evil_name); time.sleep(0.2)
        wait_for(lambda: self.api.get("/api/status")[1]["device"]["name"] == evil_name, 40, msg="the hostile device name")
        for kind, sel in (("desktop", "#hTitle"), ("phone", "h1.ttl")):
            with self.context(kind) as c:
                p = self.page(c); expect(p.locator(sel)).to_have_text(evil_name, timeout=15000)
                self.assertIsNone(p.evaluate("window.__xss")); self.assertEqual(p.locator("#pwned").count(), 0)
                if kind == "desktop": p.locator("#pickBtn").click()
                else: p.locator("#cards .acard", has_text="立即备份").first.click(); p.locator("#cards .acard", has_text="备份位置").locator(".go").click()
                p.get_by_role("button", name=re.compile("存储空间 1")).click(); expect(p.get_by_text('<i id=evilfolder>z')).to_be_visible(timeout=8000)
                self.assertEqual(p.locator("#evilfolder").count(), 0); self.assertIsNone(p.evaluate("window.__xss"))
        self.srv.phone(name="Air")

    def test_08_no_sideways_scrolling_at_any_width(self):
        widths = (320, 360, 390, 430, 600, 768, 1024, 1440)
        for kind, path in (("desktop", "/?ui=desktop"), ("phone", "/m")):
            with self.context("desktop" if kind == "desktop" else "phone") as c:
                p = self.page(c, path); p.wait_for_selector("#hTitle" if kind == "desktop" else "h1.ttl", timeout=15000)
                for w in widths:
                    p.set_viewport_size({"width": w, "height": 900}); time.sleep(0.15)
                    views = ["#p-sum", "#p-bak", "#p-sch", "#p-con", "#p-log"] if kind == "desktop" else [None]
                    for v in views:
                        if v: p.locator(".side button[data-p=%s]" % v[3:]).click()
                        over = p.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                        self.assertLessEqual(over, 1, "%s UI at %dpx wide scrolls sideways by %dpx (%s)" % (kind, w, over, v))

    def test_09_every_button_and_field_has_a_name(self):
        for kind, path in (("desktop", "/"), ("phone", "/")):
            with self.context(kind) as c:
                p = self.page(c, path); p.wait_for_selector("#hTitle" if kind == "desktop" else "h1.ttl", timeout=15000)
                bad = p.evaluate("""() => [...document.querySelectorAll('button, input, select')].filter(e => {
                    if (e.hidden || e.closest('[hidden]')) return false;
                    const name = (e.getAttribute('aria-label') || e.title || e.textContent || e.placeholder || '').trim();
                    const label = e.id && document.querySelector('label[for="'+e.id+'"]');
                    const wrapped = e.closest('label');
                    return !name && !label && !wrapped && e.type !== 'radio' && e.type !== 'checkbox';
                }).map(e => e.outerHTML.slice(0, 90))""")
                self.assertEqual(bad, [], "%s UI: controls without an accessible name" % kind)


    def make_backup(self):
        self.api.post("/api/probe")
        wait_for(lambda: self.api.get("/api/status")[1]["device"]["name"] == "Air", 45, msg="the device name to refresh after test_07")
        root = self.api.get("/api/browse?path=")[1]["volumes"][0]["path"]; self.api.post("/api/mkdir", {"parent": root, "name": "iphone_backups"})
        self.api.post("/api/config", {"path": root + "/iphone_backups"})
        self.srv.phone(backup_seconds=3.0); self.api.post("/api/backup")
        wait_for(lambda: self.api.get("/api/status")[1]["job"] is not None, 8, 0.1)
        wait_for(lambda: self.api.get("/api/status")[1]["job"] is None, 30)
        wait_for(lambda: (self.api.get("/api/status")[1]["storage"]["dir"] or 0) > 0, 10, msg="the backup folder size")

    def test_10_delete_backup_on_the_desktop(self):
        self.make_backup()
        with self.context("desktop") as c:
            p = self.page(c)
            expect(p.locator("#hTitle")).to_have_text("Air", timeout=15000)
            expect(p.locator("#goDelete")).to_be_enabled(timeout=8000)
            p.locator("#goDelete").click(); expect(p.locator("#dBox h2")).to_have_text("删除这台设备的备份")
            expect(p.locator("#dName")).to_have_text("Air")
            p.locator("[data-a=del]").click(); expect(p.locator("#dErr")).to_contain_text("请输入“删除”")      # needs the typed word
            self.assertGreater(self.api.get("/api/status")[1]["storage"]["dir"], 0)
            p.keyboard.press("Escape"); expect(p.locator("#dlg")).to_be_hidden()
            p.locator("#goDelete").click(); p.locator("#dc").fill("删除"); p.locator("[data-a=del]").click()
            expect(p.locator("#dlg")).to_be_hidden(timeout=8000)
            wait_for(lambda: not self.api.get("/api/status")[1]["deleting"], 20)
            expect(p.locator("#goDelete")).to_be_disabled(timeout=15000)       # nothing left to delete
            expect(p.locator("#lbTime")).to_have_text("尚未备份", timeout=15000)

    def test_11_delete_backup_on_the_phone(self):
        self.make_backup()
        with self.context("phone", lang="en-US") as c:
            p = self.page(c, "/m?lang=en#backup")
            expect(p.locator("h1.ttl")).to_have_text("Back Up Now", timeout=15000)
            expect(p.locator("#cards .acard", has_text="Delete Backup")).to_be_visible()
            p.locator("#cards .acard", has_text="Delete Backup").click()
            p.locator(".cta", has_text="Delete Backup").click(); expect(p.locator(".err")).to_contain_text("Type DELETE")
            p.locator("input.field").fill("delete"); p.locator(".cta", has_text="Delete Backup").click()
            wait_for(lambda: (self.api.get("/api/status")[1]["storage"]["dir"] or 0) == 0, 30, msg="backup folder to empty")
            expect(p.locator("#cards .acard", has_text="Delete Backup")).to_have_class(re.compile("dis"), timeout=15000)


    def test_12_device_switcher_on_the_desktop(self):
        root = self.api.get("/api/browse?path=")[1]["volumes"][0]["path"]; self.api.post("/api/mkdir", {"parent": root, "name": "iphone_backups"})
        self.api.post("/api/config", {"path": root + "/iphone_backups"})       # otherwise the first-run folder picker opens by itself
        self.srv.phone(second=None)
        with self.context("desktop") as c:
            p = self.page(c)
            expect(p.locator("#hTitle")).to_have_text("Air", timeout=15000)
            expect(p.locator("#devSw")).to_be_hidden()                               # one device: nothing to switch
            self.srv.phone(second={"conn": "usb", "name": "Sam’s iPad", "ptype": "iPad13,1"})
            expect(p.locator("#hTitle")).to_have_text("Sam’s iPad", timeout=30000)   # the USB device takes over by itself
            self.api.post("/api/mkdir", {"parent": root, "name": "ipad_backups"}); self.api.post("/api/config", {"path": root + "/ipad_backups"})
            if p.locator("#dlg").is_visible(): p.keyboard.press("Escape")             # the folder picker opens by itself for a device without one
            expect(p.locator("#devSw")).to_be_visible(timeout=10000)
            wait_for(lambda: all(d["name"] for d in self.api.get("/api/status")[1]["devices"]), 90, msg="names of both devices")
            p.locator("#devSwBtn").click(); expect(p.locator("#devMenu")).to_be_visible()
            expect(p.locator("#devMenu button")).to_have_count(2)
            expect(p.locator("#devMenu button[aria-checked=true]")).to_contain_text("Sam’s iPad")
            p.keyboard.press("Escape"); expect(p.locator("#devMenu")).to_be_hidden()
            p.locator("#devSwBtn").click(); p.locator("#devMenu button", has_text="Air").click()
            expect(p.locator("#hTitle")).to_have_text("Air", timeout=15000); expect(p.locator("#devMenu")).to_be_hidden()
            expect(p.locator("#kvUdid")).not_to_have_text(SECOND_UDID)
            self.srv.phone(backup_seconds=25.0); self.api.post("/api/backup")           # during a backup the switcher is locked
            expect(p.locator("#devSwBtn")).to_be_disabled(timeout=15000)
            self.idle()
            self.srv.phone(second=None)
            expect(p.locator("#devSw")).to_be_hidden(timeout=30000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
