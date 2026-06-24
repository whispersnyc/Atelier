import os, sys, glob, json, threading, queue, subprocess
from bottle import request, response, static_file

from atelier.web.app import app
from atelier.config import ASSETS, ASSETS_MODS, PAKS, GUI_DIR
from atelier.tools import uat
from atelier.handlers.texture import decode_batch, stage_inject, cmd_export
from atelier.paths import game_rel_for_skin
from atelier.web.browse import (browse, all_char_ids, char_skin_ids, char_name, skin_name,
                                token, game_rel_from_token, all_imported)

# ── static ────────────────────────────────────────────────────────────────────

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
        char_ids = all_char_ids()
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"error": str(e)})
    out = []
    for cid in char_ids:
        skins = char_skin_ids(cid)
        out.append({"char_id": cid, "name": char_name(cid), "skin_count": len(skins)})
    response.content_type = "application/json"
    return json.dumps(out)

# ── skins ─────────────────────────────────────────────────────────────────────

@app.get("/api/skins")
def api_skins():
    cid      = request.query.get("char_id", "")
    skin_ids = char_skin_ids(cid)
    out      = []
    for i, sid in enumerate(skin_ids, 1):
        name  = skin_name(sid)
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
        items = browse(skin_id, subpath)
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"error": str(e)})
    response.content_type = "application/json"
    return json.dumps(items)

# ── preview image ─────────────────────────────────────────────────────────────

@app.get("/api/preview")
def api_preview():
    gr = request.query.get("game_rel", "")
    if gr:
        png = os.path.join(ASSETS, *gr.split("/")) + ".png"
        if os.path.exists(png):
            response.content_type = "image/png"
            with open(png, "rb") as f: return f.read()
    tok = request.query.get("token", "")
    if tok:
        gr = game_rel_from_token(tok)
        if gr:
            png = os.path.join(ASSETS, *gr.split("/")) + ".png"
            if os.path.exists(png):
                response.content_type = "image/png"
                with open(png, "rb") as f: return f.read()
    response.status = 404
    return b""

# ── imported list ─────────────────────────────────────────────────────────────

@app.get("/api/imported")
def api_imported():
    response.content_type = "application/json"
    return json.dumps(all_imported())

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
        gr       = game_rel_for_skin(skin_id, rel)
        dst_base = os.path.join(ASSETS, *gr.split("/"))
        os.makedirs(os.path.dirname(dst_base), exist_ok=True)
        uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS),
             "--filter", os.path.basename(gr)])
        decode_batch([dst_base + ".uasset"])
        png_exists = os.path.exists(dst_base + ".png")
        response.content_type = "application/json"
        return json.dumps({
            "ok":       png_exists,
            "token":    token(gr) if png_exists else None,
            "game_rel": gr,
        })
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

# ── bulk import (job) ─────────────────────────────────────────────────────────

_job      = {"running": False, "current": 0, "total": 0, "name": "",
             "done": False, "error": None, "results": []}
_job_lock = threading.Lock()
_sse_queues: list[queue.Queue] = []
_sse_lock   = threading.Lock()

def _push_sse(data: dict):
    with _sse_lock:
        dead = []
        for q in _sse_queues:
            try: q.put_nowait(data)
            except queue.Full: dead.append(q)
        for q in dead: _sse_queues.remove(q)

def _run_import_job(items):
    """items: [{skin_id, rel_path, game_rel, name}] — extract+decode all via UAssetTool."""
    with _job_lock:
        _job.update(running=True, current=0, total=len(items), name="", done=False, error=None, results=[])
    try:
        names = sorted({os.path.basename(it["game_rel"]) for it in items})
        _push_sse({"current": 0, "total": len(items), "name": "Extracting from game…", "done": False})
        uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS), "--filter"] + names)

        _push_sse({"current": 0, "total": len(items), "name": "Decoding…", "done": False})
        uassets = [os.path.join(ASSETS, *it["game_rel"].split("/")) + ".uasset" for it in items]
        decode_batch([u for u in uassets if os.path.exists(u)])

        results = []; current = 0
        for it in items:
            dst_base = os.path.join(ASSETS, *it["game_rel"].split("/"))
            current += 1
            if os.path.exists(dst_base + ".png"):
                results.append({"name": it["name"], "token": token(it["game_rel"]),
                                 "game_rel": it["game_rel"]})
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

@app.post("/api/import_all")
def api_import_all():
    body  = request.json or {}
    items = body.get("items", [])
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

@app.get("/api/import_status")
def api_import_status():
    with _job_lock:
        snap = dict(_job)
    response.content_type = "application/json"
    return json.dumps(snap)

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

# ── file change watcher ───────────────────────────────────────────────────────

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class _PNGHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".png"):
            gr = os.path.relpath(event.src_path[:-4], ASSETS).replace("\\", "/")
            _push_sse({"file_changed": True, "token": token(gr), "game_rel": gr})
    def on_created(self, event):
        self.on_modified(event)

os.makedirs(ASSETS, exist_ok=True)
_observer = Observer()
_observer.schedule(_PNGHandler(), ASSETS, recursive=True)
_observer.start()

# ── open in explorer ──────────────────────────────────────────────────────────

@app.get("/api/open_explorer")
def api_open_explorer():
    path = request.query.get("path", "")
    gr   = request.query.get("game_rel", "")
    if gr:
        path = os.path.join(ASSETS, *gr.split("/")) + ".png"
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
    items    = body.get("items", [])
    if not items:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "no items selected"})

    out_dir = ASSETS_MODS
    os.makedirs(out_dir, exist_ok=True)

    try:
        cmd_export(mod_name, items, out_dir, force=True)
        stem = f"{mod_name}_9999999_P"
        utoc = os.path.join(out_dir, stem + ".utoc")
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
    gr   = body.get("game_rel", "")
    if not gr:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing game_rel"})
    base = os.path.join(ASSETS, *gr.split("/"))
    for ext in (".png", ".uasset", ".uexp", ".ubulk"):
        p = base + ext
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
    response.content_type = "application/json"
    return json.dumps({"ok": True})

@app.post("/api/delete_all_imported")
def api_delete_all_imported():
    items = all_imported()
    for item in items:
        base = os.path.join(ASSETS, *item["game_rel"].split("/"))
        for ext in (".png", ".uasset", ".uexp", ".ubulk"):
            p = base + ext
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
    response.content_type = "application/json"
    return json.dumps({"ok": True, "deleted": len(items)})
