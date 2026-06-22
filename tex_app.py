"""MR Texture Editor - browse/view (and soon replace) character skin textures, repack as a mod.
Sibling of mr_app.py: reuses its config (Paks + tools), io_lib, retoc pipeline, and WebView-EXE shell.
Run: python tex_app.py   (dev)   or via tex_editor_app.py / the built EXE."""
import os, sys, glob, re, struct, io, json, http.server, socketserver, threading, webbrowser, shutil, hashlib, base64, zipfile
ROOT = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT); sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + "/_work")
import mr_app  # reuse: config (PAKS/TOOLS), _run, io_lib, RETOC/UAG/CNW, ensure_mapping
from urllib.parse import urlparse, parse_qs
from PIL import Image
io_lib, _run = mr_app.io_lib, mr_app._run
PAKS, TOOLS, RETOC, UAG, CNW = mr_app.PAKS, mr_app.TOOLS, mr_app.RETOC, mr_app.UAG, mr_app.CNW
PORT = 8766
TCACHE = "_work/tex_cache"
def _key(path): return hashlib.md5(path.encode()).hexdigest()[:16]  # short key (avoids Windows MAX_PATH on deep texture paths)
EXTRACT_DIR = "extracted"  
MODS_DIR = "mods"          

_TEX = None
CACHE_VER = 2   # bump whenever enum logic changes, so old index.json caches auto-rebuild (e.g. the weapon-folder fix)
def enum_textures():
    global _TEX
    if _TEX is not None: return _TEX
    ck = TCACHE + "/index.json"
    if os.path.exists(ck):
        try:
            c = json.load(open(ck, encoding="utf-8"))
            if isinstance(c, dict) and c.get("_ver") == CACHE_VER: _TEX = c["d"]; return _TEX
        except Exception: pass
    skins = {}; seen = set()
    for utoc in sorted(glob.glob(PAKS + "/*.utoc")):
        try: t = io_lib.parse_toc(utoc); ents = io_lib.parse_dir_index(t)
        except Exception: continue
        cont = os.path.basename(utoc)
        for p, ud in ents:
            pl = p.lower()
            fn = pl.rsplit("/", 1)[-1]
            # capture EVERY texture under /Characters/: anything named T_* (UE texture convention), OR
            # anything inside a textures folder. Catches weapon/prop textures in oddly-named folders
            # (HandGun/, Weapon/, Tex/) and the game's MISSPELLED folders (Textrues/, Textrue/, Texures/)
            # that a literal "/textures/" match misses. Still excludes meshes (SK_/SM_), materials (MI_/M_), BPs.
            if not (fn.endswith(".uasset") and "/characters/" in pl and (fn.startswith("t_") or re.search(r"/textures?/", pl))): continue
            if pl in seen: continue
            m = re.search(r"/Characters/([^/]+)/([^/]+)/", p, re.I)
            if not m: continue
            seen.add(pl)
            key = m.group(1) + "/" + m.group(2)
            skins.setdefault(key, {"char": m.group(1), "skin": m.group(2), "textures": []})
            skins[key]["textures"].append({"name": os.path.basename(p)[:-7], "path": p, "cont": cont})
    _TEX = {k: skins[k] for k in sorted(skins) if skins[k]["textures"]}
    os.makedirs(TCACHE, exist_ok=True); json.dump({"_ver": CACHE_VER, "d": _TEX}, open(ck, "w"))
    return _TEX

