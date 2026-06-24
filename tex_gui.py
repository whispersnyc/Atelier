#!/usr/bin/env python3
"""tex_gui.py - Bottle.py web GUI for tex_cli.py  (python tex_gui.py to run)."""
import os, sys, re, json, glob, threading, time, queue, shutil, hashlib, struct, io, base64, webbrowser
ROOT = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# robust console: a frozen exe's stdout is cp1252 and crashes on non-ASCII (— … →) — make it UTF-8/replace
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

import tex_cli
from bottle import Bottle, request, response, static_file, ServerAdapter
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from socketserver import ThreadingMixIn
import subprocess

# threading WSGI server so SSE connections don't block all other routes
class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

class _ThreadedServer(ServerAdapter):
    def run(self, handler):
        srv = make_server(self.host, self.port, handler,
                          server_class=_ThreadingWSGIServer,
                          handler_class=WSGIRequestHandler)
        srv.serve_forever()

GUI_DIR  = os.path.join(getattr(sys, "_MEIPASS", ROOT), "gui")   # bundled gui/ when frozen, else local
PORT     = 8767
app      = Bottle()

# ─── character / skin name table ─────────────────────────────────────────────

def _parse_char_md():
    """Parse MarvelRivalsCharacterIDs.md -> {char_id: {name, skins:{skin_id:name}}}"""
    path = os.path.join(ROOT, "Tools", "MarvelRivalsCharacterIDs.md")
    chars = {}
    cur = None
    try:
        for line in open(path, encoding="utf-8"):
            # Row with char_id:  | 1029 | Magik | 1029100 | AMETHEYST ARMOR |
            m = re.match(r'\|\s*(\d{4})\s*\|\s*([^|]+?)\s*\|(?:\s*(\d{7})\s*\|\s*([^|]*?)\s*\|)?', line)
            if m and m.group(1):
                cur = m.group(1)
                name = m.group(2).strip()
                if name and name.upper() != "NAME":
                    chars.setdefault(cur, {"name": name, "skins": {}})
                    if m.group(3):
                        chars[cur]["skins"][m.group(3)] = (m.group(4) or "").strip()
                continue
            # Continuation row:  | | | 1029101 | WILL OF GALACTA |
            m2 = re.match(r'\|\s*\|\s*\|\s*(\d{7})\s*\|\s*([^|]*?)\s*\|', line)
            if m2 and cur and cur in chars:
                chars[cur]["skins"][m2.group(1)] = m2.group(2).strip()
    except Exception:
        pass
    return chars

_CHAR_DATA = _parse_char_md()   # {char_id: {name, skins}}

def _char_name(char_id):
    return _CHAR_DATA.get(char_id, {}).get("name") or f"Character {char_id}"

def _skin_name(skin_id):
    char_id = tex_cli._char_id(skin_id)
    return _CHAR_DATA.get(char_id, {}).get("skins", {}).get(skin_id) or skin_id

# ─── index helpers ────────────────────────────────────────────────────────────

def _all_char_ids():
    """Sorted list of char_ids present in the pak index."""
    idx = tex_cli._ensure_index()
    seen = set()
    for p, _ in idx:
        m = re.search(r"/Characters/(\d{4})/", p)
        if m: seen.add(m.group(1))
    return sorted(seen)

def _char_skin_ids(char_id):
    """Sorted list of skin_ids for a character present in the pak index."""
    idx = tex_cli._ensure_index()
    seen = set()
    needle = f"/Characters/{char_id}/".lower()
    for p, _ in idx:
        pl = p.lower()
        i = pl.find(needle)
        if i < 0: continue
        rest = pl[i + len(needle):]
        sid = rest.split("/")[0]
        if re.match(r"^\d{7}$", sid): seen.add(sid)
    return sorted(seen)

# ─── virtual folder tree ──────────────────────────────────────────────────────

