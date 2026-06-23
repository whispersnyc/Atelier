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
import os, sys, glob, re, fnmatch, shutil, subprocess, json, struct, io
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
import io_lib

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

RETOC       = os.path.join(TOOLS, "retoc-rivals-cli", "retoc-rivals-cli.exe")
TEXCONV     = os.path.join(TOOLS, "texconv.exe")
CNW         = 0x08000000 if os.name == "nt" else 0
ASSETS      = os.path.join(ROOT, "assets")
ASSETS_MODS = os.path.join(ROOT, "assets", "mods")
TPL_DIR     = os.path.join(ROOT, "templates")
_WORK       = os.path.join(ROOT, "_work")

_TCFMT = {"DXT1": "BC1_UNORM", "DXT5": "BC3_UNORM", "BC4": "BC4_UNORM",
           "BC5": "BC5_UNORM", "BC7": "BC7_UNORM", "BC6H": "BC6H_UF16"}

def _run(args):
    return subprocess.run(args, capture_output=True, cwd=ROOT, creationflags=CNW)

# ── texture decode (PNG) ───────────────────────────────────────────────────────
_BLOCK = {"DXT1": 8, "BC4": 8, "DXT5": 16, "BC5": 16, "BC7": 16, "BC6H": 16}
_DXGI  = {"BC4": 80, "BC5": 83, "BC6H": 95, "BC7": 98}

def _mip0(fmt, w, h):
    if fmt == "B8G8R8A8": return w * h * 4
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * _BLOCK.get(fmt, 16)

def _dds(fmt, w, h, data):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    base  = struct.pack("<7I", 124, flags, h, w, len(data), 0, 1) + b"\0" * 44
    if fmt == "B8G8R8A8":
        pf = struct.pack("<2I", 32, 0x41) + b"\0" * 4 + struct.pack("<5I", 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
        return b"DDS " + base + pf + struct.pack("<5I", 0x1000, 0, 0, 0, 0) + data
    if fmt in ("DXT1", "DXT5"):
        pf = struct.pack("<2I", 32, 0x4) + fmt.encode() + b"\0" * 20
        return b"DDS " + base + pf + struct.pack("<5I", 0x1000, 0, 0, 0, 0) + data
    if fmt in _DXGI:
        pf = struct.pack("<2I", 32, 0x4) + b"DX10" + b"\0" * 20
        return b"DDS " + base + pf + struct.pack("<5I", 0x1000, 0, 0, 0, 0) + struct.pack("<5I", _DXGI[fmt], 3, 0, 1, 0) + data
    return None

def _decode_png(dst_base):
    """Decode extracted UE texture files (.uasset/.uexp/.ubulk) to .png alongside them."""
    uasset = dst_base + ".uasset"; uexp = dst_base + ".uexp"; ubulk = dst_base + ".ubulk"
    if not os.path.exists(uexp): return
    try:
        ab  = open(uasset, "rb").read() if os.path.exists(uasset) else b""
        fmt = next((f for f in ("DXT1", "DXT5", "BC7", "BC6H", "BC5", "BC4", "B8G8R8A8")
                    if b"PF_" + f.encode() in ab), "DXT1")
        eb   = open(uexp, "rb").read()
        POW  = {32, 64, 128, 256, 512, 1024, 2048, 4096, 8192}
        cands = set()
        for o in range(0, len(eb) - 8):
            x, y = struct.unpack_from("<ii", eb, o)
            if x in POW and y in POW: cands.add((x, y))
        raw  = open(ubulk, "rb").read() if os.path.exists(ubulk) else eb
        fits = [(w, h) for w, h in cands if _mip0(fmt, w, h) <= len(raw)]
        if not fits: return
        sq   = [d for d in fits if d[0] == d[1]]
        w, h = max(sq or fits, key=lambda d: d[0] * d[1])
        dds  = _dds(fmt, w, h, raw[:_mip0(fmt, w, h)])
        if not dds: return
        im   = Image.open(io.BytesIO(dds)); im.load()
        im.convert("RGBA").save(dst_base + ".png")
    except Exception as e:
        print(f"  [warn] PNG decode failed for {os.path.basename(dst_base)}: {e}", file=sys.stderr)

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
def _check_prereqs(need_retoc=True):
    issues = []
    if not glob.glob(PAKS + "/pakchunk*.utoc"):
        issues.append(f"No pak files found at: {PAKS}")
    if need_retoc and not os.path.exists(RETOC):
        issues.append(f"retoc not found at: {RETOC}")
    if issues:
        for i in issues: print(f"[error] {i}", file=sys.stderr)
        sys.exit(1)

# ── list ───────────────────────────────────────────────────────────────────────
def cmd_list(arg):
    _check_prereqs(need_retoc=False)
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

    char_id    = _char_id(skin_id)
    dest_root  = os.path.abspath(os.path.join(ASSETS, "Marvel", "Content", "Marvel", "Characters", char_id, skin_id))
    print(f"  Destination: {dest_root}")

    by_cont = {}
    for p, c in entries: by_cont.setdefault(c, []).append(p)

    total = 0
    for cont, paths in sorted(by_cont.items()):
        print(f"  Extracting {len(paths)} texture(s) from {cont}...", file=sys.stderr)
        tmp = os.path.join(_WORK, "cli_import_tmp")
        shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
        try:
            flt = []
            for p in paths: flt += ["--filter", p]
            r = _run([RETOC, "unpack", f"{PAKS}/{cont}"] + flt + ["-o", tmp])
            if r.returncode != 0:
                msg = r.stderr.decode(errors="replace")[:400]
                print(f"  [warn] retoc exit {r.returncode}: {msg}", file=sys.stderr)
            for pak_path in paths:
                game_rel = _pak_rel(pak_path)              # Marvel/Content/.../T_foo
                src_base = os.path.join(tmp, *game_rel.split("/"))
                dst_base = os.path.join(ASSETS, *game_rel.split("/"))
                os.makedirs(os.path.dirname(dst_base), exist_ok=True)
                copied_this = 0
                for ext in (".uasset", ".uexp", ".ubulk"):
                    src = src_base + ext
                    if os.path.exists(src):
                        shutil.copy2(src, dst_base + ext); total += 1; copied_this += 1
                if copied_this:
                    _decode_png(dst_base)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"Extracted {total} file(s) -> {dest_root}")

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