BLOCK = {"DXT1": 8, "BC4": 8, "DXT5": 16, "BC5": 16, "BC7": 16, "BC6H": 16}
DXGI = {"BC4": 80, "BC5": 83, "BC6H": 95, "BC7": 98}
def _mip0(fmt, w, h):
    if fmt == "B8G8R8A8": return w * h * 4
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK.get(fmt, 16)
def _dds(fmt, w, h, data):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    base = struct.pack("<7I", 124, flags, h, w, len(data), 0, 1) + b"\0" * 44
    if fmt == "B8G8R8A8":
        pf = struct.pack("<2I", 32, 0x41) + b"\0" * 4 + struct.pack("<5I", 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
        return b"DDS " + base + pf + struct.pack("<5I", 0x1000, 0, 0, 0, 0) + data
    if fmt in ("DXT1", "DXT5"):
        pf = struct.pack("<2I", 32, 0x4) + fmt.encode() + b"\0" * 20
        return b"DDS " + base + pf + struct.pack("<5I", 0x1000, 0, 0, 0, 0) + data
    if fmt in DXGI:
        pf = struct.pack("<2I", 32, 0x4) + b"DX10" + b"\0" * 20
        return b"DDS " + base + pf + struct.pack("<5I", 0x1000, 0, 0, 0, 0) + struct.pack("<5I", DXGI[fmt], 3, 0, 1, 0) + data
    return None

_skinlocks = {}; _skinguard = threading.Lock()
def _skin_key(path):
    m = re.search(r"/Characters/([^/]+)/([^/]+)/", path, re.I)
    return f"{m.group(1)}/{m.group(2)}" if m else "_misc"
def ensure_skin(skinkey):
    """Unpack ALL of a skin's textures in ONE retoc call (no --game-paks-dir - textures don't need it). Cached + locked."""
    sd = f"{TCACHE}/s_{hashlib.md5(skinkey.encode()).hexdigest()[:12]}"
    if os.path.exists(sd + "/.done"): return sd
    with _skinguard:
        lock = _skinlocks.setdefault(skinkey, threading.Lock())
    with lock:
        if os.path.exists(sd + "/.done"): return sd
        os.makedirs(sd, exist_ok=True)
        info = enum_textures().get(skinkey)
        if info:
            by_cont = {}
            for t in info["textures"]: by_cont.setdefault(t["cont"], []).append(t["path"])
            for cont, paths in by_cont.items():
                flt = []
                for p in paths: flt += ["--filter", p]
                _run([RETOC, "unpack", f"{PAKS}/{cont}"] + flt + ["-o", sd])
        open(sd + "/.done", "w").write("1")
    return sd
def _rel(path):
    """Container path -> canonical mount-relative path (strips leading ../../../ and the .uasset ext).
    e.g. '../../../Marvel/Content/.../T_x.uasset' -> 'Marvel/Content/.../T_x'. Forward slashes."""
    r = re.sub(r"^(\.\./)+", "", path.replace("\\", "/"))
    return r[:-7] if r.lower().endswith(".uasset") else r
def _locate(sd, path):
    """Resolve a texture's unpacked base path by its EXACT full path (NOT basename).
    A skin can hold two textures with the same filename in different subfolders (e.g.
    .../Textures/T_x and .../1042_Girl/Textures/T_x) - basename matching grabs the wrong
    one, giving the wrong mount path -> wrong package id -> the mod silently fails to override."""
    b = os.path.join(sd, *_rel(path).split("/"))
    return b if os.path.exists(b + ".uexp") else None

def tex_info(cont, path):
    """Return {fmt, w, h, ua, uexp, ubulk}."""
    sd = ensure_skin(_skin_key(path))
    b = _locate(sd, path)
    if not b: raise RuntimeError("texture not found after unpack")
    uexp = b + ".uexp"; asset = b + ".uasset"; ubulk = b + ".ubulk"
    ab = open(asset, "rb").read() if os.path.exists(asset) else b""
    fmt = next((f for f in ("DXT1", "DXT5", "BC7", "BC6H", "BC5", "BC4", "B8G8R8A8") if b"PF_" + f.encode() in ab), "DXT1")
    eb = open(uexp, "rb").read()
    POW = {32, 64, 128, 256, 512, 1024, 2048, 4096, 8192}
    cands = set()
    for o in range(0, len(eb) - 8):
        x, y = struct.unpack_from("<ii", eb, o)
        if x in POW and y in POW: cands.add((x, y))
    src = open(ubulk, "rb").read() if os.path.exists(ubulk) else eb
    fits = [(w, h) for w, h in cands if _mip0(fmt, w, h) <= len(src)]
    if not fits: raise RuntimeError("could not determine dimensions")
    sq = [d for d in fits if d[0] == d[1]]
    w, h = max(sq or fits, key=lambda d: d[0] * d[1])
    return {"fmt": fmt, "w": w, "h": h, "uexp": uexp, "asset": asset, "ubulk": ubulk if os.path.exists(ubulk) else None}

def decode_texture(cont, path):
    key = _key(path); png = f"{TCACHE}/{key}.png"
    if os.path.exists(png): return png
    inf = tex_info(cont, path)
    src = open(inf["ubulk"], "rb").read() if inf["ubulk"] else open(inf["uexp"], "rb").read()
    sz = _mip0(inf["fmt"], inf["w"], inf["h"])
    dds = _dds(inf["fmt"], inf["w"], inf["h"], src[:sz])
    if not dds: raise RuntimeError("unsupported format " + inf["fmt"])
    im = Image.open(io.BytesIO(dds)); im.load()
    im.convert("RGBA").save(png)
    return png

TEXCONV = os.path.join(TOOLS, "texconv.exe")
TCFMT = {"DXT1": "BC1_UNORM", "DXT5": "BC3_UNORM", "BC4": "BC4_UNORM", "BC5": "BC5_UNORM", "BC7": "BC7_UNORM", "BC6H": "BC6H_UF16"}
def _texconv_mip0(image_bytes, fmt, w, h):
    """Encode via Microsoft texconv -> raw top-mip blocks (handles every BC format). None if texconv absent/unsupported."""
    f = TCFMT.get(fmt)
    if not f or not os.path.exists(TEXCONV): return None
    os.makedirs(TCACHE, exist_ok=True)
    tin = os.path.abspath(f"{TCACHE}/_tin.png"); Image.open(io.BytesIO(image_bytes)).convert("RGBA").save(tin)
    out = f"{TCACHE}/_tc"; shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
    _run([TEXCONV, "-nologo", "-f", f, "-m", "1", "-w", str(w), "-h", str(h), "-o", os.path.abspath(out), "-y", tin])
    dds = glob.glob(out + "/*.dds")
    if not dds: return None
    d = open(dds[0], "rb").read(); off = 148 if d[84:88] == b"DX10" else 128
    return d[off:]
def encode_mip0(image_bytes, fmt, w, h):
    m = _texconv_mip0(image_bytes, fmt, w, h)
    if m is not None: return m
    pf = {"DXT1": "DXT1", "DXT5": "DXT5"}.get(fmt)
    if not pf: raise RuntimeError(fmt + " encode needs texconv.exe in Tools/ (Pillow only does DXT1/DXT5)")
    im = Image.open(io.BytesIO(image_bytes)).convert("RGBA").resize((w, h), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "DDS", pixel_format=pf)
    return b.getvalue()[128:]  

PKG_TAG = b"\xc1\x83\x2a\x9e"
TPL_DIR = ROOT + "/templates"
# Template LIBRARY: one correctly-cooked versioned texture per (format, W, H). The proven build path is
# rename-a-template + swap-its-mip0, which only works when the template matches the target's format+size
# exactly. registry.json maps "PF_<fmt>|W|H" -> {file, pkg (its /Game/ name), obj}. Drop in a new
# template + registry entry (cook ONE texture of that format in UE) and that format works instantly -
# this is how the editor becomes universal across ALL formats (BC5 normals, BC7, etc.).
_TPL_REG = None; _tpl_cache = {}
def _templates():
    global _TPL_REG
    if _TPL_REG is None:
        _TPL_REG = {}
        rf = f"{TPL_DIR}/registry.json"
        if os.path.exists(rf):
            for k, v in json.load(open(rf)).items():
                f, w, h = k.split("|"); _TPL_REG[(f, int(w), int(h))] = v
    return _TPL_REG
def _tpl_for(fmt, w, h):
    return _templates().get(("PF_" + fmt, w, h))
def _load_template(fn):
    if fn not in _tpl_cache:
        _tpl_cache[fn] = (open(f"{TPL_DIR}/{fn}.uasset", "rb").read(), open(f"{TPL_DIR}/{fn}.uexp", "rb").read())
    return _tpl_cache[fn]

def _sum(a):
    """Parse a versioned legacy summary -> {field: (file_pos, value)} for every offset that must shift on rename."""
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
    for _ in range(2): p[0] += 6; ri(); rs()        # 2x FEngineVersion (u16 major/minor/patch, u32 changelist, FString branch)
    ri(); ri(); ri(); ri(); ri("assetreg"); rq("bdso"); ri("worldtile")
    nck = ri(); p[0] += nck * 4; ri(); ri("preload"); ri(); rq("ptoc"); ri("dro")
    return F

def _rename(a, old_pkg, new_pkg, old_obj, new_obj):
    """Rename a versioned texture package: the package name lives in BOTH the summary FString and the
    name map; the object name in the name map. All can change length. We swap the strings then shift
    every summary offset that points past the name map (+total) and the name-map start (+summary delta),
    plus the export's serial_offset. PROVEN engine-valid."""
    a = bytearray(a); old_ths = struct.unpack_from("<i", a, 28)[0]
    sl = struct.unpack_from("<i", a, 32)[0]
    a[32:32 + 4 + sl] = struct.pack("<i", len(new_pkg) + 1) + new_pkg.encode() + b"\x00"   # summary package_name FString
    ds = len(new_pkg) - len(old_pkg)
    def repl(old, new):
        oe = struct.pack("<i", len(old) + 1) + old.encode() + b"\x00"
        ne = struct.pack("<i", len(new) + 1) + new.encode() + b"\x00"
        i = a.find(oe)
        if i < 0: raise RuntimeError("name not found in map: " + old)
        a[i:i + len(oe)] = ne; return len(ne) - len(oe)
    dn = repl(old_pkg, new_pkg)                       # name-map package entry
    do = repl(old_obj, new_obj)                       # name-map object entry
    total = ds + dn + do
    F = _sum(a)
    o, v = F["names"]; struct.pack_into("<i", a, o, v + ds)   # name map start shifts only by the summary growth
    for k in ("soft", "gather", "exports", "imports", "depends", "softpkg", "assetreg", "worldtile", "preload", "dro"):
        o, v = F[k]
        if v > 0: struct.pack_into("<i", a, o, v + total)
    o, v = F["ths"]; struct.pack_into("<i", a, o, v + total)
    o, v = F["bdso"]; struct.pack_into("<q", a, o, v + total)
    ex = F["exports"][1] + total                      # export map moved; find serial_offset (== old header size)
    for q in range(ex, min(ex + 300, len(a) - 8)):
        if struct.unpack_from("<q", a, q)[0] == old_ths and 0 < struct.unpack_from("<q", a, q - 8)[0] < 20000000:
            struct.pack_into("<q", a, q, old_ths + total); break
    return bytes(a)

def _stage_one(stage, cont, path, image_bytes):
    """Build one texture override: pick the matching-format template, rename it to the target, swap its mip0."""
    inf = tex_info(cont, path)
    tpl = _tpl_for(inf["fmt"], inf["w"], inf["h"])
    if not tpl:
        raise RuntimeError(f"no template for {inf['fmt']} {inf['w']}x{inf['h']} - add one to templates/ (cook 1 texture of this format)")
    supported = set(TCFMT) if os.path.exists(TEXCONV) else {"DXT1", "DXT5"}
    if inf["fmt"] not in supported: raise RuntimeError(f"{inf['fmt']} not encodable (put texconv.exe in Tools/)")
    Image.open(io.BytesIO(image_bytes)).verify()
    sz = _mip0(inf["fmt"], inf["w"], inf["h"])
    mip0 = encode_mip0(image_bytes, inf["fmt"], inf["w"], inf["h"])
    if len(mip0) != sz: raise RuntimeError(f"encoded size {len(mip0)} != {sz}")
    tua, tue = _load_template(tpl["file"])
    rel = _rel(path); new_pkg = "/Game/" + rel.split("Marvel/Content/", 1)[-1]; new_obj = os.path.basename(rel)
    ue = bytearray(tue)
    pf = next((ue.find(b"PF_" + f.encode()) for f in ("DXT1", "DXT5", "BC5", "BC7", "BC4", "BC6H", "B8G8R8A8") if ue.find(b"PF_" + f.encode()) >= 0), -1)
    L = struct.unpack_from("<i", ue, pf - 4)[0]; payoff = pf + L + 12
    ue[payoff:payoff + sz] = mip0                     # template matches target format+size, so payload size is identical
    nua = _rename(tua, tpl["pkg"], new_pkg, tpl["obj"], new_obj)
    dst = f"{stage}/{rel}"; os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst + ".uasset", "wb").write(nua); open(dst + ".uexp", "wb").write(bytes(ue))
    return f"{new_obj} ({inf['fmt']} {inf['w']}x{inf['h']})"

