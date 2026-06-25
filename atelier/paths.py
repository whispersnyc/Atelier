import re
from atelier.index import ensure_index

PAK_GAME_PREFIX = "Marvel/Content/Marvel"

def char_id(skin_id): return skin_id[:4]

def skin_needle(skin_id):
    return f"/Characters/{char_id(skin_id)}/{skin_id}/".lower()

def skin_rel(pak_path, skin_id):
    """Pak path -> relative path from the skin folder (original case, no .uasset ext)."""
    needle = skin_needle(skin_id)
    pl     = pak_path.lower().replace("\\", "/")
    idx    = pl.find(needle)
    if idx < 0: return pak_path
    rel = pak_path[idx + len(needle):]
    return rel[:-7] if rel.lower().endswith(".uasset") else rel

def pak_rel(pak_path):
    """Strip ../../../ prefix (and .uasset ext) -> mount-relative path."""
    r = re.sub(r"^(\.\./)+", "", pak_path.replace("\\", "/"))
    return r[:-7] if r.lower().endswith(".uasset") else r

def game_rel_for_skin(skin_id, tex_rel):
    """Storage-relative path for a skin asset: Characters/{cid}/{skin_id}/{tex_rel}"""
    cid = char_id(skin_id)
    return f"Characters/{cid}/{skin_id}/{tex_rel}"

def pak_game_path(game_rel):
    """Prefix a storage-relative game_rel with Marvel/Content/Marvel/ for pak operations."""
    return f"{PAK_GAME_PREFIX}/{game_rel}"

def skin_entries(skin_id):
    needle = skin_needle(skin_id)
    return [(p, c) for p, c in ensure_index() if needle in p.lower()]

def filter_subpath(entries, skin_id, subpath):
    """Narrow entries to those whose skin-relative path starts with subpath."""
    if not subpath: return entries
    needle = skin_needle(skin_id)
    sp = subpath.lower().replace("\\", "/").strip("/")
    if sp.endswith("/*"): sp = sp[:-2].strip("/")
    elif sp.endswith("*"): sp = sp[:-1]
    def _match(p):
        pl  = p.lower().replace("\\", "/")
        idx = pl.find(needle)
        if idx < 0: return False
        tail = pl[idx + len(needle):]
        return tail.startswith(sp) if sp else True
    return [(p, c) for p, c in entries if _match(p)]
