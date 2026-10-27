"""Shared helpers for the tests: start/stop the fake-phone server, talk to its API, change the fake phone."""
import http.client, json, os, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
UDID = "00008999-0000000000AB001E"
SECOND_UDID = "00008103-000A11B222C3D04E"


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Server:
    def __init__(self, work=None, keep=False):
        self.work = work or tempfile.mkdtemp(prefix="ibs-test-")
        self.keep = keep
        self.port = free_port()
        self.proc = None
        self.logf = None

    def start(self):
        self.logf = open(os.path.join(self.work, "server.log"), "ab")
        self.proc = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "run_fake_server.py"), str(self.port), self.work],
                                     stdout=self.logf, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close(); return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try: self.proc.wait(5)
            except subprocess.TimeoutExpired: self.proc.kill()
        if self.logf: self.logf.close()
        if not self.keep: shutil.rmtree(self.work, ignore_errors=True)

    def log(self):
        with open(os.path.join(self.work, "server.log"), errors="replace") as f:
            return f.read()

    # ---- fake phone
    @property
    def phone_file(self): return os.path.join(self.work, "fake", "phone.json")

    def phone(self, **kw):
        try:
            with open(self.phone_file) as f: s = json.load(f)
        except (OSError, ValueError): s = {}
        s.update(kw)
        tmp = self.phone_file + ".w"
        with open(tmp, "w") as f: json.dump(s, f)
        os.replace(tmp, self.phone_file)

    def phone_state(self):
        with open(self.phone_file) as f: return json.load(f)


class Api:
    """A tiny HTTP client with a cookie jar."""
    def __init__(self, server, ua="Mozilla/5.0 (X11; Linux x86_64) Chrome/120"):
        self.s, self.cookie, self.ua = server, None, ua

    def call(self, method, path, body=None, raw=None, headers=None, cookie=True):
        c = http.client.HTTPConnection("127.0.0.1", self.s.port, timeout=20)
        h = {"User-Agent": self.ua}
        if headers: h.update(headers)
        if cookie and self.cookie: h["Cookie"] = self.cookie
        data = raw
        if body is not None:
            data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse(); txt = r.read()
        sc = r.getheader("Set-Cookie")
        if sc and cookie and "ib_session=" in sc:
            v = sc.split(";")[0]
            self.cookie = v if v != "ib_session=" else None
        try: j = json.loads(txt)
        except ValueError: j = txt
        c.close()
        return r.status, j, r

    def get(self, path, **kw): return self.call("GET", path, **kw)
    def post(self, path, body=None, **kw): return self.call("POST", path, body if body is not None else {}, **kw)


def wait_for(fn, timeout=15, step=0.3, msg="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last: return last
        except Exception as e:
            last = e
        time.sleep(step)
    raise AssertionError("timed out waiting for %s (last: %r)" % (msg, last))
