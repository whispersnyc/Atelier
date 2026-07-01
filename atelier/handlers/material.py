import os, json, shutil
from atelier.config import WORK_IMPORT_ROOT, PAKS, USMAP, _CACHE, CACHE_3DVIEW, get_import_root
from atelier.tools import uat
from atelier.paths import pak_game_path

def is_material(path_or_name):
    return os.path.basename(path_or_name).upper().startswith("MI_")

def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0

def _gn(lst, n):
    for p in lst or []:
        if isinstance(p, dict) and p.get("Name") == n: return p
    return None

def _ex_props(e): return e.get("Data") or e.get("Value") or []

def _mat_pname(entry):
    pinfo = _gn(entry["Value"], "ParameterInfo")
    return (_gn(pinfo["Value"], "Name") or {}).get("Value") if pinfo else None

def _mat_color(entry):
    pv = _gn(entry["Value"], "ParameterValue"); v = pv.get("Value") if pv else None
    if (isinstance(v, list) and v and isinstance(v[0], dict)
            and isinstance(v[0].get("Value"), dict) and "R" in v[0]["Value"]):
        return v[0]["Value"]
    return None

def _mat_params(d):
    ex = d["Exports"][0]
    vp = _gn(_ex_props(ex), "VectorParameterValues")
    sp = _gn(_ex_props(ex), "ScalarParameterValues")
    colors, scalars = [], []
    for e in (vp or {}).get("Value", []):
        nm = _mat_pname(e); lc = _mat_color(e)
        if nm and lc: colors.append({"name": nm, "rgba": [round(_f(lc[k]), 5) for k in "RGBA"]})
    for e in (sp or {}).get("Value", []):
        nm = _mat_pname(e); pv = _gn(e["Value"], "ParameterValue")
        if nm and pv is not None and not isinstance(pv.get("Value"), (list, dict)):
            scalars.append({"name": nm, "value": round(_f(pv.get("Value")), 5)})
    return colors, scalars

def _mat_textures(d):
    """{slot: game_rel} for the MI's texture params (BaseColor/Normal/ORM/…), resolved through the
    Imports table. Each TextureParameterValue's ParameterValue is an import link (negative index);
    that import's outer Package holds the /Game/Marvel/… path → storage-relative game_rel."""
    ex = d["Exports"][0]
    tp = _gn(_ex_props(ex), "TextureParameterValues")
    imports = d.get("Imports", [])
    out = {}
    for e in (tp or {}).get("Value", []):
        nm = _mat_pname(e)
        pv = _gn(e["Value"], "ParameterValue")
        idx = pv.get("Value") if pv else None
        if not nm or not isinstance(idx, int) or idx >= 0:
            continue
        ii = -idx - 1
        if not (0 <= ii < len(imports)):
            continue
        outer = imports[ii].get("OuterIndex")
        pkg = None
        if isinstance(outer, int) and outer < 0 and (-outer - 1) < len(imports):
            pkg = imports[-outer - 1].get("ObjectName")
        if isinstance(pkg, str) and pkg.startswith("/Game/Marvel/"):
            out[nm] = pkg[len("/Game/Marvel/"):]
    return out

def _apply_mat_edits(d, colors, scalars):
    ex = d["Exports"][0]
    vp = _gn(_ex_props(ex), "VectorParameterValues")
    sp = _gn(_ex_props(ex), "ScalarParameterValues")
    for e in (vp or {}).get("Value", []):
        nm = _mat_pname(e)
        if nm in colors:
            lc = _mat_color(e)
            if lc:
                r, g, b, a = colors[nm]
                lc["R"], lc["G"], lc["B"], lc["A"] = float(r), float(g), float(b), float(a)
    for e in (sp or {}).get("Value", []):
        nm = _mat_pname(e)
        if nm in scalars:
            pv = _gn(e["Value"], "ParameterValue")
            if pv is not None: pv["Value"] = float(scalars[nm])

