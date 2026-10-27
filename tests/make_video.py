#!/usr/bin/env python3
"""Record the three-minute demo video (silent, bilingual captions) from the demo environment. No phone needed.

    pip install playwright imageio-ffmpeg pillow
    python3 tests/make_video.py            ->  docs/video/ios-backup-station-demo.mp4
"""
import functools, http.server, os, re, shutil, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("IBS_DEMO_BACKUP_SECONDS", "13")
from helpers import Api, Server, wait_for  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402
import imageio_ffmpeg  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(ROOT, "docs", "video")
FF = imageio_ffmpeg.get_ffmpeg_exe()
W, H = 1280, 720
NAME = "Alex's iPhone"

CURSOR_JS = """
(() => {
  const mk = () => {
    if (document.getElementById('__cur')) return;
    const st = document.createElement('style');
    st.textContent = '#__cur{position:fixed;z-index:2147483647;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;background:rgba(107,104,51,.28);border:2px solid #6b6833;pointer-events:none;left:-50px;top:-50px;transition:transform .12s}' +
      '#__cur.d{transform:scale(.7)}.__rip{position:fixed;z-index:2147483646;width:14px;height:14px;margin:-7px 0 0 -7px;border-radius:50%;border:3px solid #6b6833;pointer-events:none;animation:__r .55s ease-out forwards}' +
      '@keyframes __r{to{transform:scale(4.5);opacity:0}}';
    document.documentElement.appendChild(st);
    const c = document.createElement('div'); c.id = '__cur'; document.documentElement.appendChild(c);
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', e => { c.classList.add('d'); const r = document.createElement('div'); r.className = '__rip'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px'; document.documentElement.appendChild(r); setTimeout(() => r.remove(), 700); }, true);
    addEventListener('mouseup', () => c.classList.remove('d'), true);
  };
  if (document.readyState !== 'loading') mk(); else addEventListener('DOMContentLoaded', mk);
})();
"""


class Scene:
    """One recorded clip. `until(s)` pads the clip to a fixed length so the whole video lands on three minutes."""
    def __init__(self, rec, name, target):
        self.name, self.target, self.rec = name, target, rec
        self.ctx = rec.browser.new_context(viewport={"width": W, "height": H}, record_video_dir=rec.tmp, record_video_size={"width": W, "height": H}, locale="zh-CN")
        self.ctx.add_init_script(CURSOR_JS)
        # The server (rightly) forbids framing; the recording stage frames it, so drop that header for this browser only.
        def _unframe(route):
            r = route.fetch(); h = {k: v for k, v in r.headers.items() if k.lower() not in ("x-frame-options", "content-security-policy")}
            route.fulfill(response=r, headers=h)
        self.ctx.route(re.compile(r"http://127\.0\.0\.1:\d+/.*"), _unframe)
        self.page = self.ctx.new_page(); self.page.goto(rec.stage); self.page.wait_for_function("window.show !== undefined")
        self.t0 = time.time()

    def js(self, code, *a): return self.page.evaluate(code, *a)
    def say(self, zh, en=""): self.page.evaluate("([z,e]) => window.say(z,e)", [zh, en])
    def until(self, s):
        left = self.t0 + s - time.time()
        if left > 0: time.sleep(left)
        elif left < -1.5: print("   ! scene %s is %.1fs over its %ss budget" % (self.name, -left, self.target))

    def move_click(self, loc, pause=0.25):
        for attempt in range(6):          # the phone UI re-renders on every poll, so an element can be swapped out mid-action
            try:
                loc.scroll_into_view_if_needed(timeout=3000); b = loc.bounding_box(timeout=3000)
                if b: break
            except Exception:
                time.sleep(0.4)
        else:
            raise RuntimeError("could not find the element to click")
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        self.page.mouse.move(x, y, steps=22); time.sleep(pause); self.page.mouse.down(); time.sleep(0.08); self.page.mouse.up()

    def close(self):
        self.until(self.target)
        v = self.page.video; self.page.close(); self.ctx.close()
        return v.path()


