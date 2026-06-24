import os, re, hashlib
from atelier.config import ROOT, ASSETS
from atelier.index import ensure_index
from atelier.paths import skin_entries, skin_rel, game_rel_for_skin, char_id as get_char_id

def _parse_char_md():
    """Parse MarvelRivalsCharacterIDs.md -> {char_id: {name, skins:{skin_id:name}}}"""
    path  = os.path.join(ROOT, "Tools", "MarvelRivalsCharacterIDs.md")
    chars = {}
    cur   = None
    try:
        for line in open(path, encoding="utf-8"):
            m = re.match(r'\|\s*(\d{4})\s*\|\s*([^|]+?)\s*\|(?:\s*(\d{7})\s*\|\s*([^|]*?)\s*\|)?', line)
            if m and m.group(1):
                cur  = m.group(1)
                name = m.group(2).strip()
                if name and name.upper() != "NAME":
                    chars.setdefault(cur, {"name": name, "skins": {}})
                    if m.group(3):
                        chars[cur]["skins"][m.group(3)] = (m.group(4) or "").strip()
                continue
            m2 = re.match(r'\|\s*\|\s*\|\s*(\d{7})\s*\|\s*([^|]*?)\s*\|', line)
            if m2 and cur and cur in chars:
                chars[cur]["skins"][m2.group(1)] = m2.group(2).strip()
    except Exception:
        pass
    return chars

_CHAR_DATA = _parse_char_md()

def char_name(cid):
    return _CHAR_DATA.get(cid, {}).get("name") or f"Character {cid}"

def skin_name(sid):
    cid = get_char_id(sid)
    return _CHAR_DATA.get(cid, {}).get("skins", {}).get(sid) or sid

def all_char_ids():
    """Sorted list of char_ids present in the pak index."""
    seen = set()
    for p, _ in ensure_index():
        m = re.search(r"/Characters/(\d{4})/", p)
        if m: seen.add(m.group(1))
    return sorted(seen)

def char_skin_ids(cid):
    """Sorted list of skin_ids for a character present in the pak index."""
    seen   = set()
    needle = f"/Characters/{cid}/".lower()
    for p, _ in ensure_index():
        pl = p.lower()
        i  = pl.find(needle)
        if i < 0: continue
        rest = pl[i + len(needle):]
        sid  = rest.split("/")[0]
        if re.match(r"^\d{7}$", sid): seen.add(sid)
    return sorted(seen)

def token(game_rel):
    return hashlib.md5(game_rel.encode()).hexdigest()[:20]

def game_rel_from_token(tok):
    """Reverse-lookup game_rel from a token by scanning the assets PNG tree."""
    for root, _, files in os.walk(ASSETS):
        for f in files:
            if not f.endswith(".png"): continue
            gr = os.path.relpath(os.path.join(root, f[:-4]), ASSETS).replace("\\", "/")
            if token(gr) == tok:
                return gr
    return None

def browse(skin_id, subpath=""):
    """Return immediate children of `subpath` inside `skin_id`."""
    entries = skin_entries(skin_id)
    subpath = subpath.strip("/")
    prefix  = (subpath + "/") if subpath else ""

    folders  = {}
    textures = {}

    for pak_path, _cont in entries:
        rel = skin_rel(pak_path, skin_id)
        if not rel.lower().startswith(prefix.lower()):
            continue
        rest = rel[len(prefix):]
        if not rest:
            continue
        if "/" in rest:
            folder_name = rest.split("/")[0]
            folder_path = (prefix + folder_name).strip("/")
            folders[folder_name] = folder_path
        else:
            gr = game_rel_for_skin(skin_id, (prefix + rest).strip("/"))
            textures[rest] = {"rel_path": (prefix + rest).strip("/"), "game_rel": gr}

    result = []
    for name in sorted(folders, key=str.lower):
        result.append({"type": "folder", "name": name, "rel_path": folders[name]})
    for name in sorted(textures, key=str.lower):
        td       = textures[name]
        base     = os.path.join(ASSETS, *td["game_rel"].split("/"))
        imported = os.path.exists(base + ".png")
        tok      = token(td["game_rel"]) if imported else None
        result.append({
            "type":     "texture",
            "name":     name,
            "rel_path": td["rel_path"],
            "game_rel": td["game_rel"],
            "imported": imported,
            "token":    tok,
        })
    return result

def all_imported():
    """Walk assets/ and return every imported texture that has a .png."""
    items      = []
    chars_root = os.path.join(ASSETS, "Marvel", "Content", "Marvel", "Characters")
    if not os.path.isdir(chars_root):
        return items
    for cid in sorted(os.listdir(chars_root)):
        char_dir = os.path.join(chars_root, cid)
        if not os.path.isdir(char_dir): continue
        for sid in sorted(os.listdir(char_dir)):
            skin_dir = os.path.join(char_dir, sid)
            if not os.path.isdir(skin_dir): continue
            for dirpath, _, files in os.walk(skin_dir):
                for fname in sorted(files):
                    if not fname.endswith(".png"): continue
                    tex_name = fname[:-4]
                    abs_png  = os.path.join(dirpath, fname)
                    gr       = os.path.relpath(abs_png[:-4], ASSETS).replace("\\", "/")
                    items.append({
                        "token":     token(gr),
                        "game_rel":  gr,
                        "name":      tex_name,
                        "skin_id":   sid,
                        "char_id":   cid,
                        "char_name": char_name(cid),
                        "skin_name": skin_name(sid),
                        "mtime":     int(os.path.getmtime(abs_png)),
                    })
    return items
