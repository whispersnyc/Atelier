#!/usr/bin/env python3
"""tex_cli.py - Marvel Rivals skin texture tool.

Usage:
  python tex_cli.py list   <skin_id>[/subpath]                    List textures in paks (recursive)
  python tex_cli.py import <skin_id>[/subpath[/*]]                Extract to assets/Marvel/Content/...
  python tex_cli.py export <mod_name> <skin_id/tex_path> [...]    Pack from assets/ to assets/mods/
                           [--dir <output_dir>] [--override]

skin_id is the 7-digit ID (e.g. 1029304); character ID (1029) is derived automatically.
Import recreates the full game path under assets/ so meshes, VFX, UI etc. all coexist.
Export packs to <mod_name>_9999999_P.{pak,ucas,utoc}. If those files exist you will be
prompted to confirm overwrite; pass --override to skip the prompt.

Examples:
  python tex_cli.py list   1029304
  python tex_cli.py list   1029304/Textures
  python tex_cli.py import 1029304
  python tex_cli.py import 1029304/Textures/*
  python tex_cli.py import 1029301/Textures/10291/*
  python tex_cli.py export MagikWeapon 1029304/Texture/T_1029304_Body_D
  python tex_cli.py export MagikWeapon "1029304/Textures/*"
  python tex_cli.py export MagikWeapon "1029304/Textures/*" "1042306/Texture/*"
  python tex_cli.py export MagikWeapon 1029304/Texture/10290/T_1029304_10290_Weapon_S 1029304/Texture/10290/T_1029304_10290_Weapon_D
  python tex_cli.py export MagikWeapon "1029304/Texture/10290/T_1029304_10290_Weapon_*"
  python tex_cli.py export MagikWeapon "1029304/Textures/*" --dir D:/Mods --override
"""
import os, sys, glob, re, shutil, subprocess, json, threading, atexit

ROOT = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# ── config ────────────────────────────────────────────────────────────────────
def _load_config():
    try: return json.load(open(os.path.join(ROOT, "mr_config.json"), encoding="utf-8"))
    except Exception: return {}

def _detect_paks():
    cands = [r"C:/Program Files (x86)/Steam/steamapps/common/MarvelRivals/MarvelGame/Marvel/Content/Paks"]
    for vdf in (r"C:/Program Files (x86)/Steam/steamapps/libraryfolders.vdf",
                r"C:/Program Files/Steam/steamapps/libraryfolders.vdf"):
        try:
            for m in re.finditer(r'"path"\s*"([^"]+)"', open(vdf, encoding="utf-8", errors="ignore").read()):
                lib = m.group(1).replace("\\\\", "/").replace("\\", "/")
                cands.append(lib + "/steamapps/common/MarvelRivals/MarvelGame/Marvel/Content/Paks")
        except Exception: pass
    for c in cands:
        if os.path.isdir(c) and glob.glob(c + "/pakchunk*.utoc"): return c
    return cands[0]

_cfg  = _load_config()
TOOLS = _cfg.get("tools") or os.path.join(ROOT, "Tools")
PAKS  = (_cfg.get("paks") or _detect_paks()).replace("\\", "/")
os.environ["MR_TOOLS"] = TOOLS
import io_lib   # imported AFTER MR_TOOLS is set, so io_lib resolves Tools/AES_KEY.txt (and works when frozen)

UAT         = os.path.join(TOOLS, "UAssetTool.exe")
_usmaps     = sorted(glob.glob(os.path.join(TOOLS, "Mappings", "*.usmap")))
USMAP       = next((u for u in _usmaps if "_latest" not in os.path.basename(u).lower()), _usmaps[0] if _usmaps else "")
CNW         = 0x08000000 if os.name == "nt" else 0
ASSETS      = os.path.join(ROOT, "assets")
ASSETS_MODS = os.path.join(ROOT, "assets", "mods")
_WORK       = os.path.join(ROOT, "_work")