# ── template-based staging (proven pak build path) ────────────────────────────
_TPL_REG   = None
_tpl_cache = {}

def _templates():
    global _TPL_REG
    if _TPL_REG is not None: return _TPL_REG
    _TPL_REG = {}
    rf = os.path.join(TPL_DIR, "registry.json")
    if os.path.exists(rf):
        for k, v in json.load(open(rf)).items():
            f, w, h = k.split("|"); _TPL_REG[(f, int(w), int(h))] = v
    return _TPL_REG

def _tpl_for(fmt, w, h):
    return _templates().get(("PF_" + fmt, w, h))

def _load_template(fn):
    if fn not in _tpl_cache:
        _tpl_cache[fn] = (open(os.path.join(TPL_DIR, fn + ".uasset"), "rb").read(),
                          open(os.path.join(TPL_DIR, fn + ".uexp"),   "rb").read())
    return _tpl_cache[fn]

def _sum(a):
    """Parse versioned UE package summary -> {field: (file_pos, value)} for all offsets that shift on rename."""
    p = [28]; F = {}
    def ri(nm=None):
        v = struct.unpack_from("<i", a, p[0])[0]; o = p[0]; p[0] += 4
        if nm: F[nm] = (o, v)
        return v
    def rq(nm=None):
        v = struct.unpack_from("<q", a, p[0])[0]; o = p[0]; p[0] += 8
        if nm: F[nm] = (o, v)
        return v
    def rs():
        n = ri(); p[0] += n if n > 0 else (-n * 2 if n < 0 else 0)
    ri("ths"); rs(); flags = ri(); feo = (flags & 0x80000000) != 0
    ri(); ri("names"); ri(); ri("soft")
    if not feo: rs()
    ri(); ri("gather"); ri(); ri("exports"); ri(); ri("imports"); ri("depends")
    ri(); ri("softpkg"); ri("search"); ri("thumb"); p[0] += 16
    if not feo: p[0] += 16
    gen = ri(); p[0] += gen * 8
    for _ in range(2): p[0] += 6; ri(); rs()
    ri(); ri(); ri(); ri(); ri("assetreg"); rq("bdso"); ri("worldtile")
    nck = ri(); p[0] += nck * 4; ri(); ri("preload"); ri(); rq("ptoc"); ri("dro")
    return F