def build_textures(skin, edits):
    """edits: [{cont, path, image(bytes)}] -> ONE inline-mip mod (renders in-game; package ids match vanilla)."""
    if not edits: return {"ok": False, "msg": "Nothing staged - stage at least one replacement first."}
    info = enum_textures().get(skin, {"textures": []})
    by_name = {}
    for t in info["textures"]: by_name.setdefault(os.path.basename(t["path"])[:-7], []).append(t)
    staged_paths = {e["path"] for e in edits}
    expanded = list(edits)
    for e in edits:
        for tw in by_name.get(os.path.basename(e["path"])[:-7], []):
            if tw["path"] not in staged_paths:
                expanded.append({"cont": tw["cont"], "path": tw["path"], "image": e["image"]})
                staged_paths.add(tw["path"])
    edits = expanded
    modname = re.sub(r"\W+", "_", skin).strip("_")
    stage = f"{TCACHE}/bstage/{modname}"; shutil.rmtree(f"{TCACHE}/bstage", ignore_errors=True); os.makedirs(stage, exist_ok=True)
    applied, skipped = [], []
    for e in edits:
        try: applied.append(_stage_one(stage, e["cont"], e["path"], e["image"]))
        except Exception as ex: skipped.append(os.path.basename(e["path"])[:-7] + ": " + str(ex))
    if not applied: return {"ok": False, "msg": "Nothing could be built:\n  " + "\n  ".join(skipped)}
    out = MODS_DIR; os.makedirs(out, exist_ok=True)
    for f in glob.glob(f"{out}/{modname}_*"):
        try: os.remove(f)
        except OSError: pass
    _run([RETOC, "pack", stage, "-o", out])
    made = sorted(glob.glob(f"{out}/{modname}_*_P.utoc"))
    if not made: return {"ok": False, "msg": "retoc pack produced no container"}
    base = made[-1][:-5]
    return {"ok": True, "applied": applied, "skipped": skipped,
            "output": os.path.abspath(base).replace("\\", "/") + ".{pak,ucas,utoc}",
            "note": f"Versioned mod ({len(applied)} texture(s), both twins) - renders in-game. Copy the 3 files into ~mods."}

def build_texture(cont, path, image_bytes):
    """Encode the new image as mip0, swap it into the .ubulk (keep lower mips = same size), retoc-pack as a mod."""
    inf = tex_info(cont, path)
    supported = set(TCFMT) if os.path.exists(TEXCONV) else {"DXT1", "DXT5"}
    if inf["fmt"] not in supported:
        return {"ok": False, "msg": f"{inf['fmt']} not encodable here - put texconv.exe in Tools/ for BC5/BC7."}
    if not inf["ubulk"]:
        return {"ok": False, "msg": "this texture stores mips inline (no .ubulk) - not supported yet."}
    try: Image.open(io.BytesIO(image_bytes)).verify()
    except Exception as e: return {"ok": False, "msg": "bad image: " + str(e)}
    sz = _mip0(inf["fmt"], inf["w"], inf["h"])
    try: mip0 = encode_mip0(image_bytes, inf["fmt"], inf["w"], inf["h"])
    except Exception as e: return {"ok": False, "msg": str(e)}
    if len(mip0) != sz: return {"ok": False, "msg": f"encoded mip0 {len(mip0)} != expected {sz}"}
    orig = open(inf["ubulk"], "rb").read()
    newbulk = mip0 + orig[sz:]   # replace top mip, keep the streamed lower mips
    sd = ensure_skin(_skin_key(path)); src = _locate(sd, path); rel = _rel(path); name = os.path.basename(rel)
    stage = f"{TCACHE}/bstage/{name}"; shutil.rmtree(f"{TCACHE}/bstage", ignore_errors=True)
    dst = f"{stage}/{rel}"; os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(src + ".uasset", dst + ".uasset"); shutil.copy(src + ".uexp", dst + ".uexp")
    open(dst + ".ubulk", "wb").write(newbulk)
    out = f"{TCACHE}/out"; os.makedirs(out, exist_ok=True)
    for f in glob.glob(f"{out}/{name}_*"):
        try: os.remove(f)
        except OSError: pass
    _run([RETOC, "pack", stage, "-o", out])
    made = sorted(glob.glob(f"{out}/{name}_*_P.utoc"))
    if not made: return {"ok": False, "msg": "retoc pack produced no container"}
    base = made[-1][:-5]
    return {"ok": True, "output": os.path.abspath(base).replace("\\", "/") + ".{pak,ucas,utoc}",
            "fmt": inf["fmt"], "dims": f"{inf['w']}x{inf['h']}",
            "note": "Texture mod built (new top mip, byte-faithful). Copy the 3 files into your ~mods folder. Won't boot until the -64512 engine fix."}

# ---------- VFX recolor: material-instance color + scalar params ----------
# Color/scalar values live in the .uexp; UAG round-trips the .uexp byte-identically but
# CORRUPTS the .uasset name hashes. So: edit values in UAG json -> fromjson (faithful .uexp)
# -> pair with the VANILLA .uasset -> retoc pack. Proven byte-faithful round-trip.
def _enum_mis(cache_name, ver, want):
    """Index material INSTANCES (MI_*; master M_ are graphs w/ no params). want(pathlower, path) -> (char, group)
    or None to skip. Result {char:{char, mats:[{name,path,cont,group}]}}, cached + version-tagged."""
    ck = TCACHE + "/" + cache_name
    if os.path.exists(ck):
        try:
            c = json.load(open(ck, encoding="utf-8"))
            if isinstance(c, dict) and c.get("_ver") == ver: return c["d"]
        except Exception: pass
    chars = {}; seen = set()
    for utoc in sorted(glob.glob(PAKS + "/*.utoc")):
        try: t = io_lib.parse_toc(utoc); ents = io_lib.parse_dir_index(t)
        except Exception: continue
        cont = os.path.basename(utoc)
        for p, ud in ents:
            pl = p.lower(); fn = pl.rsplit("/", 1)[-1]
            if not (fn.startswith("mi_") and fn.endswith(".uasset")) or pl in seen: continue
            cg = want(pl, p)
            if not cg: continue
            seen.add(pl)
            char, grp = cg
            chars.setdefault(char, {"char": char, "mats": []})
            chars[char]["mats"].append({"name": os.path.basename(p)[:-7], "path": p, "cont": cont, "group": grp})
    d = {k: chars[k] for k in sorted(chars) if chars[k]["mats"]}
    os.makedirs(TCACHE, exist_ok=True); json.dump({"_ver": ver, "d": d}, open(ck, "w"))
    return d

