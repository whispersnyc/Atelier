import os, sys, glob, json, re, shutil, threading, queue, subprocess, tempfile, time, urllib.request
from bottle import request, response, static_file

from atelier.web.app import app
from atelier.config import (ROOT, ASSETS, IMPORT_ROOT, WORK_IMPORT_ROOT, ASSETS_MODS, PAKS, GUI_DIR, _CACHE,
                            get_prereq_status, CONFIG_HAS_PAKS, paks_suggestion, save_paks_config,
                            save_setup_config, save_usmap_config, get_usmap_checked_at, save_usmap_checked_at)

_USMAP_PATTERN = re.compile(r'^5\.3\.2-\d+\+\+\+depot_marvel\+S\d+\.\d+_release-Marvel\.usmap$')
_THREE_DAYS    = 3 * 24 * 3600

def _fetch_github_usmap_list():
    url = "https://api.github.com/repos/SpaceDepot/rivals-depot/contents/usmap"
    req = urllib.request.Request(url, headers={"User-Agent": "Atelier/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def _get_latest_usmap_from_github():
    files = _fetch_github_usmap_list()
    matching = [f for f in files if f.get("type") == "file" and _USMAP_PATTERN.match(f["name"])]
    if not matching:
        return None
    matching.sort(key=lambda x: x["name"])
    return matching[-1]

def _download_usmap_file(download_url, dest_path):
    req = urllib.request.Request(download_url, headers={"User-Agent": "Atelier/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(data)

THUMBS_DIR = os.path.join(_CACHE, "thumbs")
from atelier.tools import uat
from atelier.handlers.texture import decode_batch, stage_inject, build_mod, decode_thumb, extract_output_base, find_extracted
from atelier.handlers.pak_thumb import decode_thumb_from_pak
import atelier.handlers.pak_thumb as _pak_thumb_mod
import io_lib as _io_lib_mod
if CONFIG_HAS_PAKS and _io_lib_mod.AES_KEY:
    _pak_thumb_mod.start_warmup()
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

def _relocate_to_import(game_rel):
    """Move .uasset/.uexp/.ubulk from pak extraction location to _cache/import structure.
    Uses the index to determine the exact extraction output path; falls back to ASSETS walk
    if the asset isn't in the index (stale cache, etc.)."""
    src_base = extract_output_base(game_rel)
    if not src_base or not os.path.exists(src_base + ".uasset"):
        src_base = find_extracted(game_rel)
    if not src_base:
        return
    dst_base = _cache_import_base(game_rel)
    os.makedirs(os.path.dirname(dst_base), exist_ok=True)
    assets_root = os.path.abspath(ASSETS)
    for ext in (".uasset", ".uexp", ".ubulk"):
        src = src_base + ext
        if os.path.exists(src):
            shutil.move(src, dst_base + ext)
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

def _find_mr_root(path):
    """Find the game root by walking up path until */MarvelGame/Marvel/Content/Paks exists."""
    norm  = path.replace("\\", "/").rstrip("/")
    parts = norm.split("/")
    for i in range(len(parts), 0, -1):
        candidate = "/".join(parts[:i])
        if os.path.isdir(candidate + "/MarvelGame/Marvel/Content/Paks"):
            return candidate
    return None

def _mr_root_to_display(paks_path):
    """Given a stored Paks path, return the game root for display in the frontend."""
    mr = _find_mr_root(paks_path)
    if mr:
        return mr
    # Fallback: strip the known paks suffix if present
    norm = paks_path.replace("\\", "/").rstrip("/")
    suffix = "/marvelgame/marvel/content/paks"
    if norm.lower().endswith(suffix):
        return norm[:-len(suffix)]
    return norm

@app.get("/api/setup_status")
def api_setup_status():
    import atelier.config as _c
    cfg  = _c._load_config()
    paks = cfg.get("paks", "")
    aes  = cfg.get("aes_key", "")
    configured = bool(paks) and bool(aes) and bool(_c.USMAP)
    if paks:
        mr_prefill = _mr_root_to_display(paks)
    else:
        suggestion = _c.paks_suggestion()
        mr_prefill = _mr_root_to_display(suggestion) if suggestion else ""
    aes_prefill  = ("0x" + aes) if aes else ""
    usmap_prefill = (_c.USMAP or "").replace("\\", "/")
    response.content_type = "application/json"
    return json.dumps({"configured": configured,
                       "paks_prefill":  mr_prefill,
                       "aes_prefill":   aes_prefill,
                       "usmap_prefill": usmap_prefill})

@app.post("/api/pick_folder")
def api_pick_folder():
    body    = request.json or {}
    initial = (body.get("initial") or "").replace("/", "\\")
    env     = os.environ.copy()
    env["PAKS_INITIAL"] = initial
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = 'Select your MarvelRivals folder:'; "
        "$f.SelectedPath = $env:PAKS_INITIAL; "
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
    )
    try:
        r   = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=120, env=env)
        raw = r.stdout.strip().replace("\\", "/")
        mr  = _find_mr_root(raw) if raw else ""
        response.content_type = "application/json"
        return json.dumps({"ok": True, "path": mr or raw})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "path": "", "error": str(e)})

