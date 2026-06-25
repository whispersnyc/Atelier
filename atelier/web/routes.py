import os, sys, glob, json, shutil, threading, queue, subprocess
from bottle import request, response, static_file

from atelier.web.app import app
from atelier.config import (ASSETS, IMPORT_ROOT, WORK_IMPORT_ROOT, ASSETS_MODS, PAKS, GUI_DIR, _CACHE,
                            get_prereq_status, CONFIG_HAS_PAKS, paks_suggestion, save_paks_config)

THUMBS_DIR = os.path.join(_CACHE, "thumbs")
from atelier.tools import uat
from atelier.handlers.texture import decode_batch, stage_inject, build_mod, decode_thumb
from atelier.handlers.pak_thumb import decode_thumb_from_pak
from atelier.handlers.material import mat_json, is_material, read_material, save_material, reset_material
from atelier.handlers.vfx import read_vfx, is_vfx
from atelier.paths import game_rel_for_skin, pak_game_path
from atelier.web.browse import (browse_dispatch, token, game_rel_from_token, all_imported)
import atelier.web.browse as _browse_mod

# ── extraction helpers ────────────────────────────────────────────────────────

def _import_base(game_rel):
    """Full disk path (no ext) for a game_rel in the import structure (png/json live here)."""
    return os.path.join(IMPORT_ROOT, *game_rel.split("/"))

def _cache_import_base(game_rel):
    """Full disk path (no ext) for a game_rel in _cache/import (uasset/uexp/ubulk live here)."""
    return os.path.join(WORK_IMPORT_ROOT, *game_rel.split("/"))

def _pak_extract_base(game_rel):
    """Where extract_iostore_legacy puts the file (under ASSETS at pak game path)."""
    return os.path.join(ASSETS, *pak_game_path(game_rel).split("/"))

def _relocate_to_import(game_rel):
    """Move .uasset/.uexp/.ubulk from pak extraction location to _cache/import structure."""
    src_base = _pak_extract_base(game_rel)
    dst_base = _cache_import_base(game_rel)
    os.makedirs(os.path.dirname(dst_base), exist_ok=True)
    for ext in (".uasset", ".uexp", ".ubulk"):
        src = src_base + ext
        if os.path.exists(src):
            shutil.move(src, dst_base + ext)
    # Remove empty source directories left behind after the move
    assets_root = os.path.abspath(ASSETS)
    src_dir = os.path.abspath(os.path.dirname(src_base))
    while src_dir.startswith(assets_root) and src_dir != assets_root:
        try:
            os.rmdir(src_dir)
        except OSError:
            break
        src_dir = os.path.dirname(src_dir)

# ── static ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return static_file("index.html", root=GUI_DIR)

@app.route("/static/<path:path>")
def static(path):
    r = static_file(path, root=GUI_DIR)
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return r

# ── prereqs ───────────────────────────────────────────────────────────────────

@app.get("/api/prereqs")
def api_prereqs():
    response.content_type = "application/json"
    return json.dumps(get_prereq_status())

# ── first-run setup ───────────────────────────────────────────────────────────

@app.get("/api/setup_status")
def api_setup_status():
    import atelier.config as _c
    configured = bool(_c._load_config().get("paks"))
    suggestion = "" if configured else _c.paks_suggestion()
    response.content_type = "application/json"
    return json.dumps({"configured": configured, "suggestion": suggestion})

@app.post("/api/pick_folder")
def api_pick_folder():
    body    = request.json or {}
    initial = (body.get("initial") or "").replace("/", "\\")
    env     = os.environ.copy()
    env["PAKS_INITIAL"] = initial
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = 'Select the Marvel Rivals Paks folder'; "
        "$f.SelectedPath = $env:PAKS_INITIAL; "
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
    )
    try:
        r    = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                              capture_output=True, text=True, timeout=120, env=env)
        path = r.stdout.strip().replace("\\", "/")
        response.content_type = "application/json"
        return json.dumps({"ok": True, "path": path})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "path": "", "error": str(e)})

@app.get("/api/validate_paks")
def api_validate_paks():
    path = request.query.get("path", "").strip()
    response.content_type = "application/json"
    if not path:
        return json.dumps({"status": "empty"})
    norm = path.replace("\\", "/").rstrip("/")
    if not norm.lower().endswith("marvelgame/marvel/content/paks"):
        return json.dumps({"status": "wrong_folder"})
    if not os.path.isdir(norm):
        return json.dumps({"status": "missing"})
    return json.dumps({"status": "ok"})