VFX_CACHE_VER = 1; MATS_CACHE_VER = 1
_VFX = None; _MATS = None
def enum_vfx():
    """Particle/effect material instances under /VFX/Materials/Characters/, grouped by subfolder."""
    global _VFX
    if _VFX is None:
        def want(pl, p):
            if "/vfx/materials/characters/" not in pl: return None
            m = re.search(r"/VFX/Materials/Characters/([^/]+)/(.*)$", p, re.I)
            if not m: return None
            rest = m.group(2)
            return (m.group(1), rest.rsplit("/", 1)[0] if "/" in rest else "(root)")
        _VFX = _enum_mis("vfx_index.json", VFX_CACHE_VER, want)
    return _VFX
def enum_mats():
    """Character mesh material instances under /Characters/<char>/<skin>/Materials/ (head, body, equip, weapon),
    grouped by skin. These hold EmissiveColor/EmissiveStrength etc. - same editable Vector/Scalar params as VFX."""
    global _MATS
    if _MATS is None:
        def want(pl, p):
            if "/vfx/" in pl: return None
            m = re.search(r"/Characters/([^/]+)/([^/]+)/Materials/(.*)$", p, re.I)
            if not m: return None
            return (m.group(1), m.group(2) + ("/Lobby" if "lobby/" in m.group(3).lower() else ""))
        _MATS = _enum_mis("mats_index.json", MATS_CACHE_VER, want)
    return _MATS

def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def _ex_props(e): return e.get("Data") or e.get("Value") or []
def _named(lst, n):
    for p in lst or []:
        if isinstance(p, dict) and p.get("Name") == n: return p
    return None
def _vfx_unpack(cont, path):
    d = f"{TCACHE}/vfx_{_key(path)}"; base = os.path.join(d, *_rel(path).split("/"))
    if os.path.exists(base + ".uexp"): return base
    os.makedirs(d, exist_ok=True)
    _run([RETOC, "unpack", f"{PAKS}/{cont}", "--filter", path, "-o", d])
    return base if os.path.exists(base + ".uexp") else None
def _vfx_json(cont, path):
    base = _vfx_unpack(cont, path)
    if not base: raise RuntimeError("unpack failed")
    j = base + ".uag.json"
    if not os.path.exists(j): _run([UAG, "tojson", base + ".uasset", j, "VER_UE5_3", "Marvel_S8.5"])
    return base, j
def _mi_params(d):
    """Return (colors, scalars) from a UAG MaterialInstance json dict."""
    ex = d["Exports"][0]
    vp = _named(_ex_props(ex), "VectorParameterValues"); sp = _named(_ex_props(ex), "ScalarParameterValues")
    colors = []; scalars = []
    for e in (vp or {}).get("Value", []):
        pin = _named(e["Value"], "ParameterInfo"); nm = (_named(pin["Value"], "Name") or {}).get("Value") if pin else None
        pv = _named(e["Value"], "ParameterValue")
        lc = pv["Value"][0]["Value"] if pv and pv.get("Value") else None
        if nm and isinstance(lc, dict) and "R" in lc:
            colors.append({"name": nm, "rgba": [round(_f(lc["R"]), 5), round(_f(lc["G"]), 5), round(_f(lc["B"]), 5), round(_f(lc["A"]), 5)]})
    for e in (sp or {}).get("Value", []):
        pin = _named(e["Value"], "ParameterInfo"); nm = (_named(pin["Value"], "Name") or {}).get("Value") if pin else None
        pv = _named(e["Value"], "ParameterValue")
        val = pv.get("Value") if pv else None
        try: val = float(val)
        except (TypeError, ValueError): continue
        if nm: scalars.append({"name": nm, "value": round(val, 5)})
    return colors, scalars
def vfx_params(cont, path):
    base, j = _vfx_json(cont, path)
    colors, scalars = _mi_params(json.load(open(j, encoding="utf-8")))
    return {"colors": colors, "scalars": scalars}

_TEXLOC = None
def _texloc():
    """Global locator of every T_* texture: lowercased mount-rel path (no ext) -> [cont, container_path].
    Needed because an MI's mask lives in /VFX/Textures/, which isn't in the (skin-only) texture index."""
    global _TEXLOC
    if _TEXLOC is not None: return _TEXLOC
    ck = TCACHE + "/texloc.json"
    if os.path.exists(ck):
        try: _TEXLOC = json.load(open(ck, encoding="utf-8")); return _TEXLOC
        except Exception: pass
    loc = {}
    for utoc in sorted(glob.glob(PAKS + "/*.utoc")):
        try: t = io_lib.parse_toc(utoc); ents = io_lib.parse_dir_index(t)
        except Exception: continue
        cont = os.path.basename(utoc)
        for p, ud in ents:
            b = p.rsplit("/", 1)[-1].lower()
            if b.startswith("t_") and b.endswith(".uasset"):
                loc.setdefault(re.sub(r"^(\.\./)+", "", p).lower()[:-7], [cont, p])
    _TEXLOC = loc
    os.makedirs(TCACHE, exist_ok=True); json.dump(_TEXLOC, open(ck, "w"))
    return _TEXLOC
_MASK = {}
def _mi_mask(cont, path):
    """Resolve the MI's referenced mask texture (prefer _M / 'mask' names, else any T_) -> [tex_cont, tex_path] or None.
    The referenced texture path appears as a /Game/... string in the unpacked MI when retoc could name-resolve it
    (atlas/curve-driven materials have no mask texture -> None)."""
    if path in _MASK: return _MASK[path]
    res = None
    try:
        _, j = _vfx_json(cont, path)
        txt = open(j, encoding="utf-8").read()
        texs = sorted(set(p for p in re.findall(r'/Game/[A-Za-z0-9_/]+', txt) if re.search(r'/T_[A-Za-z0-9_]+$', p)))
        if texs:
            mask = (next((p for p in texs if p.lower().endswith("_m") or "mask" in p.lower() or "maske" in p.lower()), None)
                    or next((p for p in texs if p.lower().endswith("_d")), None) or texs[0])   # _M mask, else _D diffuse, else any
            res = _texloc().get(("marvel/content" + mask[5:]).lower())
    except Exception: res = None
    _MASK[path] = res
    return res
def vfx_mask_png(cont, path):
    r = _mi_mask(cont, path)
    if not r: return None
    try: return decode_texture(r[0], r[1])
    except Exception: return None

def _apply_mi_edits(d, colors, scalars):
    ex = d["Exports"][0]
    vp = _named(_ex_props(ex), "VectorParameterValues"); sp = _named(_ex_props(ex), "ScalarParameterValues")
    for e in (vp or {}).get("Value", []):
        pin = _named(e["Value"], "ParameterInfo"); nm = (_named(pin["Value"], "Name") or {}).get("Value") if pin else None
        if nm in colors:
            lc = _named(e["Value"], "ParameterValue")["Value"][0]["Value"]
            r, g, b, a = colors[nm]; lc["R"], lc["G"], lc["B"], lc["A"] = float(r), float(g), float(b), float(a)
    for e in (sp or {}).get("Value", []):
        pin = _named(e["Value"], "ParameterInfo"); nm = (_named(pin["Value"], "Name") or {}).get("Value") if pin else None
        if nm in scalars: _named(e["Value"], "ParameterValue")["Value"] = float(scalars[nm])