def _uat(args):
    """Run UAssetTool (texture extract/decode/inject/pack). Pass ABSOLUTE paths — it requires them for output."""
    return subprocess.run([UAT] + args, capture_output=True, text=True, cwd=ROOT, creationflags=CNW)

# ── persistent UAssetTool JSON worker (one long-lived process = no per-call startup) ──────────
_uat_proc = None
_uat_proc_lock = threading.Lock()

def _uat_json(req):
    """Send one line-delimited JSON request to a persistent UAssetTool worker; return the parsed response.
    Reusing one process is what keeps batch decode fast (startup paid once, parallel across all cores)."""
    global _uat_proc
    with _uat_proc_lock:
        if _uat_proc is None or _uat_proc.poll() is not None:
            _uat_proc = subprocess.Popen([UAT], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, cwd=ROOT, creationflags=CNW,
                                         text=True, encoding="utf-8")
        _uat_proc.stdin.write(json.dumps(req) + "\n"); _uat_proc.stdin.flush()
        # Texture actions also print human-readable status to stdout — drain lines until the JSON reply.
        # (Draining also keeps the pipe from filling and deadlocking the worker mid-batch.)
        while True:
            line = _uat_proc.stdout.readline()
            if line == "":
                return {"success": False, "message": "UAssetTool worker closed unexpectedly"}
            s = line.strip()
            if s.startswith("{") and s.endswith("}"):
                try:
                    d = json.loads(s)
                    if isinstance(d, dict) and ("success" in d or "data" in d): return d
                except Exception: pass

@atexit.register
def _uat_shutdown():
    if _uat_proc and _uat_proc.poll() is None:
        try: _uat_proc.terminate()
        except Exception: pass

def _decode_batch(uasset_paths):
    """Parallel-decode many extracted .uasset textures to .png next to each (non-textures are skipped)."""
    paths = [os.path.abspath(p) for p in uasset_paths if os.path.exists(p)]
    if not paths: return {}
    return _uat_json({"action": "batch_extract_texture_png", "file_paths": paths,
                      "output_path": os.path.abspath(ASSETS), "base_path": os.path.abspath(ASSETS),
                      "usmap_path": USMAP, "format": "png", "parallel": True})

# ── texture decode (PNG) — handled by UAssetTool ───────────────────────────────
def _decode_png(dst_base):
    """Decode one extracted UE texture (.uasset/.uexp/.ubulk) to .png via UAssetTool.
    Accurate (object-model, true dims, all BC formats, graceful mip fallback) — no pow2 guessing."""
    if not os.path.exists(dst_base + ".uasset"): return
    out_png = os.path.abspath(dst_base + ".png")
    r = _uat(["extract_texture", os.path.abspath(dst_base + ".uasset"), out_png, "--usmap", USMAP])
    if not os.path.exists(out_png):
        print(f"  [warn] PNG decode failed for {os.path.basename(dst_base)}: "
              f"{((r.stderr or '') + (r.stdout or '')).strip()[-200:]}", file=sys.stderr)

# ── pak index (cached) ────────────────────────────────────────────────────────
_INDEX      = None
_CACHE_FILE = os.path.join(_WORK, "cli_index_cache.json")

def _utoc_key():
    parts = []
    for f in sorted(glob.glob(PAKS + "/*.utoc")):
        s = os.stat(f)
        parts.append(f"{os.path.basename(f)}:{s.st_size}:{int(s.st_mtime)}")
    return "|".join(parts)

