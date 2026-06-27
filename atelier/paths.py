import re
from atelier.index import ensure_index, get_content_prefix

PAK_GAME_PREFIX = "Marvel/Content/Marvel"  # kept as a constant for external callers

def char_id(skin_id): return skin_id[:4]

def _skin_prefix(skin_id):
    return f"Characters/{char_id(skin_id)}/{skin_id}/".lower()

def skin_rel(virt_path, skin_id):
    """Virtual path -> relative path from the skin folder (original case, no .uasset ext)."""
    pfx = _skin_prefix(skin_id)
    if not virt_path.lower().startswith(pfx):
        return virt_path
    rel = virt_path[len(pfx):]
    return rel[:-7] if rel.lower().endswith(".uasset") else rel

def pak_rel(pak_path):
    """Strip ../../../ prefix (and .uasset ext) -> mount-relative path. Legacy helper."""
    r = re.sub(r"^(\.\./)+", "", pak_path.replace("\\", "/"))
    return r[:-7] if r.lower().endswith(".uasset") else r

def game_rel_for_skin(skin_id, tex_rel):
    """Storage-relative path for a skin asset: Characters/{cid}/{skin_id}/{tex_rel}"""
    cid = char_id(skin_id)
    return f"Characters/{cid}/{skin_id}/{tex_rel}"

def pak_game_path(game_rel):
    """Full content-mount path for game_rel (used for pak extraction and mod staging).
    Looks up which mount the asset came from so LQ-only assets get the right prefix."""
    pfx = get_content_prefix(game_rel)
    return pfx.rstrip("/") + "/" + game_rel

def skin_entries(skin_id):
    pfx = _skin_prefix(skin_id)
    return [(p, c) for p, c, _ in ensure_index() if p.lower().startswith(pfx)]

def filter_subpath(entries, skin_id, subpath):
    """Narrow entries to those whose skin-relative path starts with subpath."""
    if not subpath: return entries
    pfx = _skin_prefix(skin_id)
    sp  = subpath.lower().replace("\\", "/").strip("/")
    if sp.endswith("/*"): sp = sp[:-2].strip("/")
    elif sp.endswith("*"): sp = sp[:-1]
    full = (pfx + sp + "/") if sp else pfx
    return [(p, c) for p, c in entries if p.lower().startswith(full)]
