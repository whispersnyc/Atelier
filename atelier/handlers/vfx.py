import os, json, shutil
from atelier.config import ASSETS, IMPORT_ROOT, PAKS, USMAP, _WORK
from atelier.tools import uat
from atelier.paths import pak_game_path

# VFX = Niagara systems / data-interface assets. Editable content is NOT single scalar/color
# values (that's materials) — it's per-export CURVES baked into a flat LUT:
#   channels 1 -> scalar curve (size/alpha/intensity over life)
#   channels 2 -> vector2 curve (UV scroll / 2D motion)
#   channels 3 -> vector3 curve (RGB or 3D vector over life)
#   channels 4 -> color curve (RGBA; classified color / emission(HDR) / opacity(grayscale))
# UAssetTool reads these with `niagara_details` and edits them with
#   `niagara_edit ... --edits '[{"exportIndex":N,"flatLut":[...]}]'`  (flatLut length must match lut_floats).

PREVIEW_STOPS = 16   # gradient stops returned for the UI (full LUT stays on disk for editing)

def is_vfx(path_or_name):
    nl = os.path.basename(path_or_name).lower()
    return nl.startswith(("ns_", "fx_", "vfx_", "nfx_", "p_", "niagara_"))

def _ensure_extracted(game_rel):
    base = os.path.join(IMPORT_ROOT, *game_rel.split("/"))
    if not os.path.exists(base + ".uasset"):
        pak_gr   = pak_game_path(game_rel)
        pak_base = os.path.join(ASSETS, *pak_gr.split("/"))
        uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS), "--filter", os.path.basename(pak_gr)])
        os.makedirs(os.path.dirname(base), exist_ok=True)
        for ext in (".uasset", ".uexp", ".ubulk"):
            src = pak_base + ext
            if os.path.exists(src):
                shutil.move(src, base + ext)
    if not os.path.exists(base + ".uasset"):
        raise RuntimeError("VFX asset not found in game paks")
    return base

def _classify(channels, samples):
    """-> (kind, editable). kind: color|emission|opacity (4ch) | scalar (1) | vector2 (2) | vector3 (3)."""
    if channels < 4:
        return ({1: "scalar", 2: "vector2", 3: "vector3"}.get(channels, "scalar"), True)
    if not samples:
        return ("color", True)
    n = len(samples)
    sr = sg = sb = 0.0; mx = 0.0; all_zero = True
    for s in samples:
        r, g, b = (s + [0, 0, 0])[:3]
        sr += r; sg += g; sb += b
        mx = max(mx, r, g, b)
        if r or g or b: all_zero = False
    ar, ag, ab = sr / n, sg / n, sb / n
    gray = abs(ar - ag) < 0.02 and abs(ag - ab) < 0.02
    hdr  = mx > 1.05
    if all_zero or (gray and not hdr): return ("opacity", False)   # alpha/grayscale ramp — not a recolor target
    if hdr and not gray:               return ("emission", True)   # HDR glow
    return ("color", True)

def _downsample(samples, stops):
    if len(samples) <= stops: return samples
    step = (len(samples) - 1) / (stops - 1)
    return [samples[round(i * step)] for i in range(stops)]

def read_vfx(game_rel):
    """Enumerate every editable curve in a Niagara asset, classified by type.
    Returns {ok, name, total_exports, color_exports, summary, params:[...]}."""
    base = _ensure_extracted(game_rel)
    r = uat(["niagara_details", os.path.abspath(base + ".uasset"), "--usmap", USMAP])
    try:
        d = json.loads(r.stdout)
    except Exception:
        raise RuntimeError("niagara_details failed: " + (((r.stderr or "") + (r.stdout or "")).strip()[-200:] or "no output"))

    params, summary = [], {}
    for e in d.get("exports", []):
        lut      = e.get("shaderLut") or {}
        samples  = lut.get("samples") or []
        channels = e.get("channels", 1)
        kind, editable = _classify(channels, samples)
        summary[kind] = summary.get(kind, 0) + 1

        avg = [0.0, 0.0, 0.0, 1.0]
        if samples:
            for c in range(min(channels, 4)):
                avg[c] = sum((s + [0, 0, 0, 0])[c] for s in samples) / len(samples)
        is_hdr = max((max(s[:3]) for s in samples if s), default=0.0) > 1.05

        params.append({
            "export_index": e["exportIndex"],
            "class":        e["classType"],
            "channels":     channels,
            "kind":         kind,
            "editable":     editable,
            "lut_floats":   lut.get("floatCount", 0),     # flatLut length required for niagara_edit
            "sample_count": lut.get("sampleCount", 0),
            "min_time":     lut.get("minTime", 0),
            "max_time":     lut.get("maxTime", 0),
            "is_hdr":       is_hdr,
            "avg":          [round(x, 5) for x in avg],
            "stops":        [[round(x, 5) for x in s] for s in _downsample(samples, PREVIEW_STOPS)],
        })

    return {
        "ok":            True,
        "name":          os.path.basename(game_rel),
        "total_exports": d.get("totalExports"),
        "color_exports": d.get("colorExports"),
        "summary":       summary,
        "params":        params,
    }

def stage_vfx(stage, game_rel, edits):
    """Apply curve edits and write the modified Niagara asset into the export stage.
    edits: [{export_index, flat_lut:[...]}] — flat_lut length must equal that curve's lut_floats.
    (Export wiring is still WIP; this is the building block niagara_edit provides.)"""
    base = _ensure_extracted(game_rel)
    payload = [{"exportIndex": ed["export_index"], "flatLut": ed["flat_lut"]} for ed in (edits or [])]
    if not payload:
        raise RuntimeError("no curve edits supplied")
    pak_gr = pak_game_path(game_rel)
    out_ua = os.path.join(stage, *pak_gr.split("/")) + ".uasset"
    os.makedirs(os.path.dirname(out_ua), exist_ok=True)
    ej = os.path.join(_WORK, "_vfx_edit.json"); json.dump(payload, open(ej, "w"))
    uat(["niagara_edit", os.path.abspath(base + ".uasset"), "--usmap", USMAP,
         "--output", os.path.abspath(out_ua), "--edits-file", os.path.abspath(ej)])
    if not os.path.exists(out_ua):
        raise RuntimeError("niagara_edit produced no uasset")
    return os.path.basename(game_rel)