def _rename(a, old_pkg, new_pkg, old_obj, new_obj):
    """Rename a versioned texture package: shift name-map entries and every dependent offset."""
    a = bytearray(a); old_ths = struct.unpack_from("<i", a, 28)[0]
    sl = struct.unpack_from("<i", a, 32)[0]
    a[32:32 + 4 + sl] = struct.pack("<i", len(new_pkg) + 1) + new_pkg.encode() + b"\x00"
    ds = len(new_pkg) - len(old_pkg)
    def repl(old, new):
        oe = struct.pack("<i", len(old) + 1) + old.encode() + b"\x00"
        ne = struct.pack("<i", len(new) + 1) + new.encode() + b"\x00"
        i = a.find(oe)
        if i < 0: raise RuntimeError("name not found in map: " + old)
        a[i:i + len(oe)] = ne; return len(ne) - len(oe)
    dn = repl(old_pkg, new_pkg)
    do = repl(old_obj, new_obj)
    total = ds + dn + do
    F = _sum(a)
    o, v = F["names"]; struct.pack_into("<i", a, o, v + ds)
    for k in ("soft", "gather", "exports", "imports", "depends", "softpkg", "assetreg", "worldtile", "preload", "dro"):
        o, v = F[k]
        if v > 0: struct.pack_into("<i", a, o, v + total)
    o, v = F["ths"]; struct.pack_into("<i", a, o, v + total)
    o, v = F["bdso"]; struct.pack_into("<q", a, o, v + total)
    ex = F["exports"][1] + total
    for q in range(ex, min(ex + 300, len(a) - 8)):
        if struct.unpack_from("<q", a, q)[0] == old_ths and 0 < struct.unpack_from("<q", a, q - 8)[0] < 20000000:
            struct.pack_into("<q", a, q, old_ths + total); break
    return bytes(a)

def _texconv_mip0(image_bytes, fmt, w, h):
    """Encode via texconv -> raw top-mip blocks. Returns None if texconv absent or fails."""
    f = _TCFMT.get(fmt)
    if not f or not os.path.exists(TEXCONV): return None
    tin  = os.path.abspath(os.path.join(_WORK, "_tc_in.png"))
    tout = os.path.abspath(os.path.join(_WORK, "_tc_out"))
    Image.open(io.BytesIO(image_bytes)).convert("RGBA").save(tin)
    shutil.rmtree(tout, ignore_errors=True); os.makedirs(tout)
    _run([TEXCONV, "-nologo", "-f", f, "-m", "1", "-w", str(w), "-h", str(h), "-o", tout, "-y", tin])
    dds = glob.glob(tout + "/*.dds")
    if not dds: return None
    d = open(dds[0], "rb").read(); off = 148 if d[84:88] == b"DX10" else 128
    return d[off:]

def encode_mip0(image_bytes, fmt, w, h):
    m = _texconv_mip0(image_bytes, fmt, w, h)
    if m is not None: return m
    pf = {"DXT1": "DXT1", "DXT5": "DXT5"}.get(fmt)
    if not pf: raise RuntimeError(f"{fmt} encode needs texconv.exe in Tools/ (Pillow only does DXT1/DXT5)")
    im = Image.open(io.BytesIO(image_bytes)).convert("RGBA").resize((w, h), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "DDS", pixel_format=pf)
    return b.getvalue()[128:]

