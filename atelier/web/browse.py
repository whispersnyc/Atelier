import os, re, hashlib, threading, urllib.request
from atelier.config import ROOT, IMPORT_ROOT
from atelier.index import ensure_index
from atelier.paths import (skin_entries, skin_rel, game_rel_for_skin,
                           char_id as get_char_id, PAK_GAME_PREFIX)

_REMOTE_MD_URL   = "https://raw.githubusercontent.com/donutman07/MarvelRivalsCharacterIDs/refs/heads/main/MarvelRivalsCharacterIDs.md"
_update_callback = None   # set by routes.py to _push_sse after it's defined
_fetch_attempted = False
_fetch_lock      = threading.Lock()

# Paths (relative to Marvel/Content/Marvel/) whose immediate children are hero char IDs
# and grandchildren are skin IDs (both get ID→name labels).
# Add additional paths here if other areas of the pak use the same numbering scheme.
HERO_PATHS = ["Characters"]

def _parse_char_md_text(text):
    """Parse MD table text -> {char_id: {name, skins:{skin_id:name}}}"""
    chars = {}
    cur   = None
    for line in text.splitlines():
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
    return chars

def _parse_char_md():
    path = os.path.join(ROOT, "Tools", "MarvelRivalsCharacterIDs.md")
    try:
        return _parse_char_md_text(open(path, encoding="utf-8").read())
    except Exception:
        return {}