def mat_json(game_rel, out_dir=None):
    """Extract the MI + convert to JSON as <basename>.json. Returns the json path.
    The active project dir always takes priority (edits/explicit imports live there); if no
    project copy exists, the JSON is produced in out_dir (defaults to the project dir — the
    explicit import/edit flows). Pass out_dir=CACHE_3DVIEW for viewport-only reads that shouldn't
    pollute the project folder."""
    import atelier.asset_cache as _ac
    from atelier.handlers.texture import extract_info, find_extracted
    import_root = get_import_root()
    project_jp  = os.path.join(import_root, os.path.basename(game_rel)) + ".json"
    if os.path.exists(project_jp): return project_jp
    out_dir = out_dir or import_root
    jp = os.path.join(out_dir, os.path.basename(game_rel)) + ".json"
    if os.path.exists(jp): return jp
    if out_dir != CACHE_3DVIEW:
        # reuse the vanilla copy the viewport already cached instead of re-extracting from paks
        cached_jp = os.path.join(CACHE_3DVIEW, os.path.basename(game_rel)) + ".json"
        if os.path.exists(cached_jp):
            os.makedirs(out_dir, exist_ok=True)
            shutil.copyfile(cached_jp, jp)
            return jp
    work_base = _ac.cache_base(game_rel)
    if not work_base or not os.path.exists(work_base + ".uasset"):
        pak_gr = pak_game_path(game_rel)
        os.makedirs(WORK_IMPORT_ROOT, exist_ok=True)
        uat(["extract_iostore_legacy", PAKS, os.path.abspath(WORK_IMPORT_ROOT),
             "--filter", os.path.basename(pak_gr)])
        cp, pak, pfx = extract_info(game_rel)
        if cp and os.path.exists(cp + ".uasset"):
            _ac.record(game_rel, cp, pak, pfx)
            work_base = cp
        else:
            work_base = find_extracted(game_rel)
    if not work_base or not os.path.exists(work_base + ".uasset"):
        raise RuntimeError("material not found in game paks")
    os.makedirs(out_dir, exist_ok=True)
    uat(["to_json", os.path.abspath(work_base + ".uasset"), USMAP, os.path.abspath(out_dir)])
    if not os.path.exists(jp): raise RuntimeError("to_json produced no JSON")
    return jp

def read_material(game_rel, cache_only=False):
    """{colors:[{name,rgba}], scalars:[{name,value}], textures:{slot:game_rel}} for an MI instance.
    cache_only=True routes any freshly-extracted JSON into CACHE_3DVIEW instead of the project
    folder — used by the 3D viewport's automatic per-mesh material reads."""
    d = json.load(open(mat_json(game_rel, out_dir=CACHE_3DVIEW if cache_only else None), encoding="utf-8-sig"))
    colors, scalars = _mat_params(d)
    return {"colors": colors, "scalars": scalars, "textures": _mat_textures(d)}

def save_material(game_rel, colors, scalars):
    """Apply color/scalar edits and PERSIST them into the material's on-disk JSON."""
    jp = mat_json(game_rel)
    d  = json.load(open(jp, encoding="utf-8-sig"))
    _apply_mat_edits(d, colors or {}, scalars or {})
    json.dump(d, open(jp, "w"))
    cols, scals = _mat_params(d)
    return {"colors": cols, "scalars": scals}

def reset_material(game_rel):
    """Drop local edits: delete the cached JSON and re-derive vanilla params from the .uasset."""
    jp = os.path.join(get_import_root(), os.path.basename(game_rel)) + ".json"
    if os.path.exists(jp): os.remove(jp)
    return read_material(game_rel)

def stage_material(stage, game_rel, colors, scalars):
    """Apply color/scalar edits to the MI and from_json it into the export stage at pak game path."""
    d = json.load(open(mat_json(game_rel), encoding="utf-8-sig"))
    _apply_mat_edits(d, colors or {}, scalars or {})
    ej = os.path.join(_CACHE, "_mat_edit.json"); json.dump(d, open(ej, "w"))
    pak_gr = pak_game_path(game_rel)
    out_ua = os.path.join(stage, *pak_gr.split("/")) + ".uasset"
    os.makedirs(os.path.dirname(out_ua), exist_ok=True)
    uat(["from_json", os.path.abspath(ej), os.path.abspath(out_ua), USMAP])
    if not os.path.exists(out_ua): raise RuntimeError("from_json produced no uasset")
    return os.path.basename(game_rel)