class Recorder:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="ibs-video-")
        self.clips = []

    def serve_stage(self):
        d = os.path.join(self.tmp, "stage"); os.makedirs(d)
        shutil.copy(os.path.join(HERE, "video", "stage.html"), d)
        shutil.copy(os.path.join(ROOT, "docs", "images", "desktop-setup-check.png"), d)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=d)
        handler.log_message = lambda *a, **k: None
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.stage = "http://127.0.0.1:%d/stage.html" % self.httpd.server_address[1]

    def run(self):
        self.serve_stage()
        os.environ["IBS_DEMO"] = "1"
        demo = Server().start()                                      # demo: sample data
        os.environ.pop("IBS_DEMO", None)
        fresh = Server().start()                                     # fresh: a brand new install, no password yet
        dbase, fbase = "http://127.0.0.1:%d" % demo.port, "http://127.0.0.1:%d" % fresh.port
        fresh.phone(name=NAME, ptype="iPhone18,4", version="27.0.1", backup_seconds=13.0)   # demo seeds its own phone state
        api = Api(demo); assert api.post("/api/auth/login", {"password": "demo"})[0] == 200
        wait_for(lambda: api.get("/api/status")[1]["device"]["paired"] is True, 30, msg="the demo phone")
        token = api.cookie.split("=", 1)[1]
        pw = sync_playwright().start()
        self.browser = pw.chromium.launch(channel="chrome", headless=True)
        try:
            for fn in (self.s_title, self.s_why, self.s_how, self.s_install):
                self.clips.append(fn())
            self.clips.append(self.s_first_run(fbase, fresh))
            self.clips.append(self.s_backup(dbase, token, api))
            self.clips.append(self.s_history_schedule(dbase, token))
            self.clips.append(self.s_connection(dbase, token))
            self.clips.append(self.s_phone(dbase, token, api))
            self.clips.append(self.s_ipad(dbase, token))
            self.clips.append(self.s_end())
        finally:
            self.browser.close(); pw.stop(); demo.stop(); fresh.stop(); self.httpd.shutdown()
        return self.clips

    def login(self, scene, base, token):
        scene.ctx.add_cookies([{"name": "ib_session", "value": token, "url": base}])

    # ---------------------------------------------------------------- scenes
    def s_title(self):
        s = Scene(self, "title", 6); s.js("show('title')"); return s.close()

    def s_why(self):
        s = Scene(self, "why", 12); s.js("show('why')")
        for i, t in enumerate((0.6, 4.4, 8.2), 1):
            s.until(t); s.js("reveal('w%d')" % i)
        return s.close()

    def s_how(self):
        s = Scene(self, "how", 12); s.js("show('how')")
        s.until(0.5); s.js("reveal('h1')"); s.until(1.3); s.js("reveal('a1')"); s.until(1.8); s.js("reveal('h2')")
        s.say("NAS 通过 USB 或 Wi-Fi 连上手机，用的是和 iTunes 相同的备份协议", "The NAS talks to the device over USB or Wi-Fi, using the same backup protocol as iTunes")
        s.until(6.0); s.js("reveal('a2')"); s.until(6.5); s.js("reveal('h3')")
        s.say("整个东西是一个 Docker 容器，用浏览器管理", "It is one Docker container, managed from a web page")
        return s.close()

    def s_install(self):
        s = Scene(self, "install", 18); s.js("show('install')")
        s.say("一个脚本安装：推荐剩余空间最大的位置，检查能不能写", "One script installs it: it suggests the disk with the most space and checks it is writable")
        s.page.evaluate("""() => typeTerm(
          '<span class="p">$</span> git clone https://github.com/JerryGHT04/ios-backup-station.git\\n<span class="p">$</span> cd ios-backup-station\\n<span class="p">$</span> ./install.sh\\n', 14)""")
        s.until(5.6)
        s.js("termAppend(%r)" % ('<span class="dim">Places that could hold the backups (most free space first):\n  /volume1/ios-backups   (1185 GB free)</span>\n'
                                '<span class="dim">Which folder should backups be stored in? (full path) [/volume1/ios-backups]:</span> \n'
                                '<span class="ok">Wrote .env: backup location = /volume1/ios-backups, web port = 8899</span>\n'
                                '<span class="dim">The first start builds from source on the NAS: about 5 to 10 minutes...</span>\n'))
        s.until(10.5)
        s.say("没填备份路径就启动，会直接停下并告诉你该填什么，不会悄悄写进容器里", "Start without a backup path and it stops and tells you, instead of silently writing inside the container")
        s.js("termAppend(%r)" % ('\n<span class="p">$</span> docker compose up -d\n<span class="err">required variable BACKUP_LOCATION is missing a value:\nSet BACKUP_LOCATION in .env first: the full path of the NAS folder\nwhere backups are stored (or just run ./install.sh)</span>\n'))
        return s.close()

    def s_first_run(self, fbase, fresh):
        s = Scene(self, "first-run", 18); s.js("layout(false); show('desktop')"); s.js("setFrame('deskf', %r)" % (fbase + "/"))
        f = s.page.frame_locator("#deskf"); expect(f.locator("#lockBox")).to_be_visible(timeout=15000)
        s.say("第一次打开：先设置访问密码，输两遍，提示词可选", "First visit: set a password, typed twice, with an optional hint")
        pwd = f.locator("#lockBox input[type=password]")
        s.move_click(pwd.nth(0)); pwd.nth(0).type("correct horse", delay=55)
        s.move_click(pwd.nth(1)); pwd.nth(1).type("correct horse", delay=55)
        s.move_click(f.get_by_text("添加提示词")); f.locator("#lockBox input[type=text]").type("a famous battery staple", delay=40)
        s.until(8.5); s.move_click(f.get_by_role("button", name=re.compile("设置密码")))
        expect(f.locator("#dlg")).to_be_visible(timeout=15000)
        s.say("再选备份位置：只显示你共享给容器的文件夹，会推荐剩余空间最大的磁盘", "Then pick where backups go: only folders you shared with the container, with the roomiest disk suggested")
        s.until(12.5); s.move_click(f.get_by_role("button", name="使用推荐位置"))
        expect(f.locator("#dlg")).to_be_hidden(timeout=10000)
        return s.close()

    def s_backup(self, dbase, token, api):
        s = Scene(self, "backup", 26); self.login(s, dbase, token)
        s.js("layout(false); show('desktop')"); s.js("setFrame('deskf', %r)" % (dbase + "/?ui=desktop"))
        f = s.page.frame_locator("#deskf"); expect(f.locator("#hTitle")).to_have_text(NAME, timeout=15000)
        s.say("状态条显示连接方式和上次备份的时间", "The status bar shows how it is connected and when the last backup was")
        s.until(3.2); s.move_click(f.locator("#goBackup"))
        expect(f.locator("#prog")).to_be_visible(timeout=10000)
        s.say("进度是整个备份的总进度，还有已用时和剩余时间的估计", "Progress is the whole backup, with elapsed time and an estimate of what is left")
        s.until(9.5); s.move_click(f.locator("#goCancel"))
        s.say("取消要点两次确认，取消后上一次完好的备份不受影响", "Cancelling needs a second tap, and the last good backup stays untouched")
        s.until(12.0); s.move_click(f.locator("#goCancel"))
        expect(f.locator("#lcdT")).to_have_text("备份已取消", timeout=25000)
        s.until(16.0); s.move_click(f.locator("#goBackup")); expect(f.locator("#prog")).to_be_visible(timeout=10000)
        s.say("重新开始，这次让它跑完（真实耗时取决于数据量，第一次建议用 USB）", "Start again and let it finish (real time depends on the data; do the first one over USB)")
        expect(f.locator("#lcdT")).to_have_text("备份完成", timeout=40000)
        return s.close()

    def s_history_schedule(self, dbase, token):
        s = Scene(self, "history-schedule", 26); self.login(s, dbase, token)
        s.js("layout(false); show('desktop')"); s.js("setFrame('deskf', %r)" % (dbase + "/?ui=desktop"))
        f = s.page.frame_locator("#deskf"); expect(f.locator("#hTitle")).to_have_text(NAME, timeout=15000)
        s.say("加密备份：密码保存在手机里，NAS 上不留；第一次备份时选择", "Encrypted backups: the password stays on the device, never on the NAS; you choose at the first backup")
        s.until(5.0); s.move_click(f.locator(".side button", has_text="备份记录"))
        s.say("每次备份都有记录：方式、触发、大小、耗时，失败的写明原因", "Every backup is recorded: way, trigger, size, duration, and the reason when one fails")
        s.until(12.0); s.move_click(f.locator(".side button", has_text="定时备份"))
        s.say("定时备份由 NAS 主动发起，用 NAS 自己的时区", "Scheduled backups are started by the NAS, on the NAS's own time zone")
        s.until(17.0); s.move_click(f.locator("#schD5")); s.move_click(f.locator("#schD6"))
        s.say("手机不在线时在窗口内每 10 分钟重试；锁屏等密码超过 5 分钟就放弃", "If the device is offline it retries every 10 minutes in the window; a locked device waiting over 5 minutes is abandoned")
        return s.close()

    def s_connection(self, dbase, token):
        s = Scene(self, "connection", 20); self.login(s, dbase, token)
        s.js("layout(false); show('desktop')"); s.js("setFrame('deskf', %r)" % (dbase + "/?ui=desktop"))
        f = s.page.frame_locator("#deskf"); expect(f.locator("#hTitle")).to_have_text(NAME, timeout=15000)
        s.move_click(f.locator(".side button", has_text="连接与配对"))
        s.say("配对、开启 Wi-Fi 同步、检测连接，一步一步", "Pairing, turning on Wi-Fi sync and checking the connection, step by step")
        s.until(6.0); s.move_click(f.get_by_text("为什么需要关闭"))
        s.say("iOS 26.4 起，要关闭家里 Wi-Fi 的“私有无线局域网地址”，否则会时有时无", "Since iOS 26.4, turn off Private Wi-Fi Address for your home network or the device comes and goes")
        s.until(14.0); s.js("show('shotscene'); setShot('desktop-setup-check.png')")
        s.say("“环境自检”会提醒你没配好的地方，比如备份文件夹没有真正挂载", "The Setup check warns about problems, such as a backup folder that is not really mounted")
        return s.close()

    def s_phone(self, dbase, token, api):
        s = Scene(self, "phone", 28); self.login(s, dbase, token)
        s.js("layout(true); show('phonescene')"); s.js("setFrame('phonef', %r)" % (dbase + "/m?lang=zh"))
        f = s.page.frame_locator("#phonef"); expect(f.locator("h1.ttl")).to_have_text(NAME, timeout=15000)
        s.say("手机和 iPad 访问会自动用为触屏做的界面", "Phones and iPads automatically get an interface made for touch")
        s.until(4.5); s.move_click(f.locator("#cards .acard").first)
        expect(f.get_by_text("开始备份").first).to_be_visible(timeout=8000)
        s.say("一屏一个重点：点“立即备份”进入，加密、位置、备份记录都在里面", "One thing per screen: Back Up Now opens encryption, location and history")
        s.until(9.0); s.move_click(f.locator("#cards .acard", has_text="开始备份"))
        expect(f.locator("#rpct")).to_be_visible(timeout=10000)
        s.say("备份时图标变成进度环，同样是总进度", "While it runs the icon becomes a progress ring, again showing overall progress")
        s.until(19.0)
        api.post("/api/cancel")
        wait_for(lambda: api.get("/api/status")[1]["job"] is None, 25, msg="the backup to stop")
        s.until(23.0); s.move_click(f.locator("#hdr .cbtn").last); s.move_click(f.get_by_text("语言").first)
        s.say("手机版有简体中文和 English，密码页右上角也能切换", "The phone layout comes in English and Simplified Chinese; the password screen has a switch too")
        return s.close()

    def s_ipad(self, dbase, token):
        s = Scene(self, "ipad", 8); self.login(s, dbase, token)
        s.js("layout(true); show('ipadscene')"); s.js("setFrame('ipadf', %r)" % (dbase + "/m?lang=en"))
        f = s.page.frame_locator("#ipadf"); expect(f.locator("h1.ttl")).to_have_text(NAME, timeout=15000)
        s.say("iPad 上是同一套界面，列更宽", "On an iPad it is the same interface with a wider column")
        return s.close()

    def s_end(self):
        s = Scene(self, "end", 10); s.js("show('end')"); return s.close()


