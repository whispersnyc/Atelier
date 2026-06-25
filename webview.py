#!/usr/bin/env python3
import subprocess
import sys
import time
import urllib.request
import urllib.error
import os
import webview

PORT = 8767
URL  = f"http://localhost:{PORT}"

ROOT = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))


def _wait_for_server(timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def main():
    server = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server.py")],
        cwd=ROOT,
    )

    try:
        if not _wait_for_server():
            print("Server did not start in time.", file=sys.stderr)
            server.terminate()
            sys.exit(1)

        window = webview.create_window("Atelier", URL, width=1400, height=900)
        webview.start()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