def _stage_vfx_one(stage, e):
    """Stage one MI recolor: vanilla .uasset + UAG-edited .uexp. Returns the object name."""
    base, j = _vfx_json(e["cont"], e["path"])
    d = json.load(open(j, encoding="utf-8"))
    _apply_mi_edits(d, e.get("colors", {}), e.get("scalars", {}))
    ej = base + ".edit.json"; json.dump(d, open(ej, "w"))
    _run([UAG, "fromjson", ej, base + ".edit.uasset", "Marvel_S8.5"])   # writes .edit.uasset + .edit.uexp
    if not os.path.exists(base + ".edit.uexp"): raise RuntimeError("fromjson produced no uexp")
    rel = _rel(e["path"]); dst = f"{stage}/{rel}"; os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(base + ".uasset", dst + ".uasset")    # VANILLA uasset (UAG's is hash-corrupt)
    shutil.copy(base + ".edit.uexp", dst + ".uexp")   # edited values only
    return os.path.basename(rel)

def _expand_twins(tex_edits):
    """Add same-basename texture twins within each edit's OWN skin (lobby + in-match versions)."""
    out = list(tex_edits); seen = {e["path"] for e in tex_edits}
    for e in list(tex_edits):
        info = enum_textures().get(_skin_key(e["path"]), {"textures": []})
        bn = os.path.basename(e["path"])[:-7]
        for t in info["textures"]:
            if os.path.basename(t["path"])[:-7] == bn and t["path"] not in seen:
                out.append({"cont": t["cont"], "path": t["path"], "image": e["image"]}); seen.add(t["path"])
    return out

def build_all(tex_edits, vfx_edits):
    """Stage ALL staged texture + VFX edits (across ANY character/skin) into ONE container - a single round trip.
    tex_edits: [{cont,path,image(bytes)}] ; vfx_edits: [{cont,path,colors:{name:[r,g,b,a]},scalars:{name:val}}]."""
    if not tex_edits and not vfx_edits: return {"ok": False, "msg": "Nothing staged - stage a texture or a VFX color/scalar first."}
    modname = "MRMod"
    stage = f"{TCACHE}/astage/{modname}"; shutil.rmtree(f"{TCACHE}/astage", ignore_errors=True); os.makedirs(stage, exist_ok=True)
    applied, skipped = [], []
    for e in _expand_twins(tex_edits):
        try: applied.append("tex  " + _stage_one(stage, e["cont"], e["path"], e["image"]))
        except Exception as ex: skipped.append("tex " + os.path.basename(e["path"])[:-7] + ": " + str(ex))
    for e in vfx_edits:
        try: applied.append("mat  " + _stage_vfx_one(stage, e))
        except Exception as ex: skipped.append("mat " + os.path.basename(e["path"])[:-7] + ": " + str(ex))
    if not applied: return {"ok": False, "msg": "Nothing could be built:\n  " + "\n  ".join(skipped)}
    out = MODS_DIR; os.makedirs(out, exist_ok=True)
    for f in glob.glob(f"{out}/{modname}_*"):
        try: os.remove(f)
        except OSError: pass
    _run([RETOC, "pack", stage, "-o", out])
    made = sorted(glob.glob(f"{out}/{modname}_*_P.utoc"))
    if not made: return {"ok": False, "msg": "retoc pack produced no container"}
    base = made[-1][:-5]
    nt = sum(a.startswith("tex") for a in applied); nv = sum(a.startswith("mat") for a in applied)
    return {"ok": True, "applied": applied, "skipped": skipped,
            "output": os.path.abspath(base).replace("\\", "/") + ".{pak,ucas,utoc}",
            "note": f"ONE mod: {nt} texture(s) + {nv} material(s), byte-faithful. Copy the 3 files into ~mods."}

def clear_cache():
    """Wipe all editor caches (texture/vfx indexes, decoded previews, unpacked skins/MIs) + reset in-memory indexes,
    so the next request re-scans the game fresh. Does NOT touch built mods or staged edits (those live in the browser)."""
    global _TEX, _VFX, _MATS, _TEXLOC, _MASK
    _TEX = None; _VFX = None; _MATS = None; _TEXLOC = None; _MASK = {}
    n = 0
    if os.path.isdir(TCACHE):
        for name in os.listdir(TCACHE):
            p = os.path.join(TCACHE, name)
            try:
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
                n += 1
            except OSError: pass
    return {"ok": True, "removed": n}

