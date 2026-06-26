#!/usr/bin/env python3
import sys
import time
import threading
import urllib.request
import os
import datetime
import webview

PORT = 8767
URL  = f"http://localhost:{PORT}"

_ROOT = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
         else os.path.dirname(os.path.abspath(__file__)))


def _setup_logging():
    logs_dir = os.path.join(_ROOT, "_logs")
    os.makedirs(logs_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    lf = open(os.path.join(logs_dir, f"{ts}.txt"), "w", encoding="utf-8", buffering=1)

    orig_out, orig_err = sys.stdout, sys.stderr

    class _Tee:
        def __init__(self, orig):
            self._orig = orig
        def write(self, data):
            lf.write(data)
            if self._orig:
                try: self._orig.write(data)
                except Exception: pass
        def flush(self):
            lf.flush()
            if self._orig:
                try: self._orig.flush()
                except Exception: pass
        def fileno(self):
            return lf.fileno()

    sys.stdout = _Tee(orig_out)
    sys.stderr = _Tee(orig_err)


def _wait_for_server(timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def _run_server():
    import atelier.web.routes as _routes  # registers all Bottle routes
    from atelier.web.app import app, PORT as _PORT, _ThreadedServer
    try:
        app.run(host="127.0.0.1", port=_PORT, quiet=True, server=_ThreadedServer)
    finally:
        _routes._observer.stop()
        _routes._observer.join()


def main():
    _setup_logging()

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()

    if not _wait_for_server():
        print("Server did not start in time.", file=sys.stderr)
        sys.exit(1)

    window = webview.create_window("Atelier", URL, width=1400, height=900)
    webview.start(debug=False, gui='edgechromium')
    os._exit(0)


if __name__ == "__main__":
    main()
