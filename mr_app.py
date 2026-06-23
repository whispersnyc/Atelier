"""MR World Editor - local web app. Run: python mr_app.py"""
import http.server, socketserver, json, os, sys, glob, re, struct, hashlib, shutil, subprocess, threading, webbrowser, base64, datetime
from urllib.parse import urlparse, parse_qs
ROOT = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT); sys.path.insert(0, ROOT)
try:
    from img_data import IMG_B64
except Exception:
    IMG_B64 = None
CONFIG_FILE = os.path.join(ROOT, "mr_config.json")
def load_config():
    try: return json.load(open(CONFIG_FILE, encoding="utf-8"))
    except Exception: return {}
def detect_paks():
    cands = [r"C:/Program Files (x86)/Steam/steamapps/common/MarvelRivals/MarvelGame/Marvel/Content/Paks"]
    for vdf in (r"C:/Program Files (x86)/Steam/steamapps/libraryfolders.vdf", r"C:/Program Files/Steam/steamapps/libraryfolders.vdf"):
        try:
            for m in re.finditer(r'"path"\s*"([^"]+)"', open(vdf, encoding="utf-8", errors="ignore").read()):
                cands.append(m.group(1).replace("\\\\", "/").replace("\\", "/") + "/steamapps/common/MarvelRivals/MarvelGame/Marvel/Content/Paks")
        except Exception: pass
    for c in cands:
        if os.path.isdir(c) and glob.glob(c + "/pakchunk*.utoc"): return c
    return cands[0]
_cfg = load_config()
TOOLS = _cfg.get("tools") or os.path.join(ROOT, "Tools")
PAKS = (_cfg.get("paks") or detect_paks()).replace("\\", "/")
os.environ["MR_TOOLS"] = TOOLS
import io_lib
PORT = 8765
IMAGE = "Textrure_App_DemoImage.png"
RETOC = os.path.join(TOOLS, "retoc-rivals-cli", "retoc-rivals-cli.exe")
UAG = os.path.join(TOOLS, "UAssetGUI.exe")
CACHE = "_work/editor_cache"
CNW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: child exes (retoc/UAG) never flash a console
def _run(args, **kw):
    kw.setdefault("cwd", ROOT); kw.setdefault("capture_output", True); kw.setdefault("creationflags", CNW)
    return subprocess.run(args, **kw)
_LOG = os.path.join(ROOT, "_work", "debug.log")
def _dbg(tag, *args):
    os.makedirs(os.path.dirname(_LOG), exist_ok=True)
    with open(_LOG, "a", encoding="utf-8") as _f:
        _f.write(f"[{datetime.datetime.now():%H:%M:%S}][{tag}] {' '.join(str(a) for a in args)}\n")
USMAP_DIR = ROOT + "/usmap"
MAPPING = "Marvel_S8.5"
def ensure_mapping():
    """Guarantee UAssetGUI can find the S8.5 mapping at runtime.
    UAG searches AppData/UAssetGUI/Mappings/ by name AND reads its config.json for PreferredMappings.
    We must populate BOTH, and we also keep a copy in Tools/Mappings/ for the full-path CLI arg."""
    tools_dir = os.path.join(TOOLS, "Mappings")
    appdata_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "UAssetGUI", "Mappings")
    tools_usmap = os.path.join(tools_dir, MAPPING + ".usmap")
    appdata_usmap = os.path.join(appdata_dir, MAPPING + ".usmap") if appdata_dir != "/UAssetGUI/Mappings" else ""
    # Locate the source .usmap (prefer already-copied Tools/Mappings/, then usmap/, then Tools/ root)
    src = (tools_usmap if os.path.exists(tools_usmap) else None
           or next(iter(sorted(glob.glob(USMAP_DIR + "/*.usmap"))), None)
           or next(iter(sorted(glob.glob(os.path.join(TOOLS, "*.usmap")))), None))
    if not src: return None
    # Ensure Tools/Mappings/ copy (needed for the full-path CLI arg below)
    if not os.path.exists(tools_usmap):
        os.makedirs(tools_dir, exist_ok=True); shutil.copy(src, tools_usmap)
    # Ensure AppData copy (UAG's name-based lookup at runtime)
    if appdata_usmap and not os.path.exists(appdata_usmap):
        os.makedirs(appdata_dir, exist_ok=True); shutil.copy(src, appdata_usmap)
    # Patch UAG's config.json so PreferredMappings is always set correctly
    cfg_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "UAssetGUI", "config.json")
    if cfg_path and os.path.exists(cfg_path):
        try:
            cfg = json.load(open(cfg_path, encoding="utf-8"))
            if cfg.get("PreferredMappings") != MAPPING:
                cfg["PreferredMappings"] = MAPPING
                json.dump(cfg, open(cfg_path, "w"), indent=2)
        except Exception: pass
    return tools_usmap
ensure_mapping()
# Full path to the .usmap — passed directly to UAG CLI so name resolution is bypassed entirely.
_USMAP_PATH = os.path.join(TOOLS, "Mappings", MAPPING + ".usmap")
GRADE_VEC = ["ColorSaturation", "ColorContrast", "ColorGain", "ColorOffset", "ColorGamma",
             "ColorSaturationShadows", "ColorContrastShadows", "ColorGainShadows",
             "ColorSaturationMidtones", "ColorSaturationHighlights"]
GRADE_SCALAR = ["WhiteTemp", "BloomIntensity", "VignetteIntensity", "FilmGrainIntensity",
                "SceneFringeIntensity", "AutoExposureBias", "CasSharpening", "AmbientCubemapIntensity"]
GRADE = GRADE_VEC + GRADE_SCALAR
ENABLE = ["bVisible", "bHiddenInGame", "bHidden", "bIsActive", "bAffectsWorld", "bUnbound"]
# Shipped grades extracted from the _work grade scripts (gain/contrast/sat + extras).
PRESETS = {
    "Asgard · grand golden":    dict(gain=(1.08, 1.0, 0.85), contrast=1.10, sat=1.12),
    "Krakoa · oasis":           dict(gain=(0.55, 0.60, 0.64), contrast=1.12, sat=1.12, fog=0),
    "Krakoa · day":             dict(gain=None, contrast=1.07, sat=1.12, fog=0.9),
    "Krakoa · nightgold":       dict(gain=(0.82, 0.56, 0.25), contrast=1.12, sat=1.12, fog=0),
    "Arakko · frozen titanium": dict(gain=(0.93, 0.98, 1.06), contrast=1.10, sat=1.12),
    "Wakanda · vibrance":       dict(gain=None, contrast=1.08, sat=1.15, fog=0.9),
    "Hydra · cold steel":       dict(gain=(0.93, 0.98, 1.06), contrast=1.13, sat=1.12, gainshadows=(0.88, 0.90, 0.96)),
    "Hydra · hot forge":        dict(gain=(1.10, 0.98, 0.78), contrast=1.13, sat=1.13, gainshadows=(0.95, 0.88, 0.78)),
    "Klyntar · eerie":          dict(gain=(0.60, 0.64, 0.74), contrast=1.13, sat=1.08, vignette=0.7),
    "Kunlun · jade grand":      dict(gain=(1.05, 1.00, 0.91), contrast=1.10, sat=1.12),
    "Tokyo · cyber":            dict(gain=(0.50, 0.42, 0.64), contrast=1.12, sat=1.22, bloom=2.2),
    "Heroic · day":             dict(gain=(1.03, 1.00, 0.96), contrast=1.10, sat=1.16),
    "Heroic · night":           dict(gain=(0.56, 0.58, 0.68), contrast=1.10, sat=1.16, bloom=2.0),
    "NewYork · blood-moon":     dict(gain=(0.74, 0.50, 0.57), contrast=1.00, sat=1.05),
}
MAP_PRESETS = {
    "Asgard": ["Asgard · grand golden"],
    "Krakoa": ["Krakoa · oasis", "Krakoa · day", "Krakoa · nightgold"],
    "Arakko": ["Arakko · frozen titanium"],
    "Wakanda": ["Wakanda · vibrance"],
    "Hydra": ["Hydra · cold steel", "Hydra · hot forge"],
    "Klyntar": ["Klyntar · eerie"],
    "Kunlun": ["Kunlun · jade grand"],
    "Tokyo": ["Tokyo · cyber"],
    "TimeSquare": ["Heroic · night"],
    "NewYorkM01": ["NewYork · blood-moon"],
    "NewYork": ["Heroic · day", "Heroic · night"],
    "Newyork": ["Heroic · day", "Heroic · night"],
}
def map_name_of(path):
    m = re.search(r"/Maps/([^/]+)/", path, re.I); return m.group(1) if m else ""