HTML = r"""<!doctype html><meta charset=utf-8><title>MR Texture Editor</title><style>
:root{--ln:#6b6b8a;--mut:#d8dbf0;--acc:#9ab2ff;--mod:#ffd400}
*{box-sizing:border-box}body{margin:0;background:#000;color:#fff;font:700 14px/1.5 Segoe UI,system-ui,sans-serif}
.bar{display:flex;align-items:center;gap:12px;padding:11px 18px;background:#000;border-bottom:1px solid var(--ln)}
.bar b{font-size:16px}.tagb{color:var(--acc);font-size:11px;border:1.5px solid var(--acc);border-radius:9px;padding:1px 8px}
select,button,input{background:#000;color:#fff;border:1px solid var(--ln);border-radius:7px;padding:7px 11px;font:inherit;font-weight:700}
button{cursor:pointer}button:hover{border-color:#9a9ac0}.sw{margin-left:auto;color:var(--mut);font-size:12px}
.go{background:var(--acc);border-color:var(--acc);color:#06080f}
.cfg{display:none;position:fixed;top:52px;right:14px;width:430px;background:#0a0c14;border:1px solid var(--acc);border-radius:10px;padding:14px 16px;z-index:120;box-shadow:0 12px 40px #000d}
.cfg.on{display:block}.cfg h3{margin:0 0 8px}.cfg label{display:block;font-size:12px;color:var(--mut);margin-top:10px}
.cfg input{display:block;width:100%;margin-top:3px}.cfg .row{font-size:12px;margin-top:8px}.ok{color:#7CFC9B}.bad{color:#ff7a7a}
.page{padding:14px}#lb{position:fixed;top:0;left:0;right:0;height:3px;z-index:200;overflow:hidden;display:none}#lb.on{display:block}
#lb::before{content:'';position:absolute;height:100%;width:35%;background:var(--acc);animation:lbs 1.1s ease-in-out infinite}@keyframes lbs{0%{left:-35%}50%{left:55%}100%{left:100%}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.card{background:#0d0f17;border:1px solid var(--ln);border-radius:10px;padding:8px;cursor:pointer}.card:hover{border-color:var(--acc)}
.card.ed{background:#000;border:2px solid var(--mod);box-shadow:0 0 0 2px var(--mod),0 0 12px #ffd40066}
.card.ed .n{color:var(--mod);font-weight:800}
.card img{width:100%;aspect-ratio:1;object-fit:contain;background:#111522 url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2224%22 height=%2224%22><text x=%2212%22 y=%2216%22 fill=%22%23445%22 font-size=%2210%22 text-anchor=%22middle%22>...</text></svg>') center no-repeat;border-radius:6px;image-rendering:pixelated}
.card .n{font-size:11px;color:var(--mut);margin-top:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal{display:none;position:fixed;inset:0;background:#000c;z-index:100;align-items:center;justify-content:center}.modal.on{display:flex}
.mbox{background:#0d0f17;border:1px solid var(--acc);border-radius:12px;padding:16px;max-width:90vw;max-height:90vh;overflow:auto}
.mbox img{max-width:80vw;max-height:70vh;background:#111522;border-radius:8px;image-rendering:pixelated}
.modet{display:flex;gap:4px}.mt{padding:6px 13px}.mt.on{background:var(--acc);border-color:var(--acc);color:#06080f}
.card.mi{cursor:pointer}.card .g{font-size:10px;color:var(--ln);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.swrow{display:flex;gap:3px;margin-top:6px;min-height:14px}.swrow i{width:14px;height:14px;border-radius:3px;border:1px solid #333}
.vsec{font-size:12px;color:var(--acc);margin:12px 0 4px;border-bottom:1px solid var(--ln);padding-bottom:3px}
.vrow{display:flex;align-items:center;gap:9px;margin:6px 0;flex-wrap:wrap}
.vrow label{min-width:158px;color:var(--mut);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vrow input[type=color]{width:34px;height:28px;padding:2px}.vnum{width:74px}.vrange{width:150px;accent-color:var(--acc)}
.vrow .tag{font-size:10px;color:var(--ln)}.vbox{min-width:540px}
</style>
<div id=lb></div>
<div class=bar><b>MR Texture Editor</b><span class=tagb>PUBLIC ALPHA</span>
<span class=modet><button id=mt_tex class="mt on" onclick="setMode('tex')">Textures</button><button id=mt_vfx class=mt onclick="setMode('vfx')">VFX</button><button id=mt_mats class=mt onclick="setMode('mats')">Mesh</button></span>
<select id=char></select><select id=skin></select><input id=q placeholder="filter (D/N/ORM...)" style="width:150px">
<button id=exb onclick=extractAll()>Extract all PNG</button><button class=go id=bbtn onclick=doBuild()>Build mod (0)</button>
<button onclick=clearStaged() title="unstage everything (textures + VFX)">Clear</button>
<button onclick=clearCache() title="wipe cached indexes + previews and re-scan the game (keeps staged edits)">Clear cache</button>
<button onclick=openCfg()>Paths</button><span class=sw id=sw></span></div>
<div id=cfg class=cfg>
  <h3>Paths</h3>
  <div class=sw>Point these at YOUR machine, then restart the app to apply.</div>
  <label>Game Paks folder (…/MarvelGame/Marvel/Content/Paks)</label><input id=cfg_paks>
  <label>Tools folder (contains retoc, UAssetGUI, texconv, Mappings)</label><input id=cfg_tools>
  <div class=row id=cfg_status></div>
  <div style="margin-top:12px;display:flex;gap:8px"><button class=go onclick=saveCfg()>Save</button><button onclick="document.getElementById('cfg').classList.remove('on')">Close</button></div>
  <div class=row id=cfg_msg></div>
</div>
<div id=status class=sw style="padding:6px 14px 0;white-space:pre-wrap"></div>
<div class=page><div id=grid class=grid>Loading texture index...</div></div>
<div id=modal class=modal onclick="this.classList.remove('on')"><div class=mbox onclick="event.stopPropagation()">
<div id=mtitle style="margin-bottom:8px"></div><img id=mimg><div id=minfo class=sw style="margin-top:8px"></div>
<div style="margin-top:11px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button onclick=dl()>Download PNG</button><input type=file id=mfile accept="image/*"><button class=go onclick=stage()>Stage this texture</button><button onclick=unstage()>Unstage</button></div>
<div id=mres class=sw style="white-space:pre-wrap;margin-top:8px"></div></div></div>
<div id=vmodal class=modal onclick="this.classList.remove('on')"><div class="mbox vbox" onclick="event.stopPropagation()">
<img id=vmask style="max-width:150px;max-height:150px;float:right;margin:0 0 8px 12px;border-radius:8px;background:#111522;image-rendering:pixelated" onerror="this.style.display='none'">
<div id=vtitle style="margin-bottom:2px;font-size:15px"></div><div id=vsub class=sw style="margin-bottom:6px"></div>
<div id=vbody>loading...</div>
<div style="margin-top:12px;display:flex;gap:8px"><button class=go onclick=stageMi()>Stage this material</button><button onclick=resetMi()>Reset to vanilla</button></div>
<div id=vres class=sw style="white-space:pre-wrap;margin-top:8px"></div></div></div>
<script>
let IDX={},CUR=null,MODE='tex';
let VFXIDX=null,MATIDX=null,AIDX=null,VCUR=null,VEDITS={},MICUR=null;
let BUSY=0;function busy(d){BUSY=Math.max(0,BUSY+d);document.getElementById('lb').classList.toggle('on',BUSY>0);}
async function J(u){return (await fetch(u)).json();}
async function openCfg(){const c=await J('/api/config');
document.getElementById('cfg_paks').value=c.paks||'';document.getElementById('cfg_tools').value=c.tools||'';
const ok=(b,t)=>`<span class="${b?'ok':'bad'}">${b?'✓':'✗'} ${t}</span>`;
document.getElementById('cfg_status').innerHTML=[ok(c.paks_ok,'game Paks found'),ok(c.retoc_ok,'retoc'),ok(c.texconv_ok,'texconv'),ok(c.usmap_ok,'mappings')].join(' &nbsp; ');
document.getElementById('cfg_msg').textContent='';document.getElementById('cfg').classList.add('on');}
async function saveCfg(){const b={paks:document.getElementById('cfg_paks').value,tools:document.getElementById('cfg_tools').value};
const r=await (await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json();
document.getElementById('cfg_msg').innerHTML=r.ok?'<span class=ok>Saved. RESTART the app to apply.</span>':'<span class=bad>'+(r.msg||'error')+'</span>';}
async function init(){busy(1);try{IDX=await J('/api/skins');const cs=document.getElementById('char');
const chars=[...new Set(Object.keys(IDX).map(k=>k.split('/')[0]))].sort();
cs.innerHTML=chars.map(c=>`<option>${c}</option>`).join('');cs.onchange=fillSkin;
document.getElementById('skin').onchange=load;document.getElementById('q').oninput=renderActive;fillSkin();}finally{busy(-1);}}
function fillSkin(){const c=document.getElementById('char').value,ss=document.getElementById('skin');
const sk=Object.keys(IDX).filter(k=>k.split('/')[0]===c).map(k=>k.split('/')[1]).sort();
ss.innerHTML=sk.map(s=>`<option>${s}</option>`).join('');load();}
async function load(){const c=document.getElementById('char').value,s=document.getElementById('skin').value;
CUR=IDX[c+'/'+s];updBuild();document.getElementById('status').textContent='';document.getElementById('sw').textContent=CUR?CUR.textures.length+' textures':'';render();}
function render(){const g=document.getElementById('grid');if(!CUR){g.textContent='No textures';return;}
const q=document.getElementById('q').value.toLowerCase();
const tx=CUR.textures.filter(t=>!q||t.name.toLowerCase().includes(q));
g.innerHTML=tx.map(t=>`<div class="card${EDITS[t.path]?' ed':''}" onclick='zoom(${JSON.stringify(t.cont)},${JSON.stringify(t.path)},${JSON.stringify(t.name)})'>
<img loading=lazy src="/api/preview?cont=${t.cont}&path=${encodeURIComponent(t.path)}" onerror="this.style.opacity=.25">
<div class=n title="${t.name}">${EDITS[t.path]?'* ':''}${t.name}</div></div>`).join('')||'No match';}
let MCUR=null;
function zoom(cont,path,name){MCUR={cont,path,name};const ep=encodeURIComponent(path);document.getElementById('mtitle').textContent=name;
document.getElementById('mimg').src='/api/preview?cont='+cont+'&path='+ep;
document.getElementById('minfo').textContent='loading...';document.getElementById('mres').textContent='';document.getElementById('mfile').value='';
fetch('/api/info?cont='+cont+'&path='+ep).then(r=>r.json()).then(i=>{document.getElementById('minfo').textContent=i.error?i.error:(i.fmt+'  '+i.w+'x'+i.h+'  (DXT1/DXT5/BC5/BC7 replaceable via texconv)');});
document.getElementById('modal').classList.add('on');}
async function dl(){if(!MCUR)return;const res=document.getElementById('mres');res.textContent='Saving PNG...';busy(1);try{
const r=await J('/api/extractone?cont='+MCUR.cont+'&path='+encodeURIComponent(MCUR.path));
res.textContent=r.ok?('Saved PNG to:\n'+r.file):('Error: '+(r.error||r.msg||'unknown'));}finally{busy(-1);}}
async function extractAll(){const c=document.getElementById('char').value,s=document.getElementById('skin').value;
const st=document.getElementById('status');st.textContent='Extracting all PNG...';busy(1);try{
const r=await J('/api/extractall?skin='+encodeURIComponent(c+'/'+s));
st.textContent=r.ok?('Extracted '+r.n+' PNG to:\n'+r.dir+'  (folder opened)'):'Error extracting';}finally{busy(-1);}}
let EDITS={};
function updBuild(){const nt=Object.keys(EDITS).length,nv=Object.keys(VEDITS).length,p=[];
if(nt)p.push(nt+' tex');if(nv)p.push(nv+' mat');
document.getElementById('bbtn').textContent='Build mod'+(p.length?' ('+p.join(', ')+')':' (0)');}
function renderActive(){MODE==='tex'?render():renderMats();}
function doBuild(){buildAll();}
function clearStaged(){EDITS={};VEDITS={};updBuild();renderActive();document.getElementById('status').textContent='Cleared all staged edits.';}
async function clearCache(){busy(1);try{const r=await J('/api/clearcache');
IDX=await J('/api/skins');if(VFXIDX)VFXIDX=await J('/api/vfx');
if(MODE==='vfx')loadVfx();else load();
document.getElementById('status').textContent='Cache cleared ('+r.removed+' items) and index rebuilt fresh. Staged edits kept.';
}catch(e){document.getElementById('status').textContent='Error: '+e;}finally{busy(-1);}}
function stage(){const f=document.getElementById('mfile').files[0];if(!f||!MCUR)return;
const res=document.getElementById('mres');const rd=new FileReader();
rd.onload=()=>{EDITS[MCUR.path]={cont:MCUR.cont,path:MCUR.path,name:MCUR.name,img:rd.result};
document.getElementById('mimg').src=rd.result;res.textContent='Staged "'+MCUR.name+'". Stage more textures, then click Build mod ('+Object.keys(EDITS).length+').';
updBuild();render();};rd.readAsDataURL(f);}
function unstage(){if(!MCUR)return;const res=document.getElementById('mres');
if(!EDITS[MCUR.path]){res.textContent='Not staged.';return;}
delete EDITS[MCUR.path];document.getElementById('mimg').src='/api/preview?cont='+MCUR.cont+'&path='+encodeURIComponent(MCUR.path);
res.textContent='Unstaged "'+MCUR.name+'".';updBuild();render();}
async function buildAll(){const nt=Object.keys(EDITS).length,nv=Object.keys(VEDITS).length,st=document.getElementById('status');
if(!nt&&!nv){st.textContent='Stage something first (a texture PNG, or a VFX color/scalar), then Build.';return;}
st.textContent='Building one mod: '+nt+' texture(s) + '+nv+' material(s)...';busy(1);
try{const body={tex:Object.values(EDITS).map(e=>({cont:e.cont,path:e.path,image:e.img})),vfx:Object.values(VEDITS)};
const r=await (await fetch('/api/build_all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
st.textContent=r.ok?('Built '+r.applied.length+' item(s):\n  '+r.applied.join('\n  ')+(r.skipped&&r.skipped.length?'\nSkipped:\n  '+r.skipped.join('\n  '):'')+'\n'+r.output+'\n'+r.note):('Error: '+r.msg);
}catch(e){st.textContent='Error: '+e;}finally{busy(-1);}}
/* ---------- VFX mode ---------- */
async function setMode(m){if(m===MODE)return;MODE=m;
['tex','vfx','mats'].forEach(x=>document.getElementById('mt_'+x).classList.toggle('on',m===x));
document.getElementById('exb').style.display=m==='tex'?'':'none';
document.getElementById('q').value='';document.getElementById('status').textContent='';
const cs=document.getElementById('char'),ss=document.getElementById('skin');
if(m==='tex'){const chars=[...new Set(Object.keys(IDX).map(k=>k.split('/')[0]))].sort();
 cs.innerHTML=chars.map(c=>`<option>${c}</option>`).join('');cs.onchange=fillSkin;ss.onchange=load;fillSkin();}
else{busy(1);try{if(m==='vfx'&&!VFXIDX)VFXIDX=await J('/api/vfx');if(m==='mats'&&!MATIDX)MATIDX=await J('/api/mats');}finally{busy(-1);}
 AIDX=(m==='vfx')?VFXIDX:MATIDX;
 const chars=Object.keys(AIDX).sort();cs.innerHTML=chars.map(c=>`<option>${c}</option>`).join('');
 cs.onchange=loadMatChar;ss.onchange=renderMats;loadMatChar();}
updBuild();}
function loadMatChar(){const c=document.getElementById('char').value;VCUR=AIDX[c];
const ss=document.getElementById('skin');const groups=[...new Set((VCUR?VCUR.mats:[]).map(m=>m.group))].sort();
ss.innerHTML='<option value="">all groups</option>'+groups.map(g=>`<option>${g}</option>`).join('');
document.getElementById('sw').textContent=VCUR?VCUR.mats.length+' materials':'';renderMats();}
function renderMats(){const g=document.getElementById('grid');if(!VCUR){g.textContent='No materials';return;}
const q=document.getElementById('q').value.toLowerCase();const grp=document.getElementById('skin').value;
const ms=VCUR.mats.filter(m=>(!grp||m.group===grp)&&(!q||m.name.toLowerCase().includes(q)));
g.innerHTML=ms.map(m=>`<div class="card mi${VEDITS[m.path]?' ed':''}" onclick='openMi(${JSON.stringify(m.cont)},${JSON.stringify(m.path)},${JSON.stringify(m.name)})'>
<img loading=lazy src="/api/vfx_mask?cont=${encodeURIComponent(m.cont)}&path=${encodeURIComponent(m.path)}" onerror="this.style.display='none'">
<div class=n title="${m.name}">${VEDITS[m.path]?'* ':''}${m.name}</div><div class=g title="${m.group}">${m.group}</div></div>`).join('')||'No match';}
function hx2(c){return ('0'+Math.round(Math.min(255,Math.max(0,c*255))).toString(16)).slice(-2);}
function rgbaHex(rgba,inten){const n=Math.max(inten,1e-6);return '#'+hx2(rgba[0]/n)+hx2(rgba[1]/n)+hx2(rgba[2]/n);}
async function openMi(cont,path,name){MICUR={cont,path,name,colors:[],scalars:[]};
document.getElementById('vtitle').textContent=name;
document.getElementById('vsub').textContent=path.replace(/^.*\/VFX\//,'VFX/').replace('.uasset','');
document.getElementById('vbody').textContent='loading...';document.getElementById('vres').textContent='';
const vm=document.getElementById('vmask');vm.style.display='';vm.src='/api/vfx_mask?cont='+encodeURIComponent(cont)+'&path='+encodeURIComponent(path);
document.getElementById('vmodal').classList.add('on');
busy(1);let p;try{p=await J('/api/vfx_params?cont='+encodeURIComponent(cont)+'&path='+encodeURIComponent(path));}
catch(e){document.getElementById('vbody').textContent='error: '+e;busy(-1);return;}busy(-1);
if(p.error){document.getElementById('vbody').textContent='error: '+p.error;return;}
const ex=VEDITS[path];
MICUR.colors=p.colors.map(c=>{const rgba=(ex&&ex.colors&&ex.colors[c.name])?ex.colors[c.name].slice():c.rgba.slice();
 return {name:c.name,orig:c.rgba.slice(),rgba,inten:Math.max(rgba[0],rgba[1],rgba[2],1)};});
MICUR.scalars=p.scalars.map(s=>{const v=(ex&&ex.scalars&&(s.name in ex.scalars))?ex.scalars[s.name]:s.value;
 return {name:s.name,orig:s.value,value:v};});
renderMiEditor();}
function renderMiEditor(){let h='';
if(MICUR.colors.length){h+='<div class=vsec>Colors</div>';
 MICUR.colors.forEach((c,i)=>{h+=`<div class=vrow><label title="${c.name}">${c.name}</label>
  <input type=color value="${rgbaHex(c.rgba,c.inten)}" oninput="onCol(${i},this.value)">
  <span class=tag>intensity</span><input class=vnum type=number step=0.05 value="${+c.inten.toFixed(4)}" oninput="onInt(${i},this.value)">
  <span class=tag>A</span><input class=vnum type=number step=0.05 value="${+c.rgba[3].toFixed(4)}" oninput="onA(${i},this.value)"></div>`;});}
if(MICUR.scalars.length){h+='<div class=vsec>Scalars</div>';
 MICUR.scalars.forEach((s,i)=>{const mx=Math.max(Math.abs(s.orig)*3,1);
  h+=`<div class=vrow><label title="${s.name}">${s.name}</label>
  <input class=vrange id=sr${i} type=range min="${Math.min(0,s.orig)}" max="${mx}" step="${mx/1000}" value="${s.value}" oninput="onScl(${i},this.value,1)">
  <input class=vnum id=sn${i} type=number step=any value="${s.value}" oninput="onScl(${i},this.value,0)"></div>`;});}
if(!h)h='<div class=sw>This material exposes no editable color/scalar parameters.</div>';
document.getElementById('vbody').innerHTML=h;}
function onCol(i,hex){const c=MICUR.colors[i],n=Math.max(c.inten,1e-6);
 c.rgba[0]=parseInt(hex.substr(1,2),16)/255*n;c.rgba[1]=parseInt(hex.substr(3,2),16)/255*n;c.rgba[2]=parseInt(hex.substr(5,2),16)/255*n;}
function onInt(i,v){const c=MICUR.colors[i],o=Math.max(c.inten,1e-6),nv=parseFloat(v)||0;
 c.rgba[0]=c.rgba[0]/o*nv;c.rgba[1]=c.rgba[1]/o*nv;c.rgba[2]=c.rgba[2]/o*nv;c.inten=nv;}
function onA(i,v){MICUR.colors[i].rgba[3]=parseFloat(v)||0;}
function onScl(i,v,fromRange){MICUR.scalars[i].value=parseFloat(v)||0;const o=document.getElementById((fromRange?'sn':'sr')+i);if(o)o.value=v;}
function stageMi(){if(!MICUR)return;const colors={},scalars={};
MICUR.colors.forEach(c=>{colors[c.name]=[+c.rgba[0],+c.rgba[1],+c.rgba[2],+c.rgba[3]];});
MICUR.scalars.forEach(s=>{scalars[s.name]=+s.value;});
VEDITS[MICUR.path]={cont:MICUR.cont,path:MICUR.path,name:MICUR.name,colors,scalars};
document.getElementById('vres').textContent='Staged "'+MICUR.name+'". Stage more, then Build VFX mod ('+Object.keys(VEDITS).length+').';
updBuild();renderVfx();document.getElementById('vmodal').classList.remove('on');}
function resetMi(){if(!MICUR)return;delete VEDITS[MICUR.path];
MICUR.colors.forEach(c=>{c.rgba=c.orig.slice();c.inten=Math.max(c.rgba[0],c.rgba[1],c.rgba[2],1);});
MICUR.scalars.forEach(s=>{s.value=s.orig;});renderMiEditor();updBuild();renderVfx();
document.getElementById('vres').textContent='Reset to vanilla.';}
init();
</script>"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _s(self, code, body, ct="application/json"):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        try:
            if u.path == "/": self._s(200, HTML, "text/html;charset=utf-8")
            elif u.path == "/api/skins": self._s(200, enum_textures())
            elif u.path == "/api/preview":
                png = decode_texture(q["cont"][0], q["path"][0]); self._s(200, open(png, "rb").read(), "image/png")
            elif u.path == "/api/info":
                i = tex_info(q["cont"][0], q["path"][0]); self._s(200, {"fmt": i["fmt"], "w": i["w"], "h": i["h"]})
            elif u.path == "/api/extractall":
                skin = q["skin"][0]; info = enum_textures().get(skin, {"textures": []})
                dest = os.path.join(EXTRACT_DIR, re.sub(r"\W+", "_", skin).strip("_")); os.makedirs(dest, exist_ok=True); n = 0
                for t in info["textures"]:
                    try:
                        rel = re.sub(r"^.*?/Characters/[^/]+/[^/]+/", "", t["path"])[:-7] + ".png"
                        dst = os.path.join(dest, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy(decode_texture(t["cont"], t["path"]), dst); n += 1
                    except Exception: pass
                try: os.startfile(os.path.abspath(dest))
                except Exception: pass
                self._s(200, {"ok": True, "n": n, "dir": os.path.abspath(dest).replace("\\", "/")})
            elif u.path == "/api/extractone":
                path = q["path"][0]; dest = os.path.join(EXTRACT_DIR, re.sub(r"\W+", "_", _skin_key(path)).strip("_"))
                rel = re.sub(r"^.*?/Characters/[^/]+/[^/]+/", "", path)[:-7] + ".png"
                dst = os.path.join(dest, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(decode_texture(q["cont"][0], path), dst)
                try: os.startfile(os.path.dirname(os.path.abspath(dst)))
                except Exception: pass
                self._s(200, {"ok": True, "file": os.path.abspath(dst).replace("\\", "/")})
            elif u.path == "/api/config":
                self._s(200, {"paks": PAKS, "tools": TOOLS,
                              "paks_ok": bool(glob.glob(PAKS + "/pakchunk*.utoc")),
                              "retoc_ok": os.path.exists(RETOC),
                              "texconv_ok": os.path.exists(TEXCONV),
                              "usmap_ok": bool(glob.glob(TOOLS + "/Mappings/*.usmap"))})
            elif u.path == "/api/vfx": self._s(200, enum_vfx())
            elif u.path == "/api/mats": self._s(200, enum_mats())
            elif u.path == "/api/vfx_params": self._s(200, vfx_params(q["cont"][0], q["path"][0]))
            elif u.path == "/api/vfx_mask":
                png = vfx_mask_png(q["cont"][0], q["path"][0])
                if png: self._s(200, open(png, "rb").read(), "image/png")
                else: self._s(404, {"error": "no mask"})
            elif u.path == "/api/clearcache": self._s(200, clear_cache())
            else: self._s(404, {"error": "nf"})
        except Exception as e: self._s(200, {"error": f"{type(e).__name__}: {e}"})
    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/build":
            g = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            try:
                edits = [{"cont": e["cont"], "path": e["path"], "image": base64.b64decode(e["image"].split(",")[-1])} for e in g.get("edits", [])]
                self._s(200, build_textures(g["skin"], edits))
            except Exception as e: self._s(200, {"ok": False, "msg": f"{type(e).__name__}: {e}"})
        elif path == "/api/build_all":
            g = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            try:
                tex = [{"cont": e["cont"], "path": e["path"], "image": base64.b64decode(e["image"].split(",")[-1])} for e in g.get("tex", [])]
                self._s(200, build_all(tex, g.get("vfx", [])))
            except Exception as e: self._s(200, {"ok": False, "msg": f"{type(e).__name__}: {e}"})
        elif path == "/api/config":
            g = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            try:
                cfg = mr_app.load_config()
                if g.get("paks"): cfg["paks"] = g["paks"].strip()
                if g.get("tools"): cfg["tools"] = g["tools"].strip()
                json.dump(cfg, open(mr_app.CONFIG_FILE, "w"))
                self._s(200, {"ok": True})
            except Exception as e: self._s(200, {"ok": False, "msg": str(e)})
        else: self._s(404, {"error": "nf"})

if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), H)
    print(f"MR Texture Editor: http://localhost:{PORT}  (Ctrl+C to stop)")
    threading.Timer(0.6, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nstopped")