def _validate_paks_path(path):
    norm = path.replace("\\", "/").rstrip("/")
    if not norm.lower().endswith("marvelgame/marvel/content/paks"):
        return "Path must end with MarvelGame/Marvel/Content/Paks"
    if not os.path.isdir(norm):
        return "Directory does not exist"
    return None

@app.post("/api/save_paks")
def api_save_paks():
    body = request.json or {}
    path = body.get("path", "").strip()
    if not path:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "no path provided"})
    err = _validate_paks_path(path)
    if err:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": err})
    try:
        save_paks_config(path)
        def _restart():
            import time; time.sleep(0.4)
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable])
            else:
                subprocess.Popen([sys.executable] + sys.argv)
            os._exit(0)
        threading.Thread(target=_restart, daemon=True).start()
        response.content_type = "application/json"
        return json.dumps({"ok": True})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

# ── browse (unified) ──────────────────────────────────────────────────────────

@app.get("/api/browse")
def api_browse():
    path = request.query.get("path", "")
    try:
        items = browse_dispatch(path)
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
        png = _import_base(gr) + ".png"
        if os.path.exists(png):
            response.content_type = "image/png"
            with open(png, "rb") as f: return f.read()
    tok = request.query.get("token", "")
    if tok:
        gr = game_rel_from_token(tok)
        if gr:
            png = _import_base(gr) + ".png"
            if os.path.exists(png):
                response.content_type = "image/png"
                with open(png, "rb") as f: return f.read()
    response.status = 404
    return b""

# ── thumbnail (low-mip preview, no import required) ──────────────────────────

@app.get("/api/thumb")
def api_thumb():
    game_rel = request.query.get("game_rel", "")
    if not game_rel:
        response.status = 404; return b""
    thumb    = os.path.join(THUMBS_DIR, *game_rel.split("/")) + ".png"
    full_png = _import_base(game_rel) + ".png"
    if os.path.exists(thumb):
        response.content_type = "image/png"
        with open(thumb, "rb") as f: return f.read()
    if os.path.exists(full_png):
        response.content_type = "image/png"
        with open(full_png, "rb") as f: return f.read()
    response.status = 404
    return b""

_prefetch_gen      = 0
_prefetch_gen_lock = threading.Lock()

@app.post("/api/prefetch_thumbs")
def api_prefetch_thumbs():
    global _prefetch_gen
    body = request.json or {}
    game_rels = [gr for gr in body.get("game_rels", []) if gr]
    if not game_rels:
        response.content_type = "application/json"
        return json.dumps({"ok": True, "cached": [], "count": 0})

    with _prefetch_gen_lock:
        _prefetch_gen += 1
        my_gen = _prefetch_gen

    cached, pending = [], []
    for gr in game_rels:
        thumb    = os.path.join(THUMBS_DIR, *gr.split("/")) + ".png"
        full_png = _import_base(gr) + ".png"
        if os.path.exists(thumb) or os.path.exists(full_png):
            cached.append(gr)
        else:
            pending.append(gr)

    def _run():
        if not pending: return
        for gr in pending:
            with _prefetch_gen_lock:
                if _prefetch_gen != my_gen:
                    return
            thumb = os.path.join(THUMBS_DIR, *gr.split("/")) + ".png"
            if not os.path.exists(thumb):
                png = decode_thumb_from_pak(gr)
                if png:
                    os.makedirs(os.path.dirname(thumb), exist_ok=True)
                    with open(thumb, "wb") as f:
                        f.write(png)
                else:
                    # fallback: extract via UAssetTool if pak decode unsupported
                    uasset = _cache_import_base(gr) + ".uasset"
                    if not os.path.exists(uasset):
                        names = [os.path.basename(pak_game_path(gr))]
                        uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS), "--filter"] + names)
                        _relocate_to_import(gr)
                    if os.path.exists(uasset):
                        decode_thumb(uasset, thumb)
            if os.path.exists(thumb):
                _push_sse({"thumb_ready": True, "game_rel": gr})

    threading.Thread(target=_run, daemon=True).start()
    response.content_type = "application/json"
    return json.dumps({"ok": True, "cached": cached, "count": len(pending)})