@app.get("/api/validate_paks")
def api_validate_paks():
    path = request.query.get("path", "").strip()
    response.content_type = "application/json"
    if not path:
        return json.dumps({"status": "empty"})
    mr = _find_mr_root(path)
    if not mr:
        return json.dumps({"status": "wrong_folder"})
    if not os.path.isdir(mr):
        return json.dumps({"status": "missing"})
    paks = mr + "/MarvelGame/Marvel/Content/Paks"
    if not os.path.exists(paks + "/pakchunkCharacter-Windows.ucas"):
        return json.dumps({"status": "wrong_folder"})
    return json.dumps({"status": "ok"})

def _validate_and_build_paks(path):
    """Validate user-supplied path (game root or subfolder) and return (paks_path, error)."""
    mr = _find_mr_root(path)
    if not mr:
        return None, "MarvelGame/Marvel/Content/Paks not found in path"
    paks = mr + "/MarvelGame/Marvel/Content/Paks"
    if not os.path.exists(paks + "/pakchunkCharacter-Windows.ucas"):
        return None, "pakchunkCharacter-Windows.ucas not found — wrong folder"
    return paks, None

@app.post("/api/save_paks")
def api_save_paks():
    body       = request.json or {}
    path       = body.get("path", "").strip()
    aes_key    = body.get("aes_key", "").strip()  # stored without 0x prefix
    usmap_path = body.get("usmap_path", "").strip()
    if not path:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "no path provided"})
    if not aes_key:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "no AES key provided"})
    paks_path, err = _validate_and_build_paks(path)
    if err:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": err})
    try:
        save_setup_config(paks_path, aes_key,
                          usmap_path if (usmap_path and os.path.exists(usmap_path)) else None)

        # Write AES_KEY.txt so io_lib picks it up immediately
        import atelier.config as _c
        os.makedirs(_c.TOOLS, exist_ok=True)
        with open(os.path.join(_c.TOOLS, "AES_KEY.txt"), "w", encoding="utf-8") as _f:
            _f.write(aes_key)

        # Update in-process globals — no restart needed
        _io_lib_mod.AES_KEY = bytes.fromhex(aes_key)

        _c.PAKS = paks_path
        _c.CONFIG_HAS_PAKS = True
        global PAKS
        PAKS = paks_path

        import atelier.index as _idx
        _idx.PAKS = paks_path
        _idx._INDEX = None  # force re-index with new path

        _pak_thumb_mod.PAKS = paks_path
        with _pak_thumb_mod._toc_lock:  _pak_thumb_mod._toc_cache.clear()
        with _pak_thumb_mod._gr_map_lock: _pak_thumb_mod._gr_to_cont.clear()
        _pak_thumb_mod._gr_map_ready = False
        _pak_thumb_mod.start_warmup()

        if usmap_path and os.path.exists(usmap_path):
            _c.USMAP = usmap_path
            import atelier.handlers.texture as _tex
            import atelier.handlers.material as _mat
            import atelier.handlers.vfx     as _vfx
            _tex.USMAP = usmap_path
            _mat.USMAP = usmap_path
            _vfx.USMAP = usmap_path

        response.content_type = "application/json"
        return json.dumps({"ok": True})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

@app.get("/api/open_discord_key")
def api_open_discord_key():
    url = "https://discord.com/channels/1419106202511609958/1485413590310584374/1485417747834863616"
    try: os.startfile(url)
    except Exception: pass
    response.content_type = "application/json"
    return json.dumps({"ok": True})

# ── auto-update ───────────────────────────────────────────────────────────────

_update_cache      = None   # {"tag": str, "download_url": str} set by update_check
_update_state      = "idle" # idle | downloading | error
_update_state_lock = threading.Lock()
_update_progress   = {"pct": 0, "bytes": 0, "total": 0}


