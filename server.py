#!/usr/bin/env python3
import os, sys, threading, webbrowser

ROOT = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

import atelier.web.routes as _routes  # registers all Bottle routes as a side effect
from atelier.web.app import app, PORT, _ThreadedServer

if __name__ == "__main__":
    print(f"Atelier -> http://localhost:{PORT}")
    threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        app.run(host="127.0.0.1", port=PORT, quiet=True, server=_ThreadedServer)
    finally:
        _routes._observer.stop()
        _routes._observer.join()