def _ensure_index():
    global _INDEX
    if _INDEX is not None: return _INDEX
    key = _utoc_key()
    try:
        c = json.load(open(_CACHE_FILE, encoding="utf-8"))
        if c.get("key") == key:
            _INDEX = [tuple(e) for e in c["entries"]]; return _INDEX
    except Exception: pass
    utocs = sorted(glob.glob(PAKS + "/*.utoc"))
    print(f"  Indexing {len(utocs)} pak containers (first run, cached after)...", file=sys.stderr)
    _INDEX = []
    for utoc in utocs:
        try:
            t    = io_lib.parse_toc(utoc)
            ents = io_lib.parse_dir_index(t)
        except Exception as e:
            print(f"  [warn] {os.path.basename(utoc)}: {e}", file=sys.stderr); continue
        cont = os.path.basename(utoc)
        for p, _ in ents:
            if "/Characters/" in p and p.lower().endswith(".uasset"):
                _INDEX.append((p, cont))
    os.makedirs(_WORK, exist_ok=True)
    json.dump({"key": key, "entries": _INDEX}, open(_CACHE_FILE, "w"))
    return _INDEX

# ── path helpers ──────────────────────────────────────────────────────────────
def _char_id(skin_id): return skin_id[:4]

def _skin_needle(skin_id):
    return f"/Characters/{_char_id(skin_id)}/{skin_id}/".lower()

def _skin_rel(pak_path, skin_id):
    """Pak path -> relative path from the skin folder (original case, no .uasset ext)."""
    needle = _skin_needle(skin_id)
    pl     = pak_path.lower().replace("\\", "/")
    idx    = pl.find(needle)
    if idx < 0: return pak_path
    rel = pak_path[idx + len(needle):]
    return rel[:-7] if rel.lower().endswith(".uasset") else rel

def _pak_rel(pak_path):
    """Strip ../../../ prefix (and .uasset ext) -> mount-relative path."""
    r = re.sub(r"^(\.\./)+", "", pak_path.replace("\\", "/"))
    return r[:-7] if r.lower().endswith(".uasset") else r

def _skin_entries(skin_id):
    needle = _skin_needle(skin_id)
    return [(p, c) for p, c in _ensure_index() if needle in p.lower()]

def _filter_subpath(entries, skin_id, subpath):
    """Narrow entries to those whose skin-relative path starts with subpath."""
    if not subpath: return entries
    needle = _skin_needle(skin_id)
    sp = subpath.lower().replace("\\", "/").strip("/")
    # strip trailing wildcard - we always do prefix/recursive matching
    if sp.endswith("/*"): sp = sp[:-2].strip("/")
    elif sp.endswith("*"): sp = sp[:-1]
    def _match(p):
        pl  = p.lower().replace("\\", "/")
        idx = pl.find(needle)
        if idx < 0: return False
        tail = pl[idx + len(needle):]           # e.g. "textures/10291/t_bar.uasset"
        return tail.startswith(sp) if sp else True
    return [(p, c) for p, c in entries if _match(p)]

# ── prereq check ──────────────────────────────────────────────────────────────
def _check_prereqs(need_tool=True):
    issues = []
    if not glob.glob(PAKS + "/pakchunk*.utoc"):
        issues.append(f"No pak files found at: {PAKS}")
    if need_tool and not os.path.exists(UAT):
        issues.append(f"UAssetTool not found at: {UAT}")
    if issues:
        for i in issues: print(f"[error] {i}", file=sys.stderr)
        sys.exit(1)

# ── list ───────────────────────────────────────────────────────────────────────
def cmd_list(arg):
    _check_prereqs(need_tool=False)
    arg     = arg.replace("\\", "/")
    skin_id, _, subpath = arg.partition("/")
    entries = _skin_entries(skin_id)
    if not entries:
        print(f"No entries found for skin {skin_id}"); return
    if subpath:
        entries = _filter_subpath(entries, skin_id, subpath)
    if not entries:
        print(f"No entries matched under {arg!r}"); return
    seen = set()
    for p, _ in sorted(entries, key=lambda x: x[0].lower()):
        line = f"{skin_id}/{_skin_rel(p, skin_id)}"
        if line not in seen:
            seen.add(line); print(line)

