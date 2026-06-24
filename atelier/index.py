import os, sys, glob, json
from atelier.config import PAKS, _WORK  # sets MR_TOOLS env var before io_lib reads it
import io_lib

_INDEX      = None
_CACHE_FILE = os.path.join(_WORK, "cli_index_cache.json")

def _utoc_key():
    parts = []
    for f in sorted(glob.glob(PAKS + "/*.utoc")):
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
