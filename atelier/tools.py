import os, json, subprocess, threading, atexit
from atelier.config import TOOLS, CNW, ROOT

UAT = os.path.join(TOOLS, "UAssetTool.exe")

def uat(args):
    """Run UAssetTool (one-shot). Pass ABSOLUTE paths — it requires them for output."""
    return subprocess.run([UAT] + args, capture_output=True, text=True, cwd=ROOT,
                          creationflags=CNW)

_proc = None
_lock = threading.Lock()

def uat_json(req):
    """Send one line-delimited JSON request to the persistent UAssetTool worker.
    Reusing one process keeps batch decode fast (startup paid once, parallel across all cores)."""
    global _proc
    with _lock:
        if _proc is None or _proc.poll() is not None:
            _proc = subprocess.Popen([UAT], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, cwd=ROOT, creationflags=CNW,
                                     text=True, encoding="utf-8")
        _proc.stdin.write(json.dumps(req) + "\n"); _proc.stdin.flush()
        # Drain lines until the JSON reply (UAssetTool also writes human-readable status to stdout).
        while True:
            line = _proc.stdout.readline()
            if line == "":
                return {"success": False, "message": "UAssetTool worker closed unexpectedly"}
            s = line.strip()
            if s.startswith("{") and s.endswith("}"):
                try:
                    d = json.loads(s)
                    if isinstance(d, dict) and ("success" in d or "data" in d): return d
                except Exception: pass

@atexit.register
def _shutdown():
    if _proc and _proc.poll() is None:
        try: _proc.terminate()
        except Exception: pass