def _browse(skin_id, subpath=""):
    """Return immediate children of `subpath` inside `skin_id`."""
    entries = tex_cli._skin_entries(skin_id)
    subpath = subpath.strip("/")
    prefix  = (subpath + "/") if subpath else ""

    folders  = {}   # name -> rel_path
    textures = {}   # name -> {rel_path, game_rel}

    for pak_path, _cont in entries:
        rel = tex_cli._skin_rel(pak_path, skin_id)   # e.g. "Textures/T_foo"
        if not rel.lower().startswith(prefix.lower()):
            continue
        rest = rel[len(prefix):]
        if not rest:
            continue
        if "/" in rest:
            folder_name = rest.split("/")[0]
            folder_path = (prefix + folder_name).strip("/")
            folders[folder_name] = folder_path
        else:
            game_rel = tex_cli._game_rel_for_skin(skin_id, (prefix + rest).strip("/"))
            textures[rest] = {"rel_path": (prefix + rest).strip("/"), "game_rel": game_rel}

    result = []
    for name in sorted(folders, key=str.lower):
        result.append({"type": "folder", "name": name, "rel_path": folders[name]})
    for name in sorted(textures, key=str.lower):
        td       = textures[name]
        base     = os.path.join(tex_cli.ASSETS, *td["game_rel"].split("/"))
        imported = os.path.exists(base + ".png")
        token    = _token(td["game_rel"]) if imported else None
        result.append({
            "type":     "texture",
            "name":     name,
            "rel_path": td["rel_path"],
            "game_rel": td["game_rel"],
            "imported": imported,
            "token":    token,
        })
    return result

def _token(game_rel):
    return hashlib.md5(game_rel.encode()).hexdigest()[:20]

def _game_rel_from_token(token):
    """Reverse-lookup game_rel from a token by scanning the assets PNG tree."""
    for root, _, files in os.walk(tex_cli.ASSETS):
        for f in files:
            if not f.endswith(".png"): continue
            gr = os.path.relpath(os.path.join(root, f[:-4]), tex_cli.ASSETS).replace("\\", "/")
            if _token(gr) == token:
                return gr
    return None

# ─── all-imported listing ────────────────────────────────────────────────────

def _all_imported():
    """Walk assets/ and return every imported texture that has a .png."""
    items = []
    chars_root = os.path.join(tex_cli.ASSETS, "Marvel", "Content", "Marvel", "Characters")
    if not os.path.isdir(chars_root):
        return items
    for char_id in sorted(os.listdir(chars_root)):
        char_dir = os.path.join(chars_root, char_id)
        if not os.path.isdir(char_dir): continue
        for skin_id in sorted(os.listdir(char_dir)):
            skin_dir = os.path.join(char_dir, skin_id)
            if not os.path.isdir(skin_dir): continue
            for dirpath, _, files in os.walk(skin_dir):
                for fname in sorted(files):
                    if not fname.endswith(".png"): continue
                    tex_name = fname[:-4]
                    abs_png  = os.path.join(dirpath, fname)
                    game_rel = os.path.relpath(abs_png[:-4], tex_cli.ASSETS).replace("\\", "/")
                    items.append({
                        "token":     _token(game_rel),
                        "game_rel":  game_rel,
                        "name":      tex_name,
                        "skin_id":   skin_id,
                        "char_id":   char_id,
                        "char_name": _char_name(char_id),
                        "skin_name": _skin_name(skin_id),
                        "mtime":     int(os.path.getmtime(abs_png)),
                    })
    return items

# ─── import job ───────────────────────────────────────────────────────────────

_job = {
    "running": False,
    "current": 0,
    "total":   0,
    "name":    "",
    "done":    False,
    "error":   None,
    "results": [],   # [{name, token, game_rel}]
}
_job_lock = threading.Lock()
_sse_queues = []   # list of queue.Queue
_sse_lock = threading.Lock()

def _push_sse(data: dict):
    with _sse_lock:
        dead = []
        for q in _sse_queues:
            try: q.put_nowait(data)
            except queue.Full: dead.append(q)
        for q in dead: _sse_queues.remove(q)

def _run_import_job(items):
    """items: [{skin_id, rel_path, game_rel, name}] — extract+decode all via UAssetTool (one extract call,
    then a parallel batch decode through the persistent worker)."""
    with _job_lock:
        _job.update(running=True, current=0, total=len(items), name="", done=False, error=None, results=[])
    try:
        names = sorted({os.path.basename(it["game_rel"]) for it in items})
        _push_sse({"current": 0, "total": len(items), "name": "Extracting from game…", "done": False})
        tex_cli._uat(["extract_iostore_legacy", tex_cli.PAKS, os.path.abspath(tex_cli.ASSETS), "--filter"] + names)

        _push_sse({"current": 0, "total": len(items), "name": "Decoding…", "done": False})
        uassets = [os.path.join(tex_cli.ASSETS, *it["game_rel"].split("/")) + ".uasset" for it in items]
        tex_cli._decode_batch([u for u in uassets if os.path.exists(u)])

        results = []; current = 0
        for it in items:
            dst_base = os.path.join(tex_cli.ASSETS, *it["game_rel"].split("/"))
            current += 1
            if os.path.exists(dst_base + ".png"):
                results.append({"name": it["name"], "token": _token(it["game_rel"]), "game_rel": it["game_rel"]})
            with _job_lock:
                _job.update(current=current, name=it["name"])
            _push_sse({"current": current, "total": len(items), "name": it["name"], "done": False})
    except Exception as e:
        with _job_lock:
            _job.update(running=False, done=True, error=str(e))
        _push_sse({"done": True, "error": str(e), "results": []})
        return

    with _job_lock:
        _job.update(running=False, done=True, results=results)
    _push_sse({"current": current, "total": len(items), "name": "", "done": True, "results": results})