# ── import ─────────────────────────────────────────────────────────────────────
def cmd_import(arg):
    _check_prereqs()
    arg     = arg.replace("\\", "/")
    skin_id, _, subpath = arg.partition("/")
    entries = _skin_entries(skin_id)
    if not entries:
        print(f"No entries found for skin {skin_id}"); return
    if subpath:
        entries = _filter_subpath(entries, skin_id, subpath)
    if not entries:
        print(f"No entries matched {arg!r}"); return

    char_id   = _char_id(skin_id)
    dest_root = os.path.abspath(os.path.join(ASSETS, "Marvel", "Content", "Marvel", "Characters", char_id, skin_id))
    print(f"  Destination: {dest_root}")

    # UAssetTool extracts straight to legacy under assets/ (full game path preserved), then batch-decodes to PNG.
    # Names carry the skin id, so basename filters are unique; cross-container matches resolve to the patched version.
    names = sorted({os.path.basename(p)[:-7] for p, _ in entries})
    print(f"  Extracting {len(names)} asset(s) from game via UAssetTool...", file=sys.stderr)
    r = _uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS), "--filter"] + names)
    if "Extraction complete" not in (r.stdout or ""):
        print(f"  [warn] extract: {((r.stderr or '') + (r.stdout or '')).strip()[-300:]}", file=sys.stderr)
    _decode_batch(glob.glob(os.path.join(dest_root, "**", "*.uasset"), recursive=True))   # parallel -> PNG next to each

    n_assets = len(glob.glob(os.path.join(dest_root, "**", "*.uasset"), recursive=True))
    n_png    = len(glob.glob(os.path.join(dest_root, "**", "*.png"), recursive=True))
    print(f"Extracted {n_assets} asset(s), decoded {n_png} PNG -> {dest_root}")

# ── export ─────────────────────────────────────────────────────────────────────
def _game_rel_for_skin(skin_id, tex_rel):
    """Build the game-relative path from a skin_id shorthand + tex_rel."""
    char_id = _char_id(skin_id)
    return f"Marvel/Content/Marvel/Characters/{char_id}/{skin_id}/{tex_rel}"

def _split_glob_prefix(prefix):
    """Split 'some/dir/FilePrefix_' into ('some/dir', 'FilePrefix_')."""
    if "/" in prefix:
        d, f = prefix.rsplit("/", 1)
        return d, f
    return "", prefix

def _expand_export_args(args):
    """Resolve export args to [(game_rel_no_ext, display_label), ...], expanding wildcards.
    Accepts:
      <skin_id>/<tex_rel>                     shorthand (as printed by list)
      Marvel/Content/Marvel/Characters/...    full game-relative path
      /absolute/path/under/assets/...         absolute filesystem path
    """
    results = []
    for arg in args:
        arg = arg.replace("\\", "/")
        if os.path.isabs(arg):
            try: arg = os.path.relpath(arg.replace("/", os.sep), ASSETS).replace("\\", "/")
            except ValueError:
                print(f"  [warn] path not under assets/: {arg}", file=sys.stderr); continue
        noext = arg[:-7] if arg.lower().endswith(".uasset") else arg
        if re.match(r"^\d{7}(/|$)", noext):
            skin_id  = noext[:7]
            tex_part = noext[8:] if len(noext) > 8 else ""
            if not tex_part:
                print(f"  [warn] no texture path after skin_id in {arg!r}", file=sys.stderr); continue
            if "*" in tex_part:
                dir_part, file_prefix = _split_glob_prefix(tex_part.split("*")[0])
                char_id  = _char_id(skin_id)
                skin_dir = os.path.join(ASSETS, "Marvel", "Content", "Marvel", "Characters", char_id, skin_id)
                search_dir = os.path.join(skin_dir, *dir_part.split("/")) if dir_part else skin_dir
                if not os.path.isdir(search_dir):
                    print(f"  [warn] directory not found: {search_dir}", file=sys.stderr); continue
                for root_dir, _, files in os.walk(search_dir):
                    for fname in sorted(files):
                        if not fname.lower().endswith(".uasset"): continue
                        if file_prefix and not fname.lower().startswith(file_prefix.lower()): continue
                        r = os.path.relpath(os.path.join(root_dir, fname), skin_dir).replace("\\", "/")
                        r = r[:-7] if r.lower().endswith(".uasset") else r
                        results.append((_game_rel_for_skin(skin_id, r), f"{skin_id}/{r}"))
            else:
                results.append((_game_rel_for_skin(skin_id, tex_part), f"{skin_id}/{tex_part}"))
        else:
            if "*" in noext:
                dir_part, file_prefix = _split_glob_prefix(noext.split("*")[0])
                search_dir = os.path.join(ASSETS, *dir_part.split("/")) if dir_part else ASSETS
                if not os.path.isdir(search_dir):
                    print(f"  [warn] directory not found: {search_dir}", file=sys.stderr); continue
                for root_dir, _, files in os.walk(search_dir):
                    for fname in sorted(files):
                        if not fname.lower().endswith(".uasset"): continue
                        if file_prefix and not fname.lower().startswith(file_prefix.lower()): continue
                        r = os.path.relpath(os.path.join(root_dir, fname), ASSETS).replace("\\", "/")
                        r = r[:-7] if r.lower().endswith(".uasset") else r
                        results.append((r, r))
            else:
                results.append((noext, noext))
    seen = set(); out = []
    for item in results:
        if item[0] not in seen: seen.add(item[0]); out.append(item)
    return out

