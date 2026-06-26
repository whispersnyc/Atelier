#!/usr/bin/env python3
import sys
import time
import threading
import urllib.request
import os
import datetime
import subprocess
import webview

PORT = 8767
URL  = f"http://localhost:{PORT}"

_ROOT = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
         else os.path.dirname(os.path.abspath(__file__)))


def _show_launch_toast():
    try:
        with open(os.path.join(_ROOT, "version"), "r") as f:
            version = f.read().strip()
    except Exception:
        version = ""
    label = f"Atelier {version}" if version else "Atelier"
    xml = (
        f'<toast duration="short"><visual><binding template="ToastText02">'
        f'<text id="1">{label}</text>'
        f'<text id="2">Launching, please wait...</text>'
        f'</binding></visual></toast>'
    )
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]|Out-Null;"
        f"$x=[Windows.Data.Xml.Dom.XmlDocument]::new();"
        f"$x.LoadXml('{xml}');"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{label}').Show("
        f"[Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-NoProfile", "-Command", ps],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


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
    _show_launch_toast()
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