# ─── file change watcher ──────────────────────────────────────────────────────

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class _PNGHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".png"):
            gr = os.path.relpath(event.src_path[:-4], tex_cli.ASSETS).replace("\\", "/")
            _push_sse({"file_changed": True, "token": _token(gr), "game_rel": gr})
    def on_created(self, event):
        self.on_modified(event)

os.makedirs(tex_cli.ASSETS, exist_ok=True)
_observer = Observer()
_observer.schedule(_PNGHandler(), tex_cli.ASSETS, recursive=True)
_observer.start()

# ─── bottle routes ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return static_file("index.html", root=GUI_DIR)

@app.route("/static/<path:path>")
def static(path):
    return static_file(path, root=GUI_DIR)

# ── characters ────────────────────────────────────────────────────────────────

@app.get("/api/characters")
def api_characters():
    try:
        char_ids = _all_char_ids()
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"error": str(e)})
    out = []
    for cid in char_ids:
        skins = _char_skin_ids(cid)
        out.append({"char_id": cid, "name": _char_name(cid), "skin_count": len(skins)})
    response.content_type = "application/json"
    return json.dumps(out)

# ── skins ─────────────────────────────────────────────────────────────────────

@app.get("/api/skins")
def api_skins():
    char_id = request.query.get("char_id", "")
    skin_ids = _char_skin_ids(char_id)
    out = []
    for i, sid in enumerate(skin_ids, 1):
        name = _skin_name(sid)
        label = f"{i:03d} - {name}" if name != sid else f"{i:03d} - {sid}"
        if i == 1 and not name.upper().startswith("DEFAULT"):
            label_display = f"001 - Default ({name})"
        else:
            label_display = label
        out.append({"skin_id": sid, "name": name, "label": label_display if i == 1 else label})
    response.content_type = "application/json"
    return json.dumps(out)

# ── browse ────────────────────────────────────────────────────────────────────

@app.get("/api/browse")
def api_browse():
    skin_id = request.query.get("skin_id", "")
    subpath = request.query.get("path", "")
    try:
        items = _browse(skin_id, subpath)
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"error": str(e)})
    response.content_type = "application/json"
    return json.dumps(items)

# ── preview image ─────────────────────────────────────────────────────────────

@app.get("/api/preview")
def api_preview():
    token = request.query.get("token", "")
    # Fast path: check known game_rel directly
    gr = request.query.get("game_rel", "")
    if gr:
        png = os.path.join(tex_cli.ASSETS, *gr.split("/")) + ".png"
        if os.path.exists(png):
            response.content_type = "image/png"
            with open(png, "rb") as f: return f.read()
    # Slow path: scan by token
    if token:
        gr = _game_rel_from_token(token)
        if gr:
            png = os.path.join(tex_cli.ASSETS, *gr.split("/")) + ".png"
            if os.path.exists(png):
                response.content_type = "image/png"
                with open(png, "rb") as f: return f.read()
    response.status = 404
    return b""

# ── imported list ─────────────────────────────────────────────────────────────

@app.get("/api/imported")
def api_imported():
    response.content_type = "application/json"
    return json.dumps(_all_imported())

# ── single import ─────────────────────────────────────────────────────────────

@app.post("/api/import")
def api_import_one():
    body    = request.json or {}
    skin_id = body.get("skin_id", "")
    rel     = body.get("rel_path", "")
    if not skin_id or not rel:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing skin_id or rel_path"})
    try:
        game_rel = tex_cli._game_rel_for_skin(skin_id, rel)
        dst_base = os.path.join(tex_cli.ASSETS, *game_rel.split("/"))
        os.makedirs(os.path.dirname(dst_base), exist_ok=True)
        tex_cli._uat(["extract_iostore_legacy", tex_cli.PAKS, os.path.abspath(tex_cli.ASSETS),
                      "--filter", os.path.basename(game_rel)])
        tex_cli._decode_batch([dst_base + ".uasset"])
        png_exists = os.path.exists(dst_base + ".png")
        response.content_type = "application/json"
        return json.dumps({
            "ok":      png_exists,
            "token":   _token(game_rel) if png_exists else None,
            "game_rel": game_rel,
        })
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