def presets_for(mapname):
    ml = mapname.lower()
    for pat in sorted(MAP_PRESETS, key=len, reverse=True):
        if pat.lower() in ml:
            return [{"name": n, "gain": list(PRESETS[n]["gain"]) if PRESETS[n].get("gain") else None,
                     "sat": PRESETS[n].get("sat", 1.0), "contrast": PRESETS[n].get("contrast", 1.0),
                     "extra": {k: PRESETS[n][k] for k in ("fog", "bloom", "vignette", "gainshadows") if k in PRESETS[n]}}
                    for n in MAP_PRESETS[pat]]
    return []

FRIENDLY = {
    "Arakko": ("Arakko", "Convoy"), "AsgardE01": ("Yggdrasil Path", "Convoy"),
    "AsgardPalace": ("Royal Palace", "Domination"), "Battle": ("Battle", "Misc"),
    "BattlePass": ("Battlepass", "Misc"), "ConsoleTest": ("Console Test", "Misc"),
    "Feature": ("Feature", "Misc"), "GardenCQ01": ("Grand Garden", "Conquest"),
    "HydraA01": ("Unused Hydra Map", "Misc"), "HydraC": ("Hell's Heaven", "Domination"),
    "Klyntar": ("Symbiotic Surface", "Convergence"), "KlyntarC": ("Celestial Husk", "Domination"),
    "KlyntarEC01": ("Throne of Knull", "Resource Rumble"), "KrakoaBeach": ("Hellfire Bay Beach", "Hub"),
    "KrakoaC": ("Krakoa", "Domination"), "KunlunEC01": ("Shenloong Arena", "Conquest"),
    "KunlunH01": ("Heart of Heaven", "Convergence"), "Lobby": ("Solo Lobby", "Lobby"),
    "MVP": ("Default MVP", "Misc"), "MultiLobby": ("Avengers Lobby", "Lobby"),
    "MuseumE01": ("Museum of Contemplation", "Convoy"), "NewYorkE01": ("Midtown", "Convoy"),
    "NewYorkH01": ("Central Park", "Convergence"), "NewYorkM01": ("Sanctum Sanctorum", "Doom Match"),
    "NewYorkZombie": ("Marvel Zombies", "Event"), "NewyorkH02": ("Lower Manhattan", "Convergence"),
    "NoviceLevel": ("Tutorial", "Misc"), "NuevaYork": ("Alchemax Headquarters", "Doom Match"),
    "PracticeRange": ("Practice Range", "Hub"), "TeamLobby": ("Team Lobby", "Lobby"),
    "Test": ("Test", "Misc"), "TimeSquare": ("Time Square", "Hub"),
    "TokyoCQ01": ("Ninomaru", "Conquest"), "TokyoE01": ("Spider Islands", "Convoy"),
    "TokyoH01": ("Shin Shibuya", "Convergence"), "VampirePVE01": ("Bloodhunt", "Event"),
    "Wakanda": ("Birnin T'Challa", "Domination"), "WakandaMC01": ("Hall of Djalia", "Convergence"),
}
def _pt(p): return str(p.get("$type", "")).split(".")[-1].split(",")[0]
def _vec(v):
    if not isinstance(v, dict): return None
    if "X" in v: return [round(float(v.get(k, 0)), 4) for k in "XYZW"]
    if "R" in v: return [round(float(v.get(k, 0)), 4) for k in "RGBA"]
    return None
def _flat(props, vals, ov):
    for p in props:
        if not isinstance(p, dict): continue
        n = p.get("Name", "?")
        if n.startswith("bOverride_") and _pt(p) == "BoolPropertyData":
            if p.get("Value"): ov.add(n[10:])
            continue
        v = p.get("Value"); ve = _vec(v)
        if ve is not None: vals[n] = ve
        elif isinstance(v, list): _flat(v, vals, ov)
        elif isinstance(v, (int, float)): vals[n] = v

def parse_model(jpath):
    d = json.load(open(jpath, encoding="utf-8"))
    _dbg("parse_model", f"root_type={type(d).__name__} keys={list(d.keys())[:6] if isinstance(d, dict) else '(list)'}")
    imp = d.get("Imports", [])
    def cls(e):
        ci = e.get("ClassIndex", 0)
        if isinstance(ci, int) and ci < 0 and -ci - 1 < len(imp): return imp[-ci - 1].get("ObjectName", "?")
        return "?"
    comps = []; fog = None
    for i, e in enumerate(d.get("Exports", [])):
        cn = cls(e)
        en, grade, vals, ov, ppv, has_loc = {}, {}, {}, set(), False, False
        for p in e.get("Data", []):
            if not isinstance(p, dict): continue
            nm = p.get("Name", "?")
            if nm in ENABLE and _pt(p) == "BoolPropertyData": en[nm] = bool(p.get("Value"))
            if nm == "RelativeLocation": has_loc = True
            if nm == "Settings" and isinstance(p.get("Value"), list): ppv = True; _flat(p["Value"], vals, ov)
        if fog is None and cn.endswith("HeightFogComponent"):
            fd = fc = fpos = None
            for p in e.get("Data", []):
                if not isinstance(p, dict): continue
                if p.get("Name") == "FogDensity" and isinstance(p.get("Value"), (int, float)): fd = p["Value"]
                elif p.get("Name") in ("FogInscatteringLuminance", "FogInscatteringColor") and isinstance(p.get("Value"), list) and p["Value"]:
                    cv = _vec((p["Value"][0] or {}).get("Value"))
                    if cv: fc = [round(x, 4) for x in cv[:3]]
                elif p.get("Name") == "RelativeLocation" and isinstance(p.get("Value"), list) and p["Value"]:
                    lv = _vec((p["Value"][0] or {}).get("Value"))
                    if lv: fpos = [round(x, 2) for x in lv[:3]]
            fog = {"idx": i, "density": fd, "color": fc, "pos": fpos, "cls": cn}
        if ppv:
            for s in GRADE:
                if s in vals: grade[s] = {"value": vals[s], "override": s in ov}
        comps.append({"idx": i, "name": e.get("ObjectName", "?"), "cls": cn,
                      "enables": en, "is_ppv": ppv, "grade": grade,
                      "hideable": cn.endswith("Component") or ("bVisible" in en) or has_loc})
    return {"components": comps, "fog": fog}

_MAPS = None
def _cont_prio(name):
    """Order to TRY for the actual chunk: base containers hold full chunks; patch only carries changed/new ones."""
    n = name.lower()
    if "pakchunkmap" in n: return 0     # main playable maps
    if n.startswith("pakchunk0"): return 1  # base content (lobby/practice/museum/etc.)
    if n.startswith("patch"): return 2  # patch: only changed/new chunks -> fallback (e.g. KrakoaBeach is patch-only)
    return 3
def enum_maps():
    """Every map across ALL containers; each sublevel keeps the ordered list of containers it lives in."""
    global _MAPS
    if _MAPS is not None: return _MAPS
    byk = {}
    utocs = sorted(glob.glob(PAKS + "/*.utoc"), key=lambda u: (_cont_prio(os.path.basename(u)), os.path.basename(u)))
    for utoc in utocs:
        try: t = io_lib.parse_toc(utoc); entries = io_lib.parse_dir_index(t)
        except Exception: continue
        cont = os.path.basename(utoc)
        for p, ud in entries:
            pl = p.lower()
            if not (pl.endswith(".umap") and "/maps/" in pl): continue
            mm = re.search(r"/Maps/([^/]+)/", p, re.I)
            if not mm: continue
            e = byk.get(pl)
            if e is None: e = byk[pl] = {"path": p, "sub": os.path.basename(p)[:-5], "map": mm.group(1), "conts": []}
            if cont not in e["conts"]: e["conts"].append(cont)
    m = {}
    for e in byk.values():
        m.setdefault(e["map"], []).append({"path": e["path"], "sub": e["sub"], "cont": e["conts"][0], "conts": e["conts"]})
    _MAPS = {k: sorted(v, key=lambda x: x["sub"]) for k, v in sorted(m.items())}
    return _MAPS

def _unpack_sub(cont, path):
    """Unpack one sublevel to legacy assets (base-first container, patch fallback). Returns asset path or None."""
    key = re.sub(r"\W+", "_", path).strip("_"); ua = f"{CACHE}/{key}_u"
    have = glob.glob(ua + "/**/*.uasset", recursive=True)
    if have: return have[0]
    os.makedirs(CACHE, exist_ok=True)
    cands = [cont]
    for subs in enum_maps().values():
        for s in subs:
            if s["path"] == path:
                for c in s.get("conts", []):
                    if c not in cands: cands.append(c)
    for c in cands:
        shutil.rmtree(ua, ignore_errors=True)
        _run([RETOC, "unpack", f"{PAKS}/{c}", "--filter", path, "--game-paks-dir", PAKS, "-o", ua])
        f = glob.glob(ua + "/**/*.uasset", recursive=True)
        if f: return f[0]
    return None

