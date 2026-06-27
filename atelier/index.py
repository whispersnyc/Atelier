import os, sys, glob, json, re
from atelier.config import PAKS, _CACHE  # sets MR_TOOLS env var before io_lib reads it
import io_lib

_INDEX      = None
_CACHE_FILE = os.path.join(_CACHE, "cli_index_cache.json")
_CACHE_VER  = "v6"  # bump to invalidate cached indexes

# The two content mounts we care about.  Both map to the same virtual namespace.
_CONTENT_PREFIXES = (
    "Marvel/Content/Marvel/",
    "Marvel/Content/Marvel_LQ/",
)

def _virtual_path(raw):
    """Strip ../../../ prefix and content mount; return (virtual_rel_path, content_prefix) or (None, None)."""
    clean = re.sub(r"^(\.\./)+", "", raw.replace("\\", "/"))
    cl = clean.lower()
    for pfx in _CONTENT_PREFIXES:
        if cl.startswith(pfx.lower()):
            return clean[len(pfx):], pfx
    return None, None

def _index_utocs():
    # Ascending ASCII/Unicode order (case-insensitive): '-' (45) before '_' (95), so base paks
    # (pakchunkFoo-Windows) sort before patch paks (Patch_-Windows_YYYYMMDD_P), and patch paks
    # sort chronologically.  Later entries override earlier ones for the same virtual path.
    return sorted(glob.glob(PAKS + "/*.utoc"), key=lambda p: os.path.basename(p).lower())

def _utoc_key():
    parts = [_CACHE_VER]
    for f in _index_utocs():
        s = os.stat(f)
        parts.append(f"{os.path.basename(f)}:{s.st_size}:{int(s.st_mtime)}")
    return "|".join(parts)

def get_content_prefix(game_rel):
    """Return the content mount prefix for a virtual game_rel (used to reconstruct full pak paths).
    Falls back to the primary HQ prefix for assets not in the index."""
    ensure_index()
    gr = game_rel.lower()
    if not gr.endswith(".uasset"):
        gr += ".uasset"
    for vp, _cont, pfx in _INDEX:
        if vp.lower() == gr:
            return pfx
    return "Marvel/Content/Marvel/"

def ensure_index():
    global _INDEX
    if _INDEX is not None: return _INDEX
    key = _utoc_key()
    try:
        c = json.load(open(_CACHE_FILE, encoding="utf-8"))
        if c.get("key") == key:
            _INDEX = [tuple(e) for e in c["entries"]]; return _INDEX
    except Exception: pass
    utocs = _index_utocs()
    print(f"  Indexing {len(utocs)} pak containers (first run, cached after)...", file=sys.stderr)
    # Dedup by virtual path (lower-cased). Priority rules (highest wins):
    #   1. Patch paks (_P.utoc) always win — they are game updates.
    #   2. Marvel/ (HQ) beats Marvel_LQ/ for the same virtual path — prefer high-quality source.
    #   3. Within same prefix, later utoc (alphabetically) wins — chronological patch order.
    seen = {}  # virt_lower -> (virt, cont, pfx)
    for utoc in utocs:
        try:
            t    = io_lib.parse_toc(utoc)
            ents = io_lib.parse_dir_index(t)
        except Exception as e:
            print(f"  [warn] {os.path.basename(utoc)}: {e}", file=sys.stderr); continue
        cont     = os.path.basename(utoc)
        is_patch = cont.lower().endswith("_p.utoc")
        for p, _ in ents:
            if not p.lower().endswith(".uasset"):
                continue
            vp, pfx = _virtual_path(p)
            if vp is None:
                continue
            vp_key   = vp.lower()
            existing = seen.get(vp_key)
            if existing is not None and not is_patch:
                ex_pfx = existing[2]
                # Don't let an LQ entry overwrite an already-stored HQ entry.
                if ex_pfx == "Marvel/Content/Marvel/" and pfx == "Marvel/Content/Marvel_LQ/":
                    continue
            seen[vp_key] = (vp, cont, pfx)
    _INDEX = list(seen.values())
    os.makedirs(_CACHE, exist_ok=True)
    json.dump({"key": key, "entries": _INDEX}, open(_CACHE_FILE, "w"))
    return _INDEX
