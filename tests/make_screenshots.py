#!/usr/bin/env python3
"""Regenerate the screenshots in docs/images from the demo environment (no phone needed).

    pip install playwright pillow
    python3 tests/make_screenshots.py
"""
import io, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from helpers import Api, Server, wait_for  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

OUT = os.path.join(HERE, "..", "docs", "images")
os.makedirs(OUT, exist_ok=True)
UA_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
UA_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
UA_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
NAME = "Alex's iPhone"


def save(png_bytes, name, width=None):
    """Store as a small palette PNG: the interface is flat colours, so 256 colours lose nothing visible."""
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im = im.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)   # keeps rare colours (the small green status dot) better than median cut
    path = os.path.join(OUT, name)
    im.save(path, optimize=True)
    print("  %-34s %4dx%-4d %4d KB" % (name, im.width, im.height, os.path.getsize(path) // 1024))


class Demo(Server):
    def __init__(self, show_health=False, unset=False):
        super().__init__()
        self.env_extra = {} if unset else {"IBS_DEMO": "1", **({"IBS_DEMO_SHOW_HEALTH": "1"} if show_health else {})}

    def start(self):
        os.environ.update(self.env_extra)
        try: return super().start()
        finally:
            for k in ("IBS_DEMO", "IBS_DEMO_SHOW_HEALTH"): os.environ.pop(k, None)


def token(srv):
    a = Api(srv); assert a.post("/api/auth/login", {"password": "demo"})[0] == 200
    return a.cookie.split("=", 1)[1], a


def main():
    pw = sync_playwright().start()
    br = pw.chromium.launch(channel="chrome", headless=True)

    def ctx(kind, srv, tok=None, lang="zh-CN"):
        o = dict(locale=lang)
        if kind == "desktop": o.update(viewport={"width": 1180, "height": 780}, device_scale_factor=2, user_agent=UA_WIN)
        elif kind == "phone": o.update(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, user_agent=UA_IPHONE)
        elif kind == "ipad": o.update(viewport={"width": 820, "height": 1180}, device_scale_factor=2, screen={"width": 820, "height": 1180}, is_mobile=True, has_touch=True, user_agent=UA_MAC)
        c = br.new_context(**o)
        if kind == "ipad": c.add_init_script("Object.defineProperty(navigator,'maxTouchPoints',{get:()=>5})")
        if tok: c.add_cookies([{"name": "ib_session", "value": tok, "url": "http://127.0.0.1:%d" % srv.port}])
        return c

    # ---- A: everything that does not need a running backup
    A = Demo().start(); base = "http://127.0.0.1:%d" % A.port; tok, _ = token(A)
    try:
        print("desktop (Chinese interface)")
        with ctx("desktop", A, tok) as c:
            p = c.new_page(); p.goto(base); expect(p.locator("#hTitle")).to_have_text(NAME, timeout=15000); time.sleep(0.8)
            save(p.locator(".win").screenshot(), "desktop-summary.png", 1500)
            p.locator(".side button", has_text="备份记录").click(); time.sleep(0.4); save(p.locator(".win").screenshot(), "desktop-history.png", 1500)
            p.locator(".side button", has_text="定时备份").click(); time.sleep(0.4); save(p.locator(".win").screenshot(), "desktop-schedule.png", 1500)
            p.locator(".side button", has_text="连接与配对").click(); p.get_by_text("为什么需要关闭").click(); time.sleep(0.4); save(p.locator(".win").screenshot(), "desktop-connection.png", 1500)
            p.locator(".side button", has_text="摘要").click(); p.locator("#pickBtn").click(); expect(p.locator("#dlg")).to_be_visible()
            p.get_by_role("button", name=re.compile("存储空间 1")).click(); time.sleep(0.5); save(p.screenshot(), "desktop-folder-picker.png", 1500)
        for lang, tag, q in (("zh-CN", "", "zh"), ("en-US", "-en", "en")):
            print("phone (%s)" % lang)
            with ctx("phone", A, tok, lang) as c:
                p = c.new_page(); p.goto(base + "/?lang=" + q); expect(p.locator("h1.ttl")).to_have_text(NAME, timeout=15000); time.sleep(0.8)
                save(p.screenshot(), "mobile-main%s.png" % tag, 780)
                p.locator("#cards .acard").nth(0).click(); expect(p.locator("#extra .li").first).to_be_visible(timeout=10000); time.sleep(0.6)
                save(p.screenshot(), "mobile-backup%s.png" % tag, 780)
                p.locator("#hdr .cbtn").first.click(); expect(p.locator("h1.ttl")).to_have_text(NAME)
                p.locator("#cards .acard").nth(1).locator(".go").click(); time.sleep(0.6); save(p.screenshot(), "mobile-schedule%s.png" % tag, 780)
                p.keyboard.press("Escape"); time.sleep(0.2)
                p.locator("#cards .acard").nth(2).locator(".go").click(); time.sleep(0.4)
                p.locator("#sheet details summary").click(); time.sleep(0.5); save(p.screenshot(), "mobile-connection%s.png" % tag, 780)
        print("iPad")
        with ctx("ipad", A, tok) as c:
            p = c.new_page(); p.goto(base); p.wait_for_url("**/m", timeout=10000); expect(p.locator("h1.ttl")).to_have_text(NAME, timeout=15000); time.sleep(0.8)
            save(p.screenshot(), "ipad.png", 1100)
    finally:
        A.stop()

    # ---- B: a backup in progress (desktop), C: the same on the phone in both languages
    for label in ("desktop", "phone"):
        S = Demo().start(); b = "http://127.0.0.1:%d" % S.port; t, api = token(S)
        try:
            print("%s: backup in progress" % label)
            if label == "desktop":
                with ctx("desktop", S, t) as c:
                    p = c.new_page(); p.goto(b); expect(p.locator("#hTitle")).to_have_text(NAME, timeout=15000)
                    api.post("/api/backup"); expect(p.locator("#prog")).to_be_visible(timeout=10000)
                    wait_for(lambda: (api.get("/api/status")[1]["job"] or {}).get("pct", 0) >= 38, 40, 0.3); time.sleep(0.5)
                    save(p.locator(".win").screenshot(), "desktop-backup-running.png", 1500)
            else:
                wait_for(lambda: api.get("/api/status")[1]["device"]["paired"] is True, 25, msg="the demo phone")
                assert api.post("/api/backup")[0] == 200; wait_for(lambda: (api.get("/api/status")[1]["job"] or {}).get("pct", 0) >= 38, 40, 0.3)
                for lang, tag, q in (("zh-CN", "", "zh"), ("en-US", "-en", "en")):
                    with ctx("phone", S, t, lang) as c:
                        p = c.new_page(); p.goto(b + "/?lang=%s#backup" % q); expect(p.locator("#rpct")).to_be_visible(timeout=10000); time.sleep(0.6)
                        save(p.screenshot(), "mobile-running%s.png" % tag, 780)
        finally:
            S.stop()

    # ---- D: the Setup check card, E: the password screens of a fresh install
    print("desktop: setup check card")
    hs = Demo(show_health=True).start()
    try:
        t2, _ = token(hs)
        with ctx("desktop", hs, t2) as c:
            p = c.new_page(); p.goto("http://127.0.0.1:%d" % hs.port); expect(p.locator("#healthCard")).to_be_visible(timeout=15000); time.sleep(0.6)
            save(p.locator(".win").screenshot(), "desktop-setup-check.png", 1500)
    finally:
        hs.stop()
    print("password screens (fresh install)")
    fresh = Demo(unset=True).start(); fbase = "http://127.0.0.1:%d" % fresh.port
    try:
        with ctx("desktop", fresh) as c:
            p = c.new_page(); p.goto(fbase); expect(p.locator("#lockBox")).to_be_visible(); p.get_by_text("添加提示词").click()
            p.locator("#lockBox input[type=password]").nth(0).fill("correct horse"); p.locator("#lockBox input[type=password]").nth(1).fill("correct horse")
            p.locator("#lockBox input[type=text]").fill("a famous battery staple"); time.sleep(0.3)
            save(p.screenshot(), "desktop-password.png", 1300)
        for lang, tag, q in (("zh-CN", "", "zh"), ("en-US", "-en", "en")):
            with ctx("phone", fresh, None, lang) as c:
                p = c.new_page(); p.goto(fbase + "/?lang=" + q); expect(p.locator("#lockBox")).to_be_visible(); time.sleep(0.4)
                save(p.screenshot(), "mobile-password%s.png" % tag, 780)
                p.locator("#lockBar .cbtn").click(); expect(p.locator(".langmenu")).to_be_visible(); time.sleep(0.3)
                save(p.screenshot(), "mobile-language%s.png" % tag, 780)
    finally:
        fresh.stop()

    print("hero")
    d = Image.open(os.path.join(OUT, "desktop-summary.png")).convert("RGB"); m = Image.open(os.path.join(OUT, "mobile-main-en.png")).convert("RGB")
    H = 900; d = d.resize((round(d.width * H / d.height), H), Image.LANCZOS); mh = 820; m = m.resize((round(m.width * mh / m.height), mh), Image.LANCZOS)
    W = d.width + m.width + 120; canvas = Image.new("RGB", (W, H + 80), (236, 235, 226)); canvas.paste(d, (40, 40)); canvas.paste(m, (d.width + 80, 40 + (H - mh) // 2))
    buf = io.BytesIO(); canvas.save(buf, "PNG"); save(buf.getvalue(), "hero.png", 1800)
    br.close(); pw.stop()
    print("done ->", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
