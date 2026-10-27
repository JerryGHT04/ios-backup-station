#!/usr/bin/env python3
"""Try the web interface without a phone: the real server, a fake phone and sample data.

    python3 tests/run_demo_server.py [PORT]        # default 8899, password: demo
    open http://127.0.0.1:8899

Everything lives in a temporary folder that is deleted when you press Ctrl+C. Nothing else is touched.
"""
import os, shutil, subprocess, sys, tempfile

port = sys.argv[1] if len(sys.argv) > 1 else "8899"
work = tempfile.mkdtemp(prefix="ibs-demo-")
env = dict(os.environ, IBS_DEMO="1")
print("Demo on http://127.0.0.1:%s   password: demo   (Ctrl+C to stop)" % port, flush=True)
try:
    subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_fake_server.py"), port, work], env=env)
except KeyboardInterrupt:
    pass
finally:
    shutil.rmtree(work, ignore_errors=True)