# ── export staging: inject edited PNG into the vanilla .uasset via UAssetTool ──────────────────
def _stage_inject(stage, game_rel):
    """Stage one texture for export: inject the edited PNG into the imported (vanilla) .uasset via
    UAssetTool. Pixel format is preserved from the base texture — no templates, no binary patching."""
    base = os.path.join(ASSETS, *game_rel.split("/"))
    png  = base + ".png"
    if not os.path.exists(base + ".uasset"):
        raise RuntimeError("no base asset — run 'import' first")
    if not os.path.exists(png):
        _decode_png(base)
        if not os.path.exists(png):
            raise RuntimeError("PNG missing and decode failed — re-import this texture")
    out_ua = os.path.join(stage, *game_rel.split("/")) + ".uasset"
    os.makedirs(os.path.dirname(out_ua), exist_ok=True)
    r = _uat(["inject_texture", os.path.abspath(base + ".uasset"), os.path.abspath(png),
              os.path.abspath(out_ua), "--usmap", USMAP])
    if not os.path.exists(out_ua):
        raise RuntimeError("inject failed: " + (((r.stderr or "") + (r.stdout or "")).strip()[-200:] or "unknown"))
    return os.path.basename(game_rel)

# ── material (MI) parameter editing via UAssetTool to_json / from_json ─────────────────────────
def is_material(path_or_name):
    return os.path.basename(path_or_name).upper().startswith("MI_")

def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def _gn(lst, n):
    for p in lst or []:
        if isinstance(p, dict) and p.get("Name") == n: return p
    return None
def _ex_props(e): return e.get("Data") or e.get("Value") or []
def _mat_pname(entry):
    pinfo = _gn(entry["Value"], "ParameterInfo")
    return (_gn(pinfo["Value"], "Name") or {}).get("Value") if pinfo else None
def _mat_color(entry):
    pv = _gn(entry["Value"], "ParameterValue"); v = pv.get("Value") if pv else None
    if isinstance(v, list) and v and isinstance(v[0], dict) and isinstance(v[0].get("Value"), dict) and "R" in v[0]["Value"]:
        return v[0]["Value"]
    return None
def _mat_params(d):
    ex = d["Exports"][0]
    vp = _gn(_ex_props(ex), "VectorParameterValues"); sp = _gn(_ex_props(ex), "ScalarParameterValues")
    colors, scalars = [], []
    for e in (vp or {}).get("Value", []):
        nm = _mat_pname(e); lc = _mat_color(e)
        if nm and lc: colors.append({"name": nm, "rgba": [round(_f(lc[k]), 5) for k in "RGBA"]})
    for e in (sp or {}).get("Value", []):
        nm = _mat_pname(e); pv = _gn(e["Value"], "ParameterValue")
        if nm and pv is not None and not isinstance(pv.get("Value"), (list, dict)):
            scalars.append({"name": nm, "value": round(_f(pv.get("Value")), 5)})
    return colors, scalars