# ── imported list ─────────────────────────────────────────────────────────────

@app.get("/api/imported")
def api_imported():
    response.content_type = "application/json"
    return json.dumps(all_imported())

# ── single import (texture) ────────────────────────────────────────────────────

@app.post("/api/import_texture")
def api_import_texture():
    body    = request.json or {}
    skin_id = body.get("skin_id", "")
    rel     = body.get("rel_path", "")
    if not skin_id or not rel:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing skin_id or rel_path"})
    try:
        gr         = game_rel_for_skin(skin_id, rel)
        dst_base   = _import_base(gr)
        work_base  = _cache_import_base(gr)
        os.makedirs(os.path.dirname(dst_base),  exist_ok=True)
        os.makedirs(os.path.dirname(work_base), exist_ok=True)
        uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS),
             "--filter", os.path.basename(pak_game_path(gr))])
        _relocate_to_import(gr)
        decode_batch([work_base + ".uasset"], output_root=IMPORT_ROOT, base_root=WORK_IMPORT_ROOT)
        png_exists = os.path.exists(dst_base + ".png")
        if not png_exists:
            uasset_exists = os.path.exists(work_base + ".uasset")
            msg = "decode failed — PNG not created" if uasset_exists else "extraction failed — asset not found in pak"
            response.content_type = "application/json"
            return json.dumps({"ok": False, "error": msg, "game_rel": gr})
        response.content_type = "application/json"
        return json.dumps({"ok": True, "token": token(gr), "game_rel": gr})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

# ── vfx import (placeholder) ─────────────────────────────────────────────────

@app.post("/api/import_vfx")
def api_import_vfx():
    response.content_type = "application/json"
    return json.dumps({"ok": False, "error": "VFX handler not yet implemented"})

# ── vfx parameters (read: enumerate editable Niagara curves, classified) ──────

@app.get("/api/vfx_params")
def api_vfx_params():
    gr = request.query.get("game_rel", "")
    if not gr:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing game_rel"})
    try:
        p = read_vfx(gr)
        response.content_type = "application/json"
        return json.dumps({"game_rel": gr, "token": token(gr), **p})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

# ── material import ───────────────────────────────────────────────────────────

@app.post("/api/import_material")
def api_import_material():
    body    = request.json or {}
    skin_id = body.get("skin_id", "")
    rel     = body.get("rel_path", "")
    if not skin_id or not rel:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing skin_id or rel_path"})
    try:
        gr = game_rel_for_skin(skin_id, rel)
        mat_json(gr)
        response.content_type = "application/json"
        return json.dumps({"ok": True, "token": token(gr), "game_rel": gr})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

# ── material parameters (read / save / reset) ────────────────────────────────

@app.get("/api/material_params")
def api_material_params():
    gr = request.query.get("game_rel", "")
    if not gr:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing game_rel"})
    try:
        p = read_material(gr)
        response.content_type = "application/json"
        return json.dumps({"ok": True, "game_rel": gr, "token": token(gr), **p})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

@app.post("/api/material_save")
def api_material_save():
    body = request.json or {}
    gr   = body.get("game_rel", "")
    if not gr:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing game_rel"})
    try:
        p = save_material(gr, body.get("colors", {}), body.get("scalars", {}))
        response.content_type = "application/json"
        return json.dumps({"ok": True, "game_rel": gr, "token": token(gr), **p})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

@app.post("/api/material_reset")
def api_material_reset():
    body = request.json or {}
    gr   = body.get("game_rel", "")
    if not gr:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing game_rel"})
    try:
        p = reset_material(gr)
        response.content_type = "application/json"
        return json.dumps({"ok": True, "game_rel": gr, "token": token(gr), **p})
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

_browse_mod._update_callback = _push_sse