# ── bulk import (start job) ───────────────────────────────────────────────────

@app.post("/api/import_all")
def api_import_all():
    body = request.json or {}
    items = body.get("items", [])   # [{skin_id, rel_path, game_rel, name}]
    if not items:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "no items"})
    with _job_lock:
        if _job["running"]:
            response.content_type = "application/json"
            return json.dumps({"ok": False, "error": "job already running"})
    threading.Thread(target=_run_import_job, args=(items,), daemon=True).start()
    response.content_type = "application/json"
    return json.dumps({"ok": True, "total": len(items)})

# ── SSE stream (import progress + file changes) ───────────────────────────────

@app.get("/api/events")
def api_events():
    q = queue.Queue(maxsize=128)
    with _sse_lock:
        _sse_queues.append(q)

    def generate():
        yield "retry: 1000\n\n"
        try:
            while True:
                try:
                    data = q.get(timeout=20)
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            with _sse_lock:
                try: _sse_queues.remove(q)
                except ValueError: pass

    response.content_type = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return generate()

# ── import job status (polling fallback) ──────────────────────────────────────

@app.get("/api/import_status")
def api_import_status():
    with _job_lock:
        snap = dict(_job)
    response.content_type = "application/json"
    return json.dumps(snap)

# ── open in explorer ──────────────────────────────────────────────────────────

@app.get("/api/open_explorer")
def api_open_explorer():
    path     = request.query.get("path", "")
    game_rel = request.query.get("game_rel", "")
    if game_rel:
        path = os.path.join(tex_cli.ASSETS, *game_rel.split("/")) + ".png"
    if path:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            subprocess.Popen(["explorer.exe", f"/select,{abs_path}"])
        elif os.path.isdir(os.path.dirname(abs_path)):
            subprocess.Popen(["explorer.exe", os.path.dirname(abs_path)])
    response.content_type = "application/json"
    return json.dumps({"ok": True})

# ── export ────────────────────────────────────────────────────────────────────

@app.post("/api/export")
def api_export():
    body     = request.json or {}
    mod_name = (body.get("mod_name") or "TextureMod").strip() or "TextureMod"
    items    = body.get("items", [])   # list of game_rel strings
    if not items:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "no items selected"})

    out_dir  = tex_cli.ASSETS_MODS
    os.makedirs(out_dir, exist_ok=True)

    try:
        tex_cli.cmd_export(mod_name, items, out_dir, force=True)
        # Find the produced pak
        stem   = f"{mod_name}_9999999_P"
        utoc   = os.path.join(out_dir, stem + ".utoc")
        if not os.path.exists(utoc):
            made = sorted(glob.glob(os.path.join(out_dir, "*_P.utoc")))
            utoc = made[-1] if made else None
        pak = utoc[:-5] + ".pak" if utoc else None
        response.content_type = "application/json"
        return json.dumps({"ok": bool(pak), "pak_path": pak.replace("\\", "/") if pak else None})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

# ── delete imported ───────────────────────────────────────────────────────────

@app.post("/api/delete_imported")
def api_delete_imported():
    body = request.json or {}
    game_rel = body.get("game_rel", "")
    if not game_rel:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing game_rel"})
    base = os.path.join(tex_cli.ASSETS, *game_rel.split("/"))
    for ext in (".png", ".uasset", ".uexp", ".ubulk"):
        p = base + ext
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
    response.content_type = "application/json"
    return json.dumps({"ok": True})

@app.post("/api/delete_all_imported")
def api_delete_all_imported():
    items = _all_imported()
    for item in items:
        base = os.path.join(tex_cli.ASSETS, *item["game_rel"].split("/"))
        for ext in (".png", ".uasset", ".uexp", ".ubulk"):
            p = base + ext
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
    response.content_type = "application/json"
    return json.dumps({"ok": True, "deleted": len(items)})

# ── main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Atelier Texture GUI -> http://localhost:{PORT}")
    threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        app.run(host="127.0.0.1", port=PORT, quiet=True, server=_ThreadedServer)
    finally:
        _observer.stop()
        _observer.join()