def _fetch_char_data():
    global _CHAR_DATA
    try:
        req = urllib.request.Request(
            _REMOTE_MD_URL,
            headers={"User-Agent": "Atelier-ModTool/1.0 (character-id-sync)"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode("utf-8")
    except Exception:
        return
    new_data = _parse_char_md_text(text)
    added = 0
    for cid, info in new_data.items():
        if cid not in _CHAR_DATA:
            _CHAR_DATA[cid] = info
            added += 1 + len(info["skins"])
        else:
            for sid, sname in info["skins"].items():
                if sid not in _CHAR_DATA[cid]["skins"]:
                    _CHAR_DATA[cid]["skins"][sid] = sname
                    added += 1
    if added:
        # write updated file back so next launch starts with fresh data
        try:
            path = os.path.join(ROOT, "Tools", "MarvelRivalsCharacterIDs.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
        if _update_callback:
            _update_callback({"toast": f"Character IDs updated — {added} new entries added", "toast_type": "success"})

def _try_fetch_once():
    global _fetch_attempted
    with _fetch_lock:
        if _fetch_attempted:
            return
        _fetch_attempted = True
    threading.Thread(target=_fetch_char_data, daemon=True).start()

_CHAR_DATA = _parse_char_md()

def char_name(cid):
    name = _CHAR_DATA.get(cid, {}).get("name")
    if not name:
        _try_fetch_once()
    return name or f"Character {cid}"

def skin_name(sid):
    cid  = get_char_id(sid)
    name = _CHAR_DATA.get(cid, {}).get("skins", {}).get(sid)
    if not name:
        _try_fetch_once()
    return name or sid

def token(game_rel):
    return hashlib.md5(game_rel.encode()).hexdigest()[:20]

def game_rel_from_token(tok):
    """Reverse-lookup game_rel from a token by scanning IMPORT_ROOT."""
    for root, _, files in os.walk(IMPORT_ROOT):
        for f in files:
            if not f.endswith(".png"): continue
            gr = os.path.relpath(os.path.join(root, f[:-4]), IMPORT_ROOT).replace("\\", "/")
            if token(gr) == tok:
                return gr
    return None

def _classify_file(name):
    nl = name.lower()
    if nl.startswith("t_"):
        return "texture"
    if nl.startswith(("ns_", "fx_", "vfx_", "nfx_", "p_", "niagara_")):
        return "vfx"
    if nl.startswith("mi_"):
        return "material"
    return "other"

def _label_folder(rel_path, folder_name):
    """Return display label for folder_name found at rel_path under Marvel/Content/Marvel/."""
    if rel_path in HERO_PATHS and re.match(r"^\d{4}$", folder_name):
        return f"{folder_name} — {char_name(folder_name)}"
    for hp in HERO_PATHS:
        m = re.match(rf"^{re.escape(hp)}/(\d{{4}})$", rel_path, re.IGNORECASE)
        if m and re.match(r"^\d{7}$", folder_name):
            sname  = skin_name(folder_name)
            suffix = folder_name[-3:]
            return f"{suffix} — Skin {suffix}" if sname == folder_name else f"{suffix} — {sname}"
    return folder_name

def _browse_pak_level(rel_path):
    """List immediate folder children at rel_path (relative to Marvel/Content/Marvel/) from pak index."""
    rel_path   = rel_path.strip("/")
    search_pfx = ((PAK_GAME_PREFIX + "/" + rel_path + "/") if rel_path
                  else (PAK_GAME_PREFIX + "/")).lower()

    folders = {}  # lower_name -> original_name (first seen)
    for pak_path_str, _ in ensure_index():
        clean = re.sub(r"^(\.\./)+", "", pak_path_str.replace("\\", "/"))
        cl    = clean.lower()
        idx   = cl.find(search_pfx)
        if idx < 0:
            continue
        rest = clean[idx + len(search_pfx):]
        if not rest or "/" not in rest:
            continue
        fname_orig  = rest.split("/")[0]
        fname_lower = fname_orig.lower()
        if fname_lower not in folders:
            folders[fname_lower] = fname_orig

    result = []
    for fname_lower in sorted(folders):
        fname = folders[fname_lower]
        label = _label_folder(rel_path, fname)
        child = f"{rel_path}/{fname}" if rel_path else fname
        result.append({"type": "folder", "name": fname, "label": label, "rel_path": child})
    return result

def _browse_skin(skin_id, subpath):
    """Browse immediate children of subpath inside skin_id (unchanged traversal logic)."""
    entries = skin_entries(skin_id)
    subpath = subpath.strip("/")
    prefix  = (subpath + "/") if subpath else ""

    folders = {}
    files   = {}

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
            files[rest] = {"rel_path": (prefix + rest).strip("/"), "game_rel": gr}

    result = []
    for name in sorted(folders, key=str.lower):
        result.append({"type": "folder", "name": name, "label": name, "rel_path": folders[name]})
    for name in sorted(files, key=str.lower):
        td     = files[name]
        base   = os.path.join(IMPORT_ROOT, *td["game_rel"].split("/"))
        ft     = _classify_file(name)
        is_mat = ft == "material"
        imported = os.path.exists(base + (".json" if is_mat else ".png"))
        tok      = token(td["game_rel"]) if imported else None
        result.append({
            "type":      "asset",
            "file_type": ft,
            "name":      name,
            "label":     name,
            "rel_path":  td["rel_path"],
            "game_rel":  td["game_rel"],
            "imported":  imported,
            "token":     tok,
        })
    return result

def browse_dispatch(path):
    """Unified browse entry point. path is relative to Marvel/Content/Marvel/."""
    path = (path or "").strip("/")
    for hp in HERO_PATHS:
        m = re.match(rf"^{re.escape(hp)}/(\d{{4}})/(\d{{7}})(?:/(.*))?$", path, re.IGNORECASE)
        if m:
            cid      = m.group(1)
            skin_id  = m.group(2)
            subpath  = (m.group(3) or "").strip("/")
            items    = _browse_skin(skin_id, subpath)
            skin_pfx = f"{hp}/{cid}/{skin_id}"
            for item in items:
                if item["type"] == "folder":
                    item["rel_path"] = f"{skin_pfx}/{item['rel_path']}"
            return items
    return _browse_pak_level(path)

# ── kept for backwards compat (CLI / any callers) ────────────────────────────

def all_char_ids():
    seen = set()
    for p, _ in ensure_index():
        m = re.search(r"/Characters/(\d{4})/", p)
        if m: seen.add(m.group(1))
    return sorted(seen)

def char_skin_ids(cid):
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

def all_imported():
    """Walk IMPORT_ROOT and return all imported assets."""
    items = []
    if not os.path.isdir(IMPORT_ROOT):
        return items
    for dirpath, _, files in os.walk(IMPORT_ROOT):
        for fname in sorted(files):
            rel = os.path.relpath(os.path.join(dirpath, fname), IMPORT_ROOT).replace("\\", "/")
            m   = re.match(r"^Characters/(\d{4})/(\d{7})/", rel)
            cid = m.group(1) if m else None
            sid = m.group(2) if m else None
            if fname.endswith(".png"):
                gr = rel[:-4]
                items.append({
                    "token": token(gr), "game_rel": gr, "name": fname[:-4],
                    "file_type": "texture",
                    "skin_id":   sid or "", "char_id": cid or "",
                    "char_name": char_name(cid) if cid else "",
                    "skin_name": skin_name(sid) if sid else "",
                    "mtime": int(os.path.getmtime(os.path.join(dirpath, fname))),
                })
            elif fname.endswith(".json") and _classify_file(fname[:-5]) == "material":
                gr = rel[:-5]
                items.append({
                    "token": token(gr), "game_rel": gr, "name": fname[:-5],
                    "file_type": "material",
                    "skin_id":   sid or "", "char_id": cid or "",
                    "char_name": char_name(cid) if cid else "",
                    "skin_name": skin_name(sid) if sid else "",
                    "mtime": int(os.path.getmtime(os.path.join(dirpath, fname))),
                })
    return items