def encode(clips, out):
    """Each clip -> H.264 with a short fade in and out, then concatenate."""
    parts = []
    for i, c in enumerate(clips):
        mp4 = os.path.join(os.path.dirname(c), "clip%02d.mp4" % i)
        info = subprocess.run([FF, "-i", c], capture_output=True, text=True).stderr
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info); dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        subprocess.run([FF, "-y", "-loglevel", "error", "-i", c, "-vf", "fps=25,scale=%d:%d,fade=t=in:st=0:d=0.35,fade=t=out:st=%.2f:d=0.35,format=yuv420p" % (W, H, max(0, dur - 0.35)),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "27", "-an", mp4], check=True)
        parts.append((mp4, dur))
    lst = os.path.join(os.path.dirname(parts[0][0]), "list.txt")
    with open(lst, "w") as f:
        for p, _ in parts: f.write("file '%s'\n" % p)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out], check=True)
    total = sum(d for _, d in parts)
    print("clips: " + ", ".join("%.0fs" % d for _, d in parts)); print("total %.0f s, %.1f MB -> %s" % (total, os.path.getsize(out) / 1e6, out))
    return total


def main():
    rec = Recorder()
    try:
        clips = rec.run()
        encode(clips, os.path.join(OUT, "ios-backup-station-demo.mp4"))
        poster = os.path.join(ROOT, "docs", "images", "video-poster.png")
        subprocess.run([FF, "-y", "-loglevel", "error", "-ss", "78", "-i", os.path.join(OUT, "ios-backup-station-demo.mp4"), "-frames:v", "1", poster], check=True)
    finally:
        keep = os.environ.get("IBS_VIDEO_KEEP")
        if keep: print("temp files kept in", rec.tmp)
        else: shutil.rmtree(rec.tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
