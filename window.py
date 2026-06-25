#!/usr/bin/env python3
import sys
import time
import threading
import urllib.request
import os
import webview

PORT = 8767
URL  = f"http://localhost:{PORT}"


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
