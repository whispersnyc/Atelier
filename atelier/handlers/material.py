import os, json, shutil
from atelier.config import ASSETS, IMPORT_ROOT, WORK_IMPORT_ROOT, PAKS, USMAP, _CACHE
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

def mat_json(game_rel):
    """Extract the MI + convert to JSON (cached at IMPORT_ROOT/<game_rel>.json). Returns the json path."""
    from atelier.handlers.texture import extract_output_base, find_extracted
    import_base = os.path.join(IMPORT_ROOT,      *game_rel.split("/"))
    work_base   = os.path.join(WORK_IMPORT_ROOT, *game_rel.split("/"))
    jp = import_base + ".json"
    if os.path.exists(jp): return jp
    if not os.path.exists(work_base + ".uasset"):
        pak_gr   = pak_game_path(game_rel)
        uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS),
             "--filter", os.path.basename(pak_gr)])
        src_base = extract_output_base(game_rel)
        if not src_base or not os.path.exists(src_base + ".uasset"):
            src_base = find_extracted(game_rel)
        if src_base:
            os.makedirs(os.path.dirname(work_base), exist_ok=True)
            for ext in (".uasset", ".uexp", ".ubulk"):
                src = src_base + ext
                if os.path.exists(src):
                    shutil.move(src, work_base + ext)
    if not os.path.exists(work_base + ".uasset"):
        raise RuntimeError("material not found in game paks")
    os.makedirs(os.path.dirname(import_base), exist_ok=True)
    uat(["to_json", os.path.abspath(work_base + ".uasset"), USMAP,
         os.path.abspath(os.path.dirname(import_base))])
    if not os.path.exists(jp): raise RuntimeError("to_json produced no JSON")
    return jp

def read_material(game_rel):
    """{colors:[{name,rgba}], scalars:[{name,value}]} for an MI material instance."""
    colors, scalars = _mat_params(json.load(open(mat_json(game_rel), encoding="utf-8-sig")))
    return {"colors": colors, "scalars": scalars}

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
    jp = os.path.join(IMPORT_ROOT, *game_rel.split("/")) + ".json"
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