def _ensure_json(cont, path):
    key = re.sub(r"\W+", "_", path).strip("_"); j = f"{CACHE}/{key}.json"
    if not os.path.exists(j):
        asset = _unpack_sub(cont, path)
        if not asset: raise RuntimeError("unpack produced no .uasset for " + path)
        _dbg("ensure_json", f"UAG tojson {asset!r}  usmap={_USMAP_PATH!r}")
        r = _run([UAG, "tojson", asset, j, "VER_UE5_3", _USMAP_PATH])
        _dbg("ensure_json", f"rc={r.returncode} json_exists={os.path.exists(j)}")
        if r.stdout: _dbg("ensure_json", f"stdout={r.stdout[:400]!r}")
        if r.stderr: _dbg("ensure_json", f"stderr={r.stderr[:400]!r}")
    else:
        _dbg("ensure_json", f"cache_hit {j!r}")
    return j

def load_model(cont, path):
    j = _ensure_json(cont, path)
    m = parse_model(j); mn = map_name_of(path)
    m["map"] = mn; m["presets"] = presets_for(mn); return m

def _collect_points(jp):
    """Port of map_viz.collect: pull RelativeLocation of every export -> top-down points + spawns."""
    try: d = json.load(open(jp, encoding="utf-8"))
    except Exception: return [], []
    ex = d.get("Exports", []); im = d.get("Imports", [])
    def nm(i):
        if not isinstance(i, int) or i == 0: return None
        return ex[i - 1].get("ObjectName") if 0 < i <= len(ex) else (im[-i - 1].get("ObjectName") if i < 0 and -i - 1 < len(im) else None)
    def num(x):
        try: return float(x)
        except (TypeError, ValueError): return None
    pts, spawns = [], []
    for e in ex:
        loc = mesh = None
        on = str(e.get("ObjectName", "")); cls = str(nm(e.get("ClassIndex")))
        for p in e.get("Data", []):
            if not isinstance(p, dict): continue
            if p.get("Name") == "RelativeLocation":
                vv = p.get("Value")
                if isinstance(vv, list) and vv and isinstance(vv[0], dict):
                    v = vv[0].get("Value")
                    if isinstance(v, dict):
                        x, y, z = num(v.get("X")), num(v.get("Y")), num(v.get("Z"))
                        if None not in (x, y, z): loc = (x, y, z)
            if p.get("Name") == "StaticMesh" and isinstance(p.get("Value"), int): mesh = nm(p["Value"])
        if loc and abs(loc[0]) < 80000 and abs(loc[1]) < 80000:
            if "PlayerStart" in on or "PlayerStart" in cls or "SpawnRoom" in on:
                spawns.append([round(loc[0]), round(loc[1]), round(loc[2]), on])
            else:
                pts.append([round(loc[0]), round(loc[1]), round(loc[2]), str(mesh or on)])
    return pts, spawns

def _mapviz_sub(cont, path):
    """Geometry points for one sublevel; re-runs tojson if UAG base64-dumps (intermittent)."""
    try: asset = _unpack_sub(cont, path)
    except Exception: return [], []
    if not asset: return [], []
    key = re.sub(r"\W+", "_", path).strip("_"); j = f"{CACHE}/{key}.json"
    for attempt in range(3):
        if attempt or not os.path.exists(j):
            _run([UAG, "tojson", asset, j, "VER_UE5_3", _USMAP_PATH])
        p, sp = _collect_points(j)
        if p or sp: return p, sp
    return [], []

_SEGRX = re.compile(r"_(HighQuality|LowQuality|Art|ArtDynamic|Back?Ground|Building|Config|Des|Like|RH|VFX|Collision|Destruction|Clone|CltOnly\w*|Filling|Machine|DisPCG|Spectator|SVON|AIConfig|Audio)$", re.I)
def _seg_base(sub):
    s = sub
    while True:
        n = _SEGRX.sub("", s)
        if n == s: break
        s = n
    return s or sub

