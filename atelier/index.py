import os, sys, glob, json
from atelier.config import PAKS, _CACHE  # sets MR_TOOLS env var before io_lib reads it
import io_lib

_INDEX      = None
_CACHE_FILE = os.path.join(_CACHE, "cli_index_cache.json")
_CACHE_VER  = "v3"  # bump to invalidate cached indexes

def _index_utocs():
    char  = glob.glob(PAKS + "/pakchunkCharacter-Windows*.utoc")
    patch = glob.glob(PAKS + "/Patch_-Windows*_P.utoc")
    return sorted(char + patch)

def _utoc_key():
    parts = [_CACHE_VER]
    for f in _index_utocs():
        s = os.stat(f)
        parts.append(f"{os.path.basename(f)}:{s.st_size}:{int(s.st_mtime)}")
    return "|".join(parts)

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
    _INDEX = []
    for utoc in utocs:
        try:
            t    = io_lib.parse_toc(utoc)
            ents = io_lib.parse_dir_index(t)
        except Exception as e:
            print(f"  [warn] {os.path.basename(utoc)}: {e}", file=sys.stderr); continue
        cont = os.path.basename(utoc)
        for p, _ in ents:
            if "Marvel/Content/Marvel/" in p and p.lower().endswith(".uasset"):
                _INDEX.append((p, cont))
    os.makedirs(_CACHE, exist_ok=True)
    json.dump({"key": key, "entries": _INDEX}, open(_CACHE_FILE, "w"))
    return _INDEX