@app.get("/api/update_check")
def api_update_check():
    global _update_cache
    response.content_type = "application/json"

    version_path = os.path.join(ROOT, "version")
    try:
        with open(version_path, "r") as f:
            current = tuple(int(x) for x in f.read().strip().split("."))
        print(f"[update] current version: {current}")
    except Exception as e:
        print(f"[update] failed to read version: {e}")
        return json.dumps({"available": False})

    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/clownfetus/Atelier/releases?per_page=1",
            headers={"User-Agent": "Atelier-Updater"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            releases = json.loads(r.read().decode())
        if not releases:
            return json.dumps({"available": False})
        data = releases[0]
    except Exception as e:
        print(f"[update] GitHub request failed: {e}")
        return json.dumps({"available": False})

    tag = data.get("tag_name", "")
    try:
        remote = tuple(int(x) for x in tag.lstrip("v").split("."))
    except Exception:
        return json.dumps({"available": False})

    if remote <= current:
        print(f"[update] up to date ({current} >= {remote})")
        return json.dumps({"available": False})

    try:
        with open(os.path.join(ROOT, "mr_config.json"), encoding="utf-8") as f:
            _cfg_skip = json.load(f)
        if _cfg_skip.get("skipped_update") == tag:
            print(f"[update] {tag} was skipped by user")
            return json.dumps({"available": False})
    except Exception:
        pass

    download_url = None
    for asset in data.get("assets", []):
        if asset["name"] == "AtelierSetup.exe":
            download_url = asset["browser_download_url"]
            break
    if not download_url:
        print("[update] AtelierSetup.exe not found in release assets")
        return json.dumps({"available": False})

    _update_cache = {"tag": tag, "download_url": download_url}
    print(f"[update] update available: {current} -> {remote}")
    return json.dumps({"available": True, "tag": tag})


@app.post("/api/update_skip")
def api_update_skip():
    response.content_type = "application/json"
    if not _update_cache:
        return json.dumps({"ok": False})
    tag = _update_cache["tag"]
    cfg_path = os.path.join(ROOT, "mr_config.json")
    cfg = {}
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        pass
    cfg["skipped_update"] = tag
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"[update] skipped version {tag}")
    return json.dumps({"ok": True})


@app.post("/api/update_download")
def api_update_download():
    global _update_state
    response.content_type = "application/json"
    if not _update_cache:
        return json.dumps({"ok": False, "error": "no update info cached"})
    with _update_state_lock:
        if _update_state == "downloading":
            return json.dumps({"ok": True})
        _update_state = "downloading"
    threading.Thread(target=_do_update_download, args=(_update_cache["download_url"],), daemon=True).start()
    return json.dumps({"ok": True})


@app.get("/api/update_status")
def api_update_status():
    response.content_type = "application/json"
    return json.dumps({"state": _update_state})


@app.get("/api/update_progress")
def api_update_progress():
    response.content_type = "application/json"
    return json.dumps(_update_progress)


def _do_update_download(download_url):
    global _update_state, _update_progress
    tmp_path = os.path.join(tempfile.gettempdir(), "AtelierSetup.exe")

    def _reporthook(block_num, block_size, total_size):
        global _update_progress
        if total_size > 0:
            downloaded = min(block_num * block_size, total_size)
            _update_progress = {
                "pct":   int(downloaded * 100 / total_size),
                "bytes": downloaded,
                "total": total_size,
            }

    try:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f"[update] downloading to {tmp_path}...")
        urllib.request.urlretrieve(download_url, tmp_path, reporthook=_reporthook)
        print("[update] download complete, launching installer...")
    except Exception as e:
        print(f"[update] download failed: {e}")
        with _update_state_lock:
            _update_state = "error"
        return
    subprocess.Popen(
        [tmp_path, "/SILENT"],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    print("[update] installer launched, exiting...")
    os._exit(0)


# ── USMAP management ──────────────────────────────────────────────────────────

@app.get("/api/validate_usmap")
def api_validate_usmap():
    path = request.query.get("path", "").strip()
    response.content_type = "application/json"
    if not path:
        return json.dumps({"status": "empty"})
    if not path.lower().endswith(".usmap"):
        return json.dumps({"status": "invalid"})
    if not os.path.exists(path):
        return json.dumps({"status": "missing"})
    return json.dumps({"status": "ok"})

@app.post("/api/pick_usmap_file")
def api_pick_usmap_file():
    body    = request.json or {}
    initial = (body.get("initial") or "").replace("/", "\\")
    env     = os.environ.copy()
    env["USMAP_INITIAL"] = os.path.dirname(initial) if initial else ""
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$f = New-Object System.Windows.Forms.OpenFileDialog; "
        "$f.Title = 'Select USMAP mapping file'; "
        "$f.Filter = 'USMAP files (*.usmap)|*.usmap|All files (*.*)|*.*'; "
        "$f.InitialDirectory = $env:USMAP_INITIAL; "
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.FileName }"
    )
    try:
        r   = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=120, env=env)
        raw = r.stdout.strip().replace("\\", "/")
        response.content_type = "application/json"
        return json.dumps({"ok": True, "path": raw})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "path": "", "error": str(e)})