def mapviz_points(mapname, sub=None):
    """Top-down point cloud for the segment the chosen sublevel belongs to (domination C01/C02/C03, S1/S2, etc.). Cached."""
    base = _seg_base(sub) if sub else mapname
    mn_key = re.sub(r"\W+", "_", mapname); b_key = re.sub(r"\W+", "_", base)
    ck = f"{CACHE}/mapviz_{mn_key}_{b_key}.json"
    if os.path.exists(ck): return json.load(open(ck, encoding="utf-8"))
    subs = enum_maps().get(mapname, [])
    EXCL = re.compile(r"(Audio|_SFX|Spatial|Collision|Nav|AIConfig|_Config$|SVON|Spectator|Music|Clone|CltOnly|_VFX|Destruction|Filling)", re.I)
    bl = base.lower()
    cand = [s for s in subs if s["sub"].lower().startswith(bl) and not EXCL.search(s["sub"])]
    if not cand: cand = [s for s in subs if not EXCL.search(s["sub"])]
    cand.sort(key=lambda s: 0 if "_art" in s["sub"].lower() else (1 if s["sub"].lower() == bl else 2))
    cand = cand[:10]
    pts, spawns = [], []
    for s in cand:
        p, sp = _mapviz_sub(s["cont"], s["path"]); pts += p; spawns += sp
    seen = set(); uniq = []
    for p in pts:
        k = (p[0] // 140, p[1] // 140)
        if k in seen: continue
        seen.add(k); uniq.append(p)
    pts = uniq[:2000]
    names = []; nidx = {}
    for p in pts:
        n = p[3]
        if n not in nidx: nidx[n] = len(names); names.append(n)
        p[3] = nidx[n]
    seen = set(); sp2 = []
    for s in spawns:
        k = (s[0] // 100, s[1] // 100)
        if k in seen: continue
        seen.add(k); sp2.append(s)
    out = {"title": base, "pts": pts, "names": names, "spawns": sp2, "subs": [s["sub"] for s in cand]}
    json.dump(out, open(ck, "w")); return out

def _bP(n, v): return {"$type": "UAssetAPI.PropertyTypes.Objects.BoolPropertyData, UAssetAPI", "Name": n, "ArrayIndex": 0, "PropertyGuid": None, "IsZero": (not v), "PropertyTagFlags": "None", "PropertyTypeName": None, "PropertyTagExtensions": "NoExtension", "Value": v}
def _fP(n, v): return {"$type": "UAssetAPI.PropertyTypes.Objects.FloatPropertyData, UAssetAPI", "Name": n, "ArrayIndex": 0, "PropertyGuid": None, "IsZero": (v == 0.0), "PropertyTagFlags": "None", "PropertyTypeName": None, "PropertyTagExtensions": "NoExtension", "Value": v}
def _mkvec4(n, x, y, z, w=1.0):
    return {"$type": "UAssetAPI.PropertyTypes.Structs.StructPropertyData, UAssetAPI", "StructType": "Vector4", "SerializeNone": True, "StructGUID": "{00000000-0000-0000-0000-000000000000}", "SerializationControl": "NoExtension", "Operation": "None", "Name": n, "ArrayIndex": 0, "PropertyGuid": None, "IsZero": False, "PropertyTagFlags": "None", "PropertyTypeName": None, "PropertyTagExtensions": "NoExtension", "Value": [{"$type": "UAssetAPI.PropertyTypes.Structs.Vector4PropertyData, UAssetAPI", "Name": n, "ArrayIndex": 0, "PropertyGuid": None, "IsZero": False, "PropertyTagFlags": "None", "PropertyTypeName": None, "PropertyTagExtensions": "NoExtension", "Value": {"$type": "UAssetAPI.UnrealTypes.FVector4, UAssetAPI", "X": x, "Y": y, "Z": z, "W": w}}]}

def apply_grade_json(d, grade):
    """Mutate the UAssetGUI JSON in place per the editor's dirty knobs + component edits. Returns the applied list."""
    ex = d.get("Exports", []); imp = d.get("Imports", [])
    def cls(e):
        ci = e.get("ClassIndex", 0)
        return imp[-ci - 1].get("ObjectName", "?") if isinstance(ci, int) and ci < 0 and -ci - 1 < len(imp) else "?"
    dirty = set(grade.get("dirty") or [])
    edits = {int(k): bool(v) for k, v in (grade.get("edits") or {}).items()}
    applied = []
    for i, e in enumerate(ex):
        cn = cls(e); data = e.get("Data") or []
        if i in edits:
            want = edits[i]
            for nm, val in (("bVisible", want), ("bHiddenInGame", not want)):
                hit = next((p for p in data if isinstance(p, dict) and p.get("Name") == nm and "Bool" in str(p.get("$type", ""))), None)
                if hit: hit["Value"] = val; hit["IsZero"] = (not val)
                else: data.append(_bP(nm, val))
            e["Data"] = data
        sprop = next((p for p in data if isinstance(p, dict) and p.get("Name") == "Settings" and isinstance(p.get("Value"), list)), None)
        if sprop is not None:
            sv = sprop["Value"]; sp = {x.get("Name"): x for x in sv if isinstance(x, dict)}
            def isv4(x): return isinstance(x.get("Value"), list) and x["Value"] and isinstance(x["Value"][0], dict) and isinstance(x["Value"][0].get("Value"), dict) and "X" in x["Value"][0]["Value"]
            def setv(name, x, y, z):
                if name in sp and isv4(sp[name]):
                    vv = sp[name]["Value"][0]["Value"]; vv["X"], vv["Y"], vv["Z"] = x, y, z
                else:
                    np = _mkvec4(name, x, y, z); sv.append(np); sp[name] = np
            def setf(name, v):
                if name in sp and isinstance(sp[name].get("Value"), (int, float)): sp[name]["Value"] = v
                else: sv.append(_fP(name, v)); sp[name] = sv[-1]
            def setb(name, v):
                if name in sp: sp[name]["Value"] = v; sp[name]["IsZero"] = (not v)
                else: sv.append(_bP(name, v)); sp[name] = sv[-1]
            def bov(name): setb("bOverride_" + name, True)
            if {"gain_r", "gain_g", "gain_b"} & dirty: setv("ColorGain", *grade["gain"]); bov("ColorGain"); applied.append("ColorGain %s" % (tuple(round(x, 3) for x in grade["gain"]),))
            if {"gs_r", "gs_g", "gs_b"} & dirty: setv("ColorGainShadows", *grade["gainshadows"]); bov("ColorGainShadows"); applied.append("ColorGainShadows %s" % (tuple(round(x, 3) for x in grade["gainshadows"]),))
            if "sat" in dirty: s = grade["sat"]; setv("ColorSaturation", s, s, s); bov("ColorSaturation"); applied.append("ColorSaturation %.3g" % s)
            if "contrast" in dirty: c = grade["contrast"]; setv("ColorContrast", c, c, c); bov("ColorContrast"); applied.append("ColorContrast %.3g" % c)
            if "bloom" in dirty: setf("BloomIntensity", grade["bloom"]); bov("BloomIntensity"); applied.append("BloomIntensity %.3g" % grade["bloom"])
            if "vignette" in dirty: setf("VignetteIntensity", grade["vignette"]); bov("VignetteIntensity"); applied.append("VignetteIntensity %.3g" % grade["vignette"])
        if cn.endswith("HeightFogComponent"):
            if {"fog_r", "fog_g", "fog_b"} & dirty:
                for p in data:
                    if isinstance(p, dict) and p.get("Name") in ("FogInscatteringLuminance", "FogInscatteringColor") and isinstance(p.get("Value"), list) and p["Value"]:
                        cv = p["Value"][0].get("Value")
                        if isinstance(cv, dict): cv["R"], cv["G"], cv["B"] = grade["fog"]; applied.append("Fog color %s" % (tuple(round(x, 3) for x in grade["fog"]),))
            if "fog_d" in dirty:
                for p in data:
                    if isinstance(p, dict) and p.get("Name") == "FogDensity" and isinstance(p.get("Value"), (int, float)):
                        p["Value"] = round(p["Value"] * grade["fog_d"], 7); applied.append("FogDensity x%.3g" % grade["fog_d"])
            if "fog_pos" in dirty and grade.get("fog_pos"):
                for p in data:
                    if isinstance(p, dict) and p.get("Name") == "RelativeLocation" and isinstance(p.get("Value"), list) and p["Value"]:
                        vv = p["Value"][0].get("Value")
                        if isinstance(vv, dict): vv["X"], vv["Y"], vv["Z"] = grade["fog_pos"]; applied.append("Fog position %s" % (tuple(round(x, 1) for x in grade["fog_pos"]),))
    if edits: applied.append("%d component visibility edit(s)" % len(edits))
    return list(dict.fromkeys(applied))

def _ppv_values(d):
    """Exact (un-rounded) current PP vec4/scalar values + override set + fog values from the json, for byte matching."""
    imp = d.get("Imports", [])
    def cls(e):
        ci = e.get("ClassIndex", 0)
        return imp[-ci - 1].get("ObjectName", "?") if isinstance(ci, int) and ci < 0 and -ci - 1 < len(imp) else "?"
    def F(v):
        try: return float(v)
        except (TypeError, ValueError): return 0.0
    pp, ov, fog = {}, set(), {}
    for e in d.get("Exports", []):
        cn = cls(e)
        for pr in (e.get("Data") or []):
            if not isinstance(pr, dict): continue
            nm = pr.get("Name")
            if nm == "Settings" and isinstance(pr.get("Value"), list):
                for x in pr["Value"]:
                    if not isinstance(x, dict): continue
                    xn = x.get("Name", "")
                    if xn.startswith("bOverride_") and x.get("Value") is True: ov.add(xn[10:])
                    v = x.get("Value")
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        iv = v[0].get("Value")
                        if isinstance(iv, dict) and "X" in iv: pp[xn] = [F(iv.get(k, 0)) for k in "XYZW"]
                    elif isinstance(v, (int, float)): pp.setdefault(xn, float(v))
            if cn.endswith("HeightFogComponent"):
                if nm in ("FogInscatteringLuminance", "FogInscatteringColor") and isinstance(pr.get("Value"), list) and pr["Value"]:
                    iv = pr["Value"][0].get("Value")
                    if isinstance(iv, dict) and "R" in iv: fog["color"] = [F(iv.get(k, 0)) for k in "RGBA"]
                elif nm == "FogDensity" and isinstance(pr.get("Value"), (int, float)): fog["density"] = float(pr["Value"])
                elif nm == "RelativeLocation" and isinstance(pr.get("Value"), list) and pr["Value"]:
                    iv = pr["Value"][0].get("Value")
                    if isinstance(iv, dict) and "X" in iv: fog["pos"] = [F(iv.get(k, 0)) for k in "XYZ"]
    return pp, ov, fog

def _store_entry(vt, ucas, pkg8):
    """Faithful imported-packages + shader-map hashes for one package id, from the container header chunk."""
    hi = next(i for i, c in enumerate(vt.chunk_ids) if c[11] == 6)
    vch = io_lib.read_chunk(vt, ucas, hi)
    u = lambda o: struct.unpack_from("<I", vch, o)[0]
    npkg = u(16); ids = 20
    k = next(j for j in range(npkg) if vch[ids + j * 8:ids + j * 8 + 8] == pkg8)
    eh = ids + npkg * 8 + 4 + k * 16
    ipn, ipo, shn, sho = u(eh), u(eh + 4), u(eh + 8), u(eh + 12)
    imp = [vch[eh + ipo + j * 8: eh + ipo + j * 8 + 8] for j in range(ipn)]
    sh = [vch[(eh + 8) + sho + j * 20: (eh + 8) + sho + j * 20 + 20] for j in range(shn)]
    return imp, sh

def build_mod(cont, path, grade):
    """Faithful binary-patch build: patch grade/fog floats into the VANILLA .ucas chunk, rebuild a byte-exact container."""
    key = re.sub(r"\W+", "_", path).strip("_")
    j = f"{CACHE}/{key}.json"; ua = f"{CACHE}/{key}_u"
    if not (os.path.exists(j) and glob.glob(ua + "/**/*.uasset", recursive=True)): load_model(cont, path)
    if not glob.glob(ua + "/**/*.uasset", recursive=True):
        return {"ok": False, "msg": "Map isn't unpacked yet - open it in the editor first."}
    sub = os.path.basename(path)[:-5]
    out = (ROOT + "/_work/editor_out").replace("\\", "/")
    stage = (ROOT + "/_work/editor_stage/" + sub).replace("\\", "/")
    shutil.rmtree(stage, ignore_errors=True); os.makedirs(os.path.dirname(stage), exist_ok=True); shutil.copytree(ua, stage)
    os.makedirs(out, exist_ok=True)
    rp = _run([RETOC, "pack", stage, "-o", out, "--game-paks-dir", PAKS], text=True)
    tmpl = sorted(glob.glob(f"{out}/{sub}_*_P.utoc"))
    if not tmpl: return {"ok": False, "msg": "retoc template pack failed:\n" + ((rp.stdout or "") + (rp.stderr or ""))[-600:]}
    MB = tmpl[-1][:-5]; mt = io_lib.parse_toc(MB + ".utoc")
    if mt.phash_seed_count or mt.chunks_wo_phash or mt.signed:
        return {"ok": False, "msg": "template has phash/signature sections (unsupported)"}
    i1 = next(i for i, c in enumerate(mt.chunk_ids) if c[11] == 6)
    i0 = next((i for i in range(len(mt.chunk_ids)) if mt.chunk_ids[i][11] != 6), None)
    if i0 is None: return {"ok": False, "msg": "template has no data chunk"}
    TARGET = mt.chunk_ids[i0]
    conts = [cont]
    for subs in enum_maps().values():
        for s in subs:
            if s["path"] == path:
                for c in s.get("conts", []):
                    if c not in conts: conts.append(c)
    c0 = vt = vcas = None
    for cc in conts:
        t = io_lib.parse_toc(f"{PAKS}/{cc}")
        if TARGET in t.chunk_ids:
            vcas = f"{PAKS}/{cc}"[:-5] + ".ucas"; c0 = bytearray(io_lib.read_chunk(t, vcas, t.chunk_ids.index(TARGET))); vt = t; break
    if c0 is None: return {"ok": False, "msg": "vanilla chunk not found in any container for " + sub}
    d = json.load(open(j, encoding="utf-8")); pp, ov, fog = _ppv_values(d)
    dirty = set(grade.get("dirty") or [])
    applied, skipped = [], []
    def patch(old, new, lbl, sz):
        n = c0.count(old)
        if n == 1: i = c0.find(old); c0[i:i + sz] = new; applied.append(lbl); return
        skipped.append("%s (%s - needs retoc)" % (lbl, "not found" if n == 0 else "value not unique"))
    def vec4(name, new3):
        cur = pp.get(name)
        if not cur: skipped.append(name + " (not in this PPV)"); return
        if name not in ov: skipped.append(name + " (not overridden - would not render)"); return
        patch(struct.pack("<dddd", *cur), struct.pack("<dddd", new3[0], new3[1], new3[2], cur[3]), "%s %s" % (name, tuple(round(x, 3) for x in new3)), 32)
    if {"gain_r", "gain_g", "gain_b"} & dirty: vec4("ColorGain", grade["gain"])
    if {"gs_r", "gs_g", "gs_b"} & dirty: vec4("ColorGainShadows", grade["gainshadows"])
    if "sat" in dirty: vec4("ColorSaturation", [grade["sat"]] * 3)
    if "contrast" in dirty: vec4("ColorContrast", [grade["contrast"]] * 3)
    if {"fog_r", "fog_g", "fog_b"} & dirty and fog.get("color"):
        fc = fog["color"]; patch(struct.pack("<ffff", *fc), struct.pack("<ffff", grade["fog"][0], grade["fog"][1], grade["fog"][2], fc[3]), "Fog color %s" % (tuple(round(x, 3) for x in grade["fog"]),), 16)
    if "fog_d" in dirty and isinstance(fog.get("density"), float) and round(grade.get("fog_d", 1), 4) != 1:
        patch(struct.pack("<f", fog["density"]), struct.pack("<f", fog["density"] * grade["fog_d"]), "FogDensity x%.3g" % grade["fog_d"], 4)
    if "fog_pos" in dirty and fog.get("pos"):
        patch(struct.pack("<ddd", *fog["pos"]), struct.pack("<ddd", *grade["fog_pos"]), "Fog position %s" % (tuple(round(x, 1) for x in grade["fog_pos"]),), 24)
    if grade.get("edits"): skipped.append("%d component hide/show (bool edit - not supported by binary-patch yet)" % len(grade["edits"]))
    if not applied:
        return {"ok": False, "msg": "Nothing could be patched faithfully:\n  " + ("\n  ".join(skipped) or "(no changes made)")}
    imp, sh = _store_entry(vt, vcas, TARGET[:8])
    chh0 = io_lib.read_chunk(mt, MB + ".ucas", i1)
    ip_data = b"".join(imp); sh_data = b"".join(sh); sh_off = (16 + len(ip_data)) - 8 if sh else 0
    blob = struct.pack("<IIII", len(imp), 16, len(sh), sh_off) + ip_data + sh_data
    ose = struct.unpack_from("<I", chh0, 0x1c)[0]
    chh = chh0[:0x1c] + struct.pack("<I", len(blob)) + blob + chh0[0x20 + ose:]
    CB = mt.cblk_size
    def split(dd): return [dd[x:x + CB] for x in range(0, len(dd), CB)] or [b""]
    data_for = lambda idx: bytes(c0) if idx == i0 else (chh if idx == i1 else io_lib.read_chunk(mt, MB + ".ucas", idx))
    ucas = bytearray(); blk = []; offl = {}; metas = {}
    for idx in range(mt.entry_count):
        dat = data_for(idx); offl[idx] = (len(blk) * CB, len(dat)); metas[idx] = hashlib.sha1(dat).digest()
        for b in split(dat): blk.append((len(ucas), len(b), len(b), 0)); ucas += b
    hdr = bytearray(mt.buf[:144]); struct.pack_into("<I", hdr, 28, len(blk))
    buf = bytearray(hdr) + mt.buf[mt.off_chunkids: mt.off_chunkids + 12 * mt.entry_count]
    for idx in range(mt.entry_count):
        o, l = offl[idx]; buf += o.to_bytes(5, "big") + l.to_bytes(5, "big")
    for bo, cs, us, mi in blk:
        buf += bo.to_bytes(5, "little") + cs.to_bytes(3, "little") + us.to_bytes(3, "little") + bytes([mi])
    buf += mt.buf[mt.off_methods: mt.off_methods + mt.cm_name_count * mt.cm_name_len]
    buf += mt.buf[mt.off_dirindex: mt.off_dirindex + mt.dir_index_size]
    for idx in range(mt.entry_count):
        m = bytearray(mt.meta[idx]); m[:20] = metas[idx]; buf += bytes(m)
    base = f"{out}/{sub}_9999999_P"
    open(base + ".utoc", "wb").write(buf); open(base + ".ucas", "wb").write(ucas)
    if os.path.abspath(MB + ".pak") != os.path.abspath(base + ".pak"): shutil.copy(MB + ".pak", base + ".pak")
    return {"ok": True, "applied": applied, "skipped": skipped, "output": base + ".{pak,ucas,utoc}",
            "note": "Faithful binary-patch - byte-exact except your edits. Copy the 3 files to your ~mods. "
                    "Only OVERRIDDEN grade settings render in-game; fog applies directly. Won't boot until the -64512 engine fix."}

HTML = r"""<!doctype html><meta charset=utf-8><title>MR World Editor</title><style>
:root{--ln:#6b6b8a;--mut:#d8dbf0;--acc:#9ab2ff}
*{box-sizing:border-box}body{margin:0;background:#000;color:#fff;font:700 14px/1.5 Segoe UI,system-ui,sans-serif}
.bar{display:flex;align-items:center;gap:12px;padding:11px 18px;background:#000;border-bottom:1px solid var(--ln)}
.bar b{font-size:16px}
select,button{background:#000;color:#fff;border:1px solid var(--ln);border-radius:7px;padding:7px 11px;font:inherit;font-weight:700}
button{cursor:pointer}button:hover{border-color:#9a9ac0}.go{background:var(--acc);border-color:var(--acc);color:#06080f}
.tagb{color:var(--acc);font-size:11px;border:1.5px solid var(--acc);border-radius:9px;padding:1px 8px}
.ibtn{width:26px;height:26px;border-radius:50%;padding:0;font:italic 700 14px Georgia,serif;border:1.5px solid var(--acc);color:var(--acc);background:#000;line-height:22px;text-align:center}
.guide{display:none;position:fixed;top:54px;right:14px;width:312px;background:#000;border:1px solid var(--acc);border-radius:10px;padding:12px 15px;z-index:60;box-shadow:0 10px 34px #000c}
.guide.show{display:block}.guide b{color:var(--acc)}.guide ul{margin:8px 0 0;padding-left:18px}.guide li{margin:5px 0}
.cfgr{margin-top:9px;font-size:12px;color:var(--mut)}
.cfgi{display:block;width:100%;margin-top:3px;background:#000;color:#fff;border:1px solid var(--ln);border-radius:6px;padding:6px 8px;font:inherit;font-weight:700}
.sw{margin-left:auto;color:var(--mut);font-size:12px}
.page{padding:14px}
.top{display:flex;gap:14px;align-items:flex-start}.tcol{flex:1;min-width:0}
.prev,.mv{display:block;width:100%;background:#0d0f17;border-radius:11px;border:1px solid var(--ln)}
.mv{cursor:crosshair}
.lb{position:fixed;top:0;left:0;right:0;height:3px;z-index:200;overflow:hidden;display:none}
.lb.on{display:block}
.lb::before{content:'';position:absolute;height:100%;width:35%;background:var(--acc);border-radius:2px;animation:lbs 1.1s ease-in-out infinite}
@keyframes lbs{0%{left:-35%}50%{left:55%}100%{left:100%}}
.note{font-size:12px;color:var(--mut);margin:6px 2px 0}
.banner{background:#3a1d1d;border:1px solid #ff8688;color:#ffd9d9;border-radius:8px;padding:9px 11px;margin-bottom:11px;font-size:12.5px;font-weight:700}
.wrap{display:flex;gap:14px;align-items:flex-start;margin-top:14px}
.pn{background:#000;border:1px solid var(--ln);border-radius:10px;padding:13px}
.L{flex:0 0 380px;max-height:78vh;overflow:auto}.R{flex:1;min-width:340px;max-height:78vh;overflow:auto}
.h{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);margin:16px 0 9px}.h:first-child{margin-top:0}
.c{display:flex;align-items:center;gap:7px;padding:4px 5px;border-radius:6px}.c:hover{background:#17171f}
.c .n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.c .k{color:var(--mut);font-size:12px}
.pill{font-size:10px;padding:0 7px;line-height:16px;border-radius:9px;border:1.5px solid;background:transparent;white-space:nowrap;font-weight:700}
.pill.ppv{color:#c7bcff;border-color:#c7bcff}.pill.on{color:#74e6a4;border-color:#74e6a4}.pill.off{color:#ff8688;border-color:#ff8688}
.tgl{cursor:pointer}.tgl:hover{filter:brightness(1.35)}
.tag{font-size:10px;padding:0 6px;line-height:15px;border-radius:9px;border:1.5px solid;background:transparent;margin-left:6px;font-weight:700}
.tag.f{color:#74e6a4;border-color:#74e6a4}.tag.r{color:#f4b65a;border-color:#f4b65a}.tag.x{color:#9ab2ff;border-color:#9ab2ff}
.row{margin-top:9px}.rh{display:flex;justify-content:space-between;margin-bottom:1px}.rh .v{color:var(--mut);font-variant-numeric:tabular-nums}
input[type=range]{width:100%;accent-color:var(--acc)}
.fnl{display:flex;align-items:center;gap:4px;font-size:12px;color:var(--mut);font-weight:700;white-space:nowrap}
.fpr{display:flex;gap:6px}.fpr input{flex:1;width:0;background:#000;color:#fff;border:1px solid var(--ln);border-radius:6px;padding:5px 6px;font:inherit;font-weight:700}
#st{margin-top:12px;font-size:13px;white-space:pre-wrap;background:#000;border:1px solid var(--ln);border-radius:8px;padding:10px;display:none}
</style>
<div id=lb class=lb></div>
<div class=bar><b>MR World Editor</b><span class=tagb>BUILD 12 - real build</span><label class=fnl title="Show in-game names and mode"><input type=checkbox id=fn onchange=rebuildMaps()>Friendly</label><select id=map></select><select id=sub></select><span class=sw id=sw></span><button onclick=openCfg() title="Game Paks + tools folders" style="padding:4px 9px">Paths</button><button class=ibtn title="How it works" onclick="document.getElementById('cfg').classList.remove('show');document.getElementById('guide').classList.toggle('show')">i</button></div>
<div id=guide class=guide><b>How it works</b><ul><li>Pick a map and sublevel at the top.</li><li>The editor finds its post process volume for you.</li><li>Drag the sliders to change color, light and fog.</li><li>The image is a rough preview, not exact.</li><li>Click a shipped grade to load a look we made.</li><li>Turn parts on or off in the left list.</li><li>Click Build mod to make the 3 mod files.</li><li>Copy them into the game ~mods folder to use.</li></ul></div>
<div id=cfg class=guide style="width:520px"><b>Settings - paths</b><div class=cfgr>Game Paks folder<input id=cfg_paks class=cfgi></div><div class=cfgr>Tools folder (retoc + UAssetGUI)<input id=cfg_tools class=cfgi></div><div style="margin-top:11px;display:flex;gap:8px;align-items:center"><button class=go onclick=saveCfg()>Save</button><span id=cfg_msg class=note></span></div><div class=note style="margin-top:6px">Restart the app after changing paths. The tools folder needs retoc-rivals-cli/ (with oo2core dll) and UAssetGUI.exe.</div></div>
<div class=page>
<div class=top>
<div class=tcol><canvas class=prev id=cv></canvas><div class=note>Edit Visualizer, not 100% accurate</div></div>
<div class=tcol><canvas class=mv id=mv width=900 height=560></canvas><div class=note id=mvnote>Map view - pick a map</div></div>
</div>
<div class=wrap>
<div class="pn L"><div class=h>Components <span id=ec style="color:var(--acc)"></span></div><div id=cm>Pick a map…</div></div>
<div class="pn R"><div id=nb class=banner style="display:none">⚠ No post-process volume in this sublevel — color/bloom/vignette have nowhere to write. The editor auto-scans for one; if none is found, pick another sublevel or map.</div><div id=gr></div><div id=fpos></div>
<div class=h>Shipped grades <span style="text-transform:none;letter-spacing:0;color:var(--mut)">— what we used here</span></div>
<div id=ps style="display:flex;gap:8px;flex-wrap:wrap"></div>
<div style="margin-top:12px;display:flex;gap:8px"><button onclick=resetAll()>Reset</button><button class=go style=margin-left:auto onclick=bd()>Build mod</button></div>
<div id=st></div></div>
</div></div>
<script>
let MAPS={},M=null,CUR=null,EDITS={},BASE={},FRIENDLY={};
const KNOBS=[['Color grade'],
['gain_r','Color gain · R','ColorGain',0,2.5,.01,1],['gain_g','Color gain · G','ColorGain',0,2.5,.01,1],['gain_b','Color gain · B','ColorGain',0,2.5,.01,1],
['gs_r','Gain shadows · R','ColorGainShadows',0,2.5,.01,1],['gs_g','Gain shadows · G','ColorGainShadows',0,2.5,.01,1],['gs_b','Gain shadows · B','ColorGainShadows',0,2.5,.01,1],
['sat','Saturation','ColorSaturation',0,2,.01,1],['contrast','Contrast','ColorContrast',.5,2,.01,1],
['Light FX'],['bloom','Bloom intensity','BloomIntensity',0,4,.05,1],['vignette','Vignette','VignetteIntensity',0,1.5,.05,0],
['Atmosphere fog (XYZ)'],['fog_r','Fog color · X','_fog',0,2,.01,1],['fog_g','Fog color · Y','_fog',0,2,.01,1],['fog_b','Fog color · Z','_fog',0,2,.01,1],['fog_d','Fog density ×','_fog',0,3,.01,1]];
const G={};KNOBS.forEach(k=>{if(k.length>1)G[k[0]]=k[6]});G.fp_x=G.fp_y=G.fp_z=0;
async function J(u,o){return (await fetch(u,o)).json()}
let BUSY=0;function busy(d){BUSY=Math.max(0,BUSY+d);document.getElementById('lb').classList.toggle('on',BUSY>0);}
async function openCfg(){const c=await J('/api/config');document.getElementById('cfg_paks').value=c.paks||'';document.getElementById('cfg_tools').value=c.tools||'';
document.getElementById('cfg_msg').textContent=(c.paks_ok?'Paks OK. ':'Paks NOT found here. ')+(c.tools_ok?'Tools OK.':'Tools NOT found here.');
document.getElementById('guide').classList.remove('show');document.getElementById('cfg').classList.toggle('show');}
async function saveCfg(){const b={paks:document.getElementById('cfg_paks').value,tools:document.getElementById('cfg_tools').value};
const r=await J('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
document.getElementById('cfg_msg').textContent=r.ok?'Saved - restart the app to apply.':'Error: '+(r.msg||'');}
async function init(){loadImg();mvInit();renderG();MAPS=await J('/api/maps');FRIENDLY=await J('/api/names');const ms=document.getElementById('map');
ms.onchange=fillSub;rebuildMaps();if(MAPS['AsgardE01'])ms.value='AsgardE01';
document.getElementById('sub').onchange=()=>load(false);fillSub();}
function rebuildMaps(){const ms=document.getElementById('map'),cur=ms.value||'AsgardE01',fn=document.getElementById('fn').checked;
const arr=Object.keys(MAPS).map(k=>({k,label:(fn&&FRIENDLY[k])?FRIENDLY[k][0]+' - '+FRIENDLY[k][1]:k}));
arr.sort((a,b)=>a.label.localeCompare(b.label));
ms.innerHTML=arr.map(o=>`<option value="${o.k}"${o.k===cur?' selected':''}>${o.label}</option>`).join('');}
function fillSub(){const k=document.getElementById('map').value,ss=document.getElementById('sub');
const subs=MAPS[k];ss.innerHTML=subs.map((s,i)=>`<option value=${i}>${s.sub}</option>`).join('');
let hi=subs.findIndex(s=>/HighQuality$/.test(s.sub));if(hi<0)hi=0;ss.value=hi;load(true);}
async function load(scan){busy(1);try{const k=document.getElementById('map').value,i=+document.getElementById('sub').value;
CUR=MAPS[k][i];EDITS={};document.getElementById('cm').textContent='Loading '+CUR.sub+'…';
M=await J('/api/model?cont='+CUR.cont+'&path='+encodeURIComponent(CUR.path));
if(M.error){document.getElementById('cm').textContent='✗ '+M.error;document.getElementById('sw').textContent='';return;}
if(scan && !M.components.some(c=>c.is_ppv)){
const subs=MAPS[k],pri=s=>{const n=s.sub.toLowerCase();
return /highquality$/.test(n)?0:/lowquality$/.test(n)?1:/_(art|lighting|light|env|main)$/.test(n)?2:/(audio|sfx|spatial|collision|nav|aiconfig|config|svon|spectator|_s\d)/i.test(n)?9:3;};
const order=subs.map((s,n)=>n).filter(n=>n!==i&&pri(subs[n])<9).sort((a,b)=>pri(subs[a])-pri(subs[b]));
for(const n of order){document.getElementById('cm').textContent='No PPV in '+CUR.sub+' — scanning '+subs[n].sub+'…';
const mm=await J('/api/model?cont='+subs[n].cont+'&path='+encodeURIComponent(subs[n].path));
if(mm.components&&mm.components.some(c=>c.is_ppv)){document.getElementById('sub').value=n;CUR=subs[n];M=mm;break;}}}
const dis=M.components.filter(c=>c.enables&&c.enables.bVisible===false).length,ppv=M.components.filter(c=>c.is_ppv).length;
document.getElementById('sw').textContent=`${M.components.length} components · ${dis} hidden · ${ppv} PPV`+(M.fog?' · fog':'');
seed();BASE=Object.assign({},G);renderC();renderG();renderFogPos();renderPresets();updEC();loadMapViz(k,CUR.sub);
const nb=document.getElementById('nb');if(nb)nb.style.display=ppv?'none':'block';}finally{busy(-1);}}
function seed(){const p=M.components.find(c=>c.is_ppv),gd=(p&&p.grade)||{},S=(id,v)=>{if(typeof v==='number')G[id]=v};
S('gain_r',gd.ColorGain?gd.ColorGain.value[0]:1);S('gain_g',gd.ColorGain?gd.ColorGain.value[1]:1);S('gain_b',gd.ColorGain?gd.ColorGain.value[2]:1);
S('gs_r',gd.ColorGainShadows?gd.ColorGainShadows.value[0]:1);S('gs_g',gd.ColorGainShadows?gd.ColorGainShadows.value[1]:1);S('gs_b',gd.ColorGainShadows?gd.ColorGainShadows.value[2]:1);
S('sat',gd.ColorSaturation?gd.ColorSaturation.value[0]:1);S('contrast',gd.ColorContrast?gd.ColorContrast.value[0]:1);
S('bloom',gd.BloomIntensity?gd.BloomIntensity.value:1);S('vignette',gd.VignetteIntensity?gd.VignetteIntensity.value:0);
const fc=M.fog&&M.fog.color;S('fog_r',fc?fc[0]:1);S('fog_g',fc?fc[1]:1);S('fog_b',fc?fc[2]:1);G.fog_d=1;
const fp=M.fog&&M.fog.pos;G.fp_x=fp?fp[0]:0;G.fp_y=fp?fp[1]:0;G.fp_z=fp?fp[2]:0;}
function renderC(){document.getElementById('cm').innerHTML=M.components.map(c=>{
const e=c.enables||{},vis=(c.idx in EDITS)?EDITS[c.idx]:(e.bVisible!==false);
const f=c.hideable?`<span class="pill ${vis?'on':'off'} tgl" onclick="tgl(${c.idx})">${vis?'visible':'hidden'}</span>`:'';
return `<div class=c><span class=n>${c.name}</span><span class=k>${c.cls}</span>${c.is_ppv?'<span class="pill ppv">PPV</span>':''}${f}</div>`}).join('')||'No components';}
function tgl(idx){const c=M.components.find(x=>x.idx===idx),v0=((c.enables||{}).bVisible!==false),cur=(idx in EDITS)?EDITS[idx]:v0,nv=!cur;
if(nv===v0)delete EDITS[idx];else EDITS[idx]=nv;renderC();updEC();}
function updEC(){const n=Object.keys(EDITS).length;document.getElementById('ec').textContent=n?`· ${n} edited`:'';}
function go(s){if(!M)return null;const p=M.components.find(c=>c.is_ppv);return p&&p.grade[s];}
function tg(s){if(s==='_fog')return (M&&M.fog)?'<span class="tag x">editable</span>':'<span class="tag r">no fog</span>';const g=go(s);return g&&g.override?'<span class="tag f">faithful</span>':'<span class="tag r">retoc</span>';}
function sl(k){const id=k[0];return `<div class=row><div class=rh><span>${k[1]} ${tg(k[2])}</span><span class=v id=v_${id}>${(+G[id]).toFixed(2)}</span></div><input type=range id=s_${id} min=${k[3]} max=${k[4]} step=${k[5]} value=${G[id]}></div>`}
function renderG(){let h='';for(const k of KNOBS)h+=(k.length===1)?`<div class=h>${k[0]}</div>`:sl(k);
document.getElementById('gr').innerHTML=h;
for(const k of KNOBS){if(k.length===1)continue;const id=k[0],s=document.getElementById('s_'+id);
if(s)s.oninput=()=>{G[id]=parseFloat(s.value);document.getElementById('v_'+id).textContent=G[id].toFixed(2);ap();}}ap();}
let SRC=null,CX=null,RAF=0;
function loadImg(){const cv=document.getElementById('cv');CX=cv.getContext('2d');const im=new Image();
im.onload=()=>{const w=Math.min(820,im.width),h=Math.round(im.height*w/im.width);cv.width=w;cv.height=h;CX.drawImage(im,0,0,w,h);SRC=CX.getImageData(0,0,w,h);ap();};im.src='/image';}
function ap(){if(!RAF)RAF=requestAnimationFrame(render);}
function render(){RAF=0;if(!SRC)return;const s=SRC.data,d=new Uint8ClampedArray(s.length),W=SRC.width,H=SRC.height,
gr=G.gain_r,gg=G.gain_g,gb=G.gain_b,sgr=G.gs_r,sgg=G.gs_g,sgb=G.gs_b,st=G.sat,cn=G.contrast,vg=G.vignette,bl=G.bloom-1,cx=W/2,cy=H/2,mr2=cx*cx+cy*cy;
for(let i=0,px=0;i<s.length;i+=4,px++){let r=s[i],g=s[i+1],b=s[i+2];const L=.299*r+.587*g+.114*b;
r=L+st*(r-L);g=L+st*(g-L);b=L+st*(b-L);
r=(r-127.5)*cn+127.5;g=(g-127.5)*cn+127.5;b=(b-127.5)*cn+127.5;
const sw=L<128?1-L/128:0;
r*=gr*(1+(sgr-1)*sw);g*=gg*(1+(sgg-1)*sw);b*=gb*(1+(sgb-1)*sw);
if(bl){const hi=L>165?(L-165)/90:0,add=bl*hi*75;r+=add;g+=add;b+=add;}
if(vg){const X=(px%W)-cx,Y=((px/W)|0)-cy,vf=1-vg*((X*X+Y*Y)/mr2)*.85;r*=vf;g*=vf;b*=vf;}
d[i]=r;d[i+1]=g;d[i+2]=b;d[i+3]=s[i+3];}
CX.putImageData(new ImageData(d,W,H),0,0);}
let MVD=null,MVsc=1,MVpx=0,MVpy=0,MVb=null,MVdrag=false,MVlx=0,MVly=0;
function _sx(x){return (x-MVb.minx)*MVsc+MVb.pad+MVpx}
function _sy(y){return MVb.H-MVb.pad-(y-MVb.miny)*MVsc+MVpy}
function _wx(x){return (x-MVb.pad-MVpx)/MVsc+MVb.minx}
function _wy(y){return (MVb.H-MVb.pad+MVpy-y)/MVsc+MVb.miny}
async function loadMapViz(mapname,sub){busy(1);try{const note=document.getElementById('mvnote'),cv=document.getElementById('mv');
note.textContent='Map view - loading geometry for '+(sub||mapname)+' (first time can take ~15s)';
cv.getContext('2d').clearRect(0,0,cv.width,cv.height);MVD=null;MVb=null;
let dat;try{dat=await J('/api/mapviz?map='+encodeURIComponent(mapname)+'&sub='+encodeURIComponent(sub||''));}catch(e){note.textContent='Map view - load failed';return;}
if(mapname!==document.getElementById('map').value||(CUR&&sub&&CUR.sub!==sub))return;
if(dat&&dat.error){note.textContent='Map view - '+dat.error;return;}
if(!dat||!dat.pts||!dat.pts.length){note.textContent='Map view - no geometry found for this subworld';return;}
MVD=dat;const P=dat.pts,S=dat.spawns,ax=P.map(p=>p[0]).concat(S.map(s=>s[0])),ay=P.map(p=>p[1]).concat(S.map(s=>s[1]));
const minx=Math.min(...ax),maxx=Math.max(...ax),miny=Math.min(...ay),maxy=Math.max(...ay),zs=P.map(p=>p[2]);
MVb={minx,maxx,miny,maxy,minz:Math.min(...zs),maxz:Math.max(...zs),W:cv.width,H:cv.height,pad:46};
MVsc=Math.min((cv.width-92)/((maxx-minx)||1),(cv.height-92)/((maxy-miny)||1));MVpx=0;MVpy=0;
note.textContent='Map view - '+(dat.title||mapname)+': '+P.length+' meshes, '+S.length+' spawns (drag/zoom/hover)';drawMV();}finally{busy(-1);}}
function drawMV(){const cv=document.getElementById('mv'),ctx=cv.getContext('2d');ctx.clearRect(0,0,cv.width,cv.height);
if(!MVD||!MVb)return;const b=MVb;
ctx.strokeStyle='#1b2130';ctx.fillStyle='#5b6373';ctx.font='10px monospace';ctx.lineWidth=1;
for(let gx=Math.ceil(b.minx/5000)*5000;gx<=b.maxx;gx+=5000){const px=_sx(gx);ctx.beginPath();ctx.moveTo(px,0);ctx.lineTo(px,b.H);ctx.stroke();ctx.fillText(gx,px+2,12);}
for(let gy=Math.ceil(b.miny/5000)*5000;gy<=b.maxy;gy+=5000){const py=_sy(gy);ctx.beginPath();ctx.moveTo(0,py);ctx.lineTo(b.W,py);ctx.stroke();ctx.fillText(gy,2,py-2);}
ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(_sx(0),_sy(0),3,0,7);ctx.fill();
const dz=(b.maxz-b.minz)||1;for(const p of MVD.pts){ctx.fillStyle='hsl('+(220-220*((p[2]-b.minz)/dz))+',70%,55%)';ctx.beginPath();ctx.arc(_sx(p[0]),_sy(p[1]),2.2,0,7);ctx.fill();}
ctx.lineWidth=2.5;ctx.strokeStyle='#e8a33d';for(const s of MVD.spawns){ctx.beginPath();ctx.arc(_sx(s[0]),_sy(s[1]),8,0,7);ctx.stroke();}}
function mvInit(){const cv=document.getElementById('mv');if(!cv)return;
cv.addEventListener('mousemove',e=>{if(!MVb)return;const r=cv.getBoundingClientRect(),mx=(e.clientX-r.left)*cv.width/r.width,my=(e.clientY-r.top)*cv.height/r.height;
if(MVdrag){MVpx+=mx-MVlx;MVpy+=my-MVly;MVlx=mx;MVly=my;drawMV();return;}
let best=null,bd=1e9;for(const p of MVD.pts){const dx=_sx(p[0])-mx,dy=_sy(p[1])-my,d=dx*dx+dy*dy;if(d<bd){bd=d;best=p;}}
const note=document.getElementById('mvnote');
if(best&&bd<200)note.textContent=MVD.names[best[3]]+'  X='+best[0]+' Y='+best[1]+' Z='+best[2];
else note.textContent='world ('+Math.round(_wx(mx))+', '+Math.round(_wy(my))+')';});
cv.addEventListener('mousedown',e=>{const r=cv.getBoundingClientRect();MVdrag=true;MVlx=(e.clientX-r.left)*cv.width/r.width;MVly=(e.clientY-r.top)*cv.height/r.height;});
window.addEventListener('mouseup',()=>{MVdrag=false;});
cv.addEventListener('wheel',e=>{if(!MVb)return;e.preventDefault();const r=cv.getBoundingClientRect(),mx=(e.clientX-r.left)*cv.width/r.width,my=(e.clientY-r.top)*cv.height/r.height,wx=_wx(mx),wy=_wy(my);MVsc*=e.deltaY<0?1.15:0.87;MVpx=mx-((wx-MVb.minx)*MVsc+MVb.pad);MVpy=my-(MVb.H-MVb.pad-(wy-MVb.miny)*MVsc);drawMV();},{passive:false});}
function setG(id,v){if(!(id in G))return;G[id]=+v;const e=document.getElementById('s_'+id);if(e){e.value=v;document.getElementById('v_'+id).textContent=(+v).toFixed(2)}}
function renderPresets(){const el=document.getElementById('ps'),P=(M&&M.presets)||[];
el.innerHTML=P.length?P.map((p,i)=>{const x=p.extra||{},ex=Object.keys(x).map(k=>k+' '+x[k]).join(', ');
return `<button onclick=applyPreset(${i}) title="gain ${p.gain?p.gain.map(n=>n.toFixed(2)).join('/'):'—'} · sat ${p.sat} · contrast ${p.contrast}${ex?' · '+ex:''}">${p.name}</button>`}).join(''):'<span class=note>No shipped grade on record — start from the sliders.</span>';}
function applyPreset(i){const p=M.presets[i],g=p.gain||[1,1,1],x=p.extra||{};
setG('gain_r',g[0]);setG('gain_g',g[1]);setG('gain_b',g[2]);setG('sat',p.sat);setG('contrast',p.contrast);
if(x.gainshadows){setG('gs_r',x.gainshadows[0]);setG('gs_g',x.gainshadows[1]);setG('gs_b',x.gainshadows[2]);}
if('bloom' in x)setG('bloom',x.bloom);if('vignette' in x)setG('vignette',x.vignette);if('fog' in x)setG('fog_d',x.fog);ap();}
function resetAll(){for(const k of KNOBS)if(k.length>1)setG(k[0],k[6]);ap();}
function renderFogPos(){const el=document.getElementById('fpos');if(!el)return;const has=M.fog&&M.fog.pos;
el.innerHTML='<div class=row><div class=rh><span>Fog position (XYZ) <span class="tag x">move</span></span></div><div class=fpr><input type=number id=fp_x step=10><input type=number id=fp_y step=10><input type=number id=fp_z step=10></div></div>'+(has?'':'<div class=note>No fog in this sublevel.</div>');
['fp_x','fp_y','fp_z'].forEach(id=>{const e=document.getElementById(id);if(e){e.value=G[id];e.oninput=()=>{G[id]=parseFloat(e.value)||0;}}});}
async function bd(){busy(1);try{const st=document.getElementById('st');st.style.display='block';st.textContent='Building... (round-trip, ~10s)';
const dirty=KNOBS.filter(k=>k.length>1&&G[k[0]]!==BASE[k[0]]).map(k=>k[0]);
if(['fp_x','fp_y','fp_z'].some(id=>G[id]!==BASE[id]))dirty.push('fog_pos');
const r=await J('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cont:CUR.cont,path:CUR.path,
gain:[G.gain_r,G.gain_g,G.gain_b],gainshadows:[G.gs_r,G.gs_g,G.gs_b],sat:G.sat,contrast:G.contrast,bloom:G.bloom,vignette:G.vignette,fog:[G.fog_r,G.fog_g,G.fog_b],fog_d:G.fog_d,fog_pos:[G.fp_x,G.fp_y,G.fp_z],dirty:dirty,edits:EDITS})});
st.textContent=r.ok?'✓ '+r.output+'\n\nApplied:\n  '+(r.applied.join('\n  ')||'(none)')+((r.skipped&&r.skipped.length)?'\n\nSkipped:\n  '+r.skipped.join('\n  '):'')+'\n\n'+r.note:'✗ '+r.msg;}finally{busy(-1);}}
init();
</script>"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _s(self, code, body, ct="application/json"):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        try:
            if u.path == "/": self._s(200, HTML, "text/html;charset=utf-8")
            elif u.path == "/image":
                if IMG_B64: self._s(200, base64.b64decode(IMG_B64), "image/jpeg")
                else: self._s(200, open(IMAGE, "rb").read(), "image/png")
            elif u.path == "/api/maps": self._s(200, enum_maps())
            elif u.path == "/api/names": self._s(200, FRIENDLY)
            elif u.path == "/api/mapviz": self._s(200, mapviz_points(q["map"][0], (q.get("sub") or [None])[0]))
            elif u.path == "/api/config": self._s(200, {"paks": PAKS, "tools": TOOLS, "paks_ok": bool(glob.glob(PAKS + "/pakchunk*.utoc")), "tools_ok": os.path.exists(RETOC) and os.path.exists(UAG)})
            elif u.path == "/api/model": self._s(200, load_model(q["cont"][0], q["path"][0]))
            else: self._s(404, {"error": "nf"})
        except Exception as e: self._s(200, {"error": f"{type(e).__name__}: {e}"})
    def do_POST(self):
        p = urlparse(self.path).path
        g = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if p == "/api/build":
            try: self._s(200, build_mod(g["cont"], g["path"], g))
            except Exception as e: self._s(200, {"ok": False, "msg": f"{type(e).__name__}: {e}"})
        elif p == "/api/config":
            try:
                cfg = load_config()
                if g.get("paks"): cfg["paks"] = g["paks"].strip()
                if g.get("tools"): cfg["tools"] = g["tools"].strip()
                json.dump(cfg, open(CONFIG_FILE, "w"))
                self._s(200, {"ok": True})
            except Exception as e: self._s(200, {"ok": False, "msg": str(e)})
        else: self._s(404, {"error": "nf"})

if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), H)
    print(f"MR World Editor: http://localhost:{PORT}  (Ctrl+C to stop)")
    threading.Timer(0.6, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nstopped")