def _apply_mat_edits(d, colors, scalars):
    ex = d["Exports"][0]
    vp = _gn(_ex_props(ex), "VectorParameterValues"); sp = _gn(_ex_props(ex), "ScalarParameterValues")
    for e in (vp or {}).get("Value", []):
        nm = _mat_pname(e)
        if nm in colors:
            lc = _mat_color(e)
            if lc: r, g, b, a = colors[nm]; lc["R"], lc["G"], lc["B"], lc["A"] = float(r), float(g), float(b), float(a)
    for e in (sp or {}).get("Value", []):
        nm = _mat_pname(e)
        if nm in scalars:
            pv = _gn(e["Value"], "ParameterValue")
            if pv is not None: pv["Value"] = float(scalars[nm])

def _mat_json(game_rel):
    """Extract the MI + convert to JSON (cached at assets/<game_rel>.json). Returns the json path."""
    base = os.path.join(ASSETS, *game_rel.split("/"))
    jp = base + ".json"
    if os.path.exists(jp): return jp
    if not os.path.exists(base + ".uasset"):
        _uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS), "--filter", os.path.basename(game_rel)])
    if not os.path.exists(base + ".uasset"):
        raise RuntimeError("material not found in game paks")
    _uat(["to_json", os.path.abspath(base + ".uasset"), USMAP, os.path.abspath(os.path.dirname(base))])
    if not os.path.exists(jp): raise RuntimeError("to_json produced no JSON")
    return jp
def read_material(game_rel):
    """{colors:[{name,rgba}], scalars:[{name,value}]} for an MI material instance."""
    colors, scalars = _mat_params(json.load(open(_mat_json(game_rel), encoding="utf-8-sig")))
    return {"colors": colors, "scalars": scalars}
def _stage_material(stage, game_rel, colors, scalars):
    """Apply color/scalar edits to the MI and from_json it into the export stage (byte-faithful)."""
    d = json.load(open(_mat_json(game_rel), encoding="utf-8-sig"))
    _apply_mat_edits(d, colors or {}, scalars or {})
    ej = os.path.join(_WORK, "_mat_edit.json"); json.dump(d, open(ej, "w"))
    out_ua = os.path.join(stage, *game_rel.split("/")) + ".uasset"
    os.makedirs(os.path.dirname(out_ua), exist_ok=True)
    _uat(["from_json", os.path.abspath(ej), os.path.abspath(out_ua), USMAP])
    if not os.path.exists(out_ua): raise RuntimeError("from_json produced no uasset")
    return os.path.basename(game_rel)

def build_mod(mod_name, tex_items, mat_items, out_dir, force=True):
    """Pack TEXTURE edits (inject) + MATERIAL param edits (from_json) into ONE mod.
    tex_items: [game_rel]; mat_items: [{game_rel, colors:{name:[r,g,b,a]}, scalars:{name:val}}]."""
    out_dir = os.path.abspath(out_dir); stem = f"{mod_name}_9999999_P"; base = os.path.join(out_dir, stem)
    for ext in (".pak", ".ucas", ".utoc"):
        if os.path.exists(base + ext): os.remove(base + ext)
    stage = os.path.join(_WORK, "build_stage", mod_name)
    shutil.rmtree(os.path.join(_WORK, "build_stage"), ignore_errors=True); os.makedirs(stage)
    applied, skipped = [], []
    for game_rel in tex_items:
        try: applied.append("tex " + _stage_inject(stage, game_rel))
        except Exception as e: skipped.append(f"{os.path.basename(game_rel)}: {e}")
    for m in mat_items:
        try: applied.append("mat " + _stage_material(stage, m["game_rel"], m.get("colors", {}), m.get("scalars", {})))
        except Exception as e: skipped.append(f"{os.path.basename(m.get('game_rel',''))}: {e}")
    if not applied:
        return {"ok": False, "error": "nothing staged: " + "; ".join(skipped)}
    os.makedirs(out_dir, exist_ok=True)
    _uat(["create_mod_iostore", os.path.abspath(base), os.path.abspath(stage), "--usmap", USMAP])
    if not os.path.exists(base + ".utoc"):
        return {"ok": False, "error": "create_mod_iostore failed"}
    return {"ok": True, "applied": applied, "skipped": skipped, "pak": base + ".pak"}