def _run_import_job(items):
    """items: [{skin_id, rel_path, game_rel, name}] — extract+decode all via UAssetTool."""
    with _job_lock:
        _job.update(running=True, current=0, total=len(items), name="", done=False, error=None, results=[])
    try:
        names = sorted({os.path.basename(pak_game_path(it["game_rel"])) for it in items})
        _push_sse({"current": 0, "total": len(items), "name": "Extracting from game…", "done": False})
        uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS), "--filter"] + names)

        for it in items:
            _relocate_to_import(it["game_rel"])

        _push_sse({"current": 0, "total": len(items), "name": "Decoding…", "done": False})
        uassets = [_cache_import_base(it["game_rel"]) + ".uasset" for it in items]
        decode_batch([u for u in uassets if os.path.exists(u)],
                     output_root=IMPORT_ROOT, base_root=WORK_IMPORT_ROOT)

        results = []; current = 0
        for it in items:
            dst_base = _import_base(it["game_rel"])
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
            gr = os.path.relpath(event.src_path[:-4], IMPORT_ROOT).replace("\\", "/")
            _push_sse({"file_changed": True, "token": token(gr), "game_rel": gr})
    def on_created(self, event):
        self.on_modified(event)

os.makedirs(IMPORT_ROOT, exist_ok=True)
_observer = Observer()
_observer.schedule(_PNGHandler(), IMPORT_ROOT, recursive=True)
_observer.start()

# ── open in explorer ──────────────────────────────────────────────────────────

def _open_explorer_focused(args):
    import ctypes, time
    user32    = ctypes.windll.user32
    kernel32  = ctypes.windll.kernel32
    EnumProc  = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_size_t, ctypes.c_size_t)
    _CLS      = ("CabinetWClass", "ExploreWClass")

    def _explorer_hwnds():
        found = []
        buf   = ctypes.create_unicode_buffer(64)
        def cb(hwnd, _):
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value in _CLS and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True
        user32.EnumWindows(EnumProc(cb), 0)
        return found

    before = set(_explorer_hwnds())
    proc   = subprocess.Popen(args)

    def _focus():
        time.sleep(0.6)
        after  = _explorer_hwnds()
        target = next((h for h in after if h not in before), None) or (after[0] if after else None)
        if target:
            fg_tid  = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
            our_tid = kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(fg_tid, our_tid, True)
            user32.ShowWindow(target, 9)      # SW_RESTORE
            user32.BringWindowToTop(target)
            user32.SetForegroundWindow(target)
            user32.AttachThreadInput(fg_tid, our_tid, False)

    threading.Thread(target=_focus, daemon=True).start()


@app.get("/api/open_explorer")
def api_open_explorer():
    path = request.query.get("path", "")
    gr   = request.query.get("game_rel", "")
    if gr:
        path = _import_base(gr) + ".png"
    if path:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            _open_explorer_focused(["explorer.exe", f"/select,{abs_path}"])
        elif os.path.isdir(os.path.dirname(abs_path)):
            _open_explorer_focused(["explorer.exe", os.path.dirname(abs_path)])
    response.content_type = "application/json"
    return json.dumps({"ok": True})

# ── export ────────────────────────────────────────────────────────────────────

@app.post("/api/export")
def api_export():
    body     = request.json or {}
    mod_name = (body.get("mod_name") or "Mod").strip() or "Mod"
    items    = body.get("items", [])
    if not items:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "no items selected"})

    out_dir = ASSETS_MODS
    os.makedirs(out_dir, exist_ok=True)

    try:
        tex_items = [gr for gr in items if not is_material(gr)]
        mat_items = [{"game_rel": gr, "colors": {}, "scalars": {}} for gr in items if is_material(gr)]
        result = build_mod(mod_name, tex_items, mat_items, out_dir, force=True)
        if not result.get("ok"):
            response.content_type = "application/json"
            return json.dumps({"ok": False, "error": result.get("error", "build failed")})
        pak = result.get("pak")
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
    import_base = _import_base(gr)
    work_base   = _cache_import_base(gr)
    for ext in (".png", ".json"):
        p = import_base + ext
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
    for ext in (".uasset", ".uexp", ".ubulk"):
        p = work_base + ext
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
    response.content_type = "application/json"
    return json.dumps({"ok": True})

@app.post("/api/delete_all_imported")
def api_delete_all_imported():
    items = all_imported()
    for item in items:
        import_base = _import_base(item["game_rel"])
        work_base   = _cache_import_base(item["game_rel"])
        for ext in (".png", ".json"):
            p = import_base + ext
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        for ext in (".uasset", ".uexp", ".ubulk"):
            p = work_base + ext
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
    response.content_type = "application/json"
    return json.dumps({"ok": True, "deleted": len(items)})