@app.post("/api/download_usmap")
def api_download_usmap():
    import atelier.config as _c
    try:
        latest = _get_latest_usmap_from_github()
        if not latest:
            response.content_type = "application/json"
            return json.dumps({"ok": False, "error": "No matching USMAP found on GitHub"})
        mappings_dir = os.path.join(_c.TOOLS, "Mappings")
        dest_path    = os.path.join(mappings_dir, latest["name"])
        if not os.path.exists(dest_path):
            _download_usmap_file(latest["download_url"], dest_path)
        response.content_type = "application/json"
        return json.dumps({"ok": True, "path": dest_path.replace("\\", "/"), "name": latest["name"]})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})

_usmap_check_running = False
_usmap_check_lock    = threading.Lock()

@app.get("/api/usmap_update_check")
def api_usmap_update_check():
    global _usmap_check_running
    import atelier.config as _c
    last = _c.get_usmap_checked_at()
    if time.time() - last < _THREE_DAYS:
        response.content_type = "application/json"
        return json.dumps({"ok": True, "updated": False})
    with _usmap_check_lock:
        if _usmap_check_running:
            response.content_type = "application/json"
            return json.dumps({"ok": True, "checking": True})
        _usmap_check_running = True

    def _check():
        global _usmap_check_running
        try:
            latest = _get_latest_usmap_from_github()
            if not latest:
                return
            latest_name  = latest["name"]
            current_name = os.path.basename(_c.USMAP) if _c.USMAP else ""
            _c.save_usmap_checked_at(time.time())
            if latest_name == current_name:
                return
            mappings_dir = os.path.join(_c.TOOLS, "Mappings")
            dest_path    = os.path.join(mappings_dir, latest_name)
            _download_usmap_file(latest["download_url"], dest_path)
            # Remove old file if it matches the pattern
            if current_name and _USMAP_PATTERN.match(current_name):
                old_path = os.path.join(mappings_dir, current_name)
                if os.path.exists(old_path) and os.path.abspath(old_path) != os.path.abspath(dest_path):
                    try: os.remove(old_path)
                    except Exception: pass
            _c.save_usmap_config(dest_path)
            _c.USMAP = dest_path
            _push_sse({"usmap_updated": True, "name": latest_name})
        except Exception:
            pass  # silent fail — will retry on next launch
        finally:
            with _usmap_check_lock:
                _usmap_check_running = False

    threading.Thread(target=_check, daemon=True).start()
    response.content_type = "application/json"
    return json.dumps({"ok": True, "checking": True})

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
    gr_in   = body.get("game_rel", "")
    if skin_id and rel:
        gr = game_rel_for_skin(skin_id, rel)   # skin flow (unchanged)
    elif gr_in:
        gr = gr_in                             # non-skin flow (e.g. VFX tree) — import by game_rel
    else:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing skin_id/rel_path or game_rel"})
    try:
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


@app.post("/api/replace_texture")
def api_replace_texture():
    from PIL import Image
    import io
    gr     = request.forms.get("game_rel", "").strip()
    upload = request.files.get("file")
    if not gr or not upload:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "missing game_rel or file"})
    dst = _import_base(gr) + ".png"
    if not os.path.exists(dst):
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": "asset not imported — edit it first"})
    try:
        img = Image.open(io.BytesIO(upload.file.read())).convert("RGBA")
        img.save(dst, "PNG")
        thumb = os.path.join(THUMBS_DIR, *gr.split("/")) + ".png"
        try:
            if os.path.exists(thumb): os.remove(thumb)
        except Exception:
            pass
        response.content_type = "application/json"
        return json.dumps({"ok": True, "token": token(gr), "game_rel": gr})
    except Exception as e:
        response.content_type = "application/json"
        return json.dumps({"ok": False, "error": str(e)})


@app.get("/api/open_with")
def api_open_with():
    path = request.query.get("path", "")
    gr   = request.query.get("game_rel", "")
    if gr:
        path = _import_base(gr) + ".png"
    if path:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", abs_path])
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