def cmd_export(mod_name, tex_args, out_dir, force):
    _check_prereqs()
    pairs = _expand_export_args(tex_args)
    if not pairs:
        print("No files resolved for export"); return

    out_dir  = os.path.abspath(out_dir)
    stem     = f"{mod_name}_9999999_P"
    existing = [fp for ext in (".pak", ".ucas", ".utoc")
                for fp in (os.path.join(out_dir, stem + ext),) if os.path.exists(fp)]
    if existing and not force:
        print(f"Mod '{stem}' already exists in {out_dir}.")
        try:
            ans = input("Overwrite? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "y":
            print("Aborted."); return
    for fp in existing:
        os.remove(fp)

    stage = os.path.join(_WORK, "cli_export_stage", mod_name)
    shutil.rmtree(stage, ignore_errors=True); os.makedirs(stage)
    try:
        staged = 0; skipped = []
        for game_rel, label in pairs:
            try:
                desc = _stage_inject(stage, game_rel)
                staged += 1
                print(f"  staged {label} -> {desc}")
            except Exception as e:
                skipped.append(f"{label}: {e}")
        if skipped:
            for s in skipped: print(f"  [warn] skipped: {s}", file=sys.stderr)
        if not staged:
            print("Nothing staged — check warnings above"); return

        os.makedirs(out_dir, exist_ok=True)
        base = os.path.join(out_dir, stem)
        r = _uat(["create_mod_iostore", os.path.abspath(base), os.path.abspath(stage), "--usmap", USMAP])
        if not os.path.exists(base + ".utoc"):
            print(f"create_mod_iostore failed:\n{((r.stderr or '') + (r.stdout or '')).strip()[:500]}"); return

        if os.path.exists(base + ".utoc"):
            print(f"Packed {staged} texture(s) -> {os.path.abspath(base)}.{{pak,ucas,utoc}}")
        else:
            made = sorted(glob.glob(os.path.join(out_dir, "*_P.utoc")))
            if made:
                base = made[-1][:-5]
                print(f"Packed {staged} texture(s) -> {os.path.abspath(base)}.{{pak,ucas,utoc}}")
            else:
                print(f"retoc exit 0 but no .utoc found in {out_dir}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)

# ── main ───────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 3:
        print(__doc__.strip()); sys.exit(1)
    cmd  = sys.argv[1].lower()
    rest = sys.argv[2:]
    if cmd == "list":
        cmd_list(rest[0])
    elif cmd == "import":
        cmd_import(rest[0])
    elif cmd == "export":
        # parse --dir and --override out of the remaining args
        out_dir  = ASSETS_MODS
        force    = False
        positional = []
        i = 0
        while i < len(rest):
            if rest[i] == "--dir" and i + 1 < len(rest):
                out_dir = rest[i + 1]; i += 2
            elif rest[i] == "--override":
                force = True; i += 1
            else:
                positional.append(rest[i]); i += 1
        if len(positional) < 2:
            print("export requires: <mod_name> <tex_path> [...]\n")
            print(__doc__.strip()); sys.exit(1)
        cmd_export(positional[0], positional[1:], out_dir, force)
    else:
        print(f"Unknown command: {cmd!r}\n"); print(__doc__.strip()); sys.exit(1)

if __name__ == "__main__":
    main()