def _tex_meta(game_rel):
    """Read format + top-mip dimensions from local .uasset/.uexp/.ubulk in assets/."""
    base  = os.path.join(ASSETS, *game_rel.split("/"))
    uasset = base + ".uasset"; uexp = base + ".uexp"; ubulk = base + ".ubulk"
    if not os.path.exists(uexp): raise RuntimeError("no .uexp found — run 'import' first")
    ab  = open(uasset, "rb").read() if os.path.exists(uasset) else b""
    fmt = next((f for f in ("DXT1", "DXT5", "BC7", "BC6H", "BC5", "BC4", "B8G8R8A8")
                if b"PF_" + f.encode() in ab), "DXT1")
    eb  = open(uexp, "rb").read()
    POW = {32, 64, 128, 256, 512, 1024, 2048, 4096, 8192}
    cands = set()
    for o in range(0, len(eb) - 8):
        x, y = struct.unpack_from("<ii", eb, o)
        if x in POW and y in POW: cands.add((x, y))
    raw  = open(ubulk, "rb").read() if os.path.exists(ubulk) else eb
    fits = [(w, h) for w, h in cands if _mip0(fmt, w, h) <= len(raw)]
    if not fits: raise RuntimeError("could not determine texture dimensions")
    sq = [d for d in fits if d[0] == d[1]]
    w, h = max(sq or fits, key=lambda d: d[0] * d[1])
    return fmt, w, h

def _stage_from_local(stage, game_rel):
    """Stage one texture for export using the proven template approach:
    rename a cooked template to the target package path, encode the PNG into its mip0 slot."""
    base     = os.path.join(ASSETS, *game_rel.split("/"))
    png_path = base + ".png"

    # Auto-decode PNG if import was run but PNG is missing (e.g. decode failed originally)
    if not os.path.exists(png_path):
        if not os.path.exists(base + ".uexp"):
            raise RuntimeError("no assets found — run 'import' first")
        _decode_png(base)
        if not os.path.exists(png_path):
            raise RuntimeError("PNG decode failed; check format support")

    fmt, w, h = _tex_meta(game_rel)

    tpl = _tpl_for(fmt, w, h)
    if not tpl:
        raise RuntimeError(
            f"no template for {fmt} {w}x{h} — add one to templates/ "
            f"(cook one {fmt} {w}x{h} texture in UE and add its registry entry)")

    png_bytes = open(png_path, "rb").read()
    sz        = _mip0(fmt, w, h)
    mip0_data = encode_mip0(png_bytes, fmt, w, h)
    if len(mip0_data) != sz:
        raise RuntimeError(f"encoded mip0 {len(mip0_data)} bytes != expected {sz}")

    tua, tue_bytes = _load_template(tpl["file"])
    new_pkg = "/Game/" + game_rel.split("Marvel/Content/", 1)[-1]
    new_obj = os.path.basename(game_rel)

    ue = bytearray(tue_bytes)
    pf_off = next((ue.find(b"PF_" + f.encode()) for f in
                   ("DXT1", "DXT5", "BC5", "BC7", "BC4", "BC6H", "B8G8R8A8")
                   if ue.find(b"PF_" + f.encode()) >= 0), -1)
    if pf_off < 0: raise RuntimeError("PF_ tag not found in template .uexp")
    L      = struct.unpack_from("<i", ue, pf_off - 4)[0]
    payoff = pf_off + L + 12
    ue[payoff:payoff + sz] = mip0_data

    nua = _rename(tua, tpl["pkg"], new_pkg, tpl["obj"], new_obj)
    dst = os.path.join(stage, *game_rel.split("/"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst + ".uasset", "wb").write(nua)
    open(dst + ".uexp",   "wb").write(bytes(ue))
    return f"{new_obj} ({fmt} {w}x{h})"

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
                desc = _stage_from_local(stage, game_rel)
                staged += 1
                print(f"  staged {label} -> {desc}")
            except Exception as e:
                skipped.append(f"{label}: {e}")
        if skipped:
            for s in skipped: print(f"  [warn] skipped: {s}", file=sys.stderr)
        if not staged:
            print("Nothing staged — check warnings above"); return

        os.makedirs(out_dir, exist_ok=True)
        r = _run([RETOC, "pack", stage, "-o", out_dir])
        if r.returncode != 0:
            print(f"retoc pack failed (exit {r.returncode}):\n"
                  f"{r.stderr.decode(errors='replace')[:500]}"); return

        base = os.path.join(out_dir, stem)
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
