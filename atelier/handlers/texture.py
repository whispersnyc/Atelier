import os, sys, glob, re, shutil
from atelier.config import ASSETS, IMPORT_ROOT, ASSETS_MODS, PAKS, USMAP, _WORK, check_prereqs
from atelier.tools import uat, uat_json
from atelier.paths import char_id, game_rel_for_skin, pak_game_path, skin_entries, filter_subpath, skin_rel

def decode_png(dst_base):
    """Decode one extracted UE texture (.uasset/.uexp/.ubulk) to .png via UAssetTool."""
    if not os.path.exists(dst_base + ".uasset"): return
    out_png = os.path.abspath(dst_base + ".png")
    r = uat(["extract_texture", os.path.abspath(dst_base + ".uasset"), out_png, "--usmap", USMAP])
    if not os.path.exists(out_png):
        print(f"  [warn] PNG decode failed for {os.path.basename(dst_base)}: "
              f"{((r.stderr or '') + (r.stdout or '')).strip()[-200:]}", file=sys.stderr)

def decode_batch(uasset_paths):
    """Parallel-decode many extracted .uasset textures to .png next to each."""
    paths = [os.path.abspath(p) for p in uasset_paths if os.path.exists(p)]
    if not paths: return {}
    return uat_json({"action": "batch_extract_texture_png", "file_paths": paths,
                     "output_path": os.path.abspath(IMPORT_ROOT), "base_path": os.path.abspath(IMPORT_ROOT),
                     "usmap_path": USMAP, "format": "png", "parallel": True})

def decode_thumb(uasset_path, thumb_path):
    """Decode the lowest available mip to a small thumbnail PNG (tries mip 4 → 3 → 2 → 0)."""
    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
    for mip in (4, 3, 2, 0):
        uat(["extract_texture", os.path.abspath(uasset_path), os.path.abspath(thumb_path),
             "--usmap", USMAP, "--mip", str(mip)])
        if os.path.exists(thumb_path):
            return True
    return False

def stage_inject(stage, game_rel):
    """Stage one texture: inject the edited PNG into the vanilla .uasset via UAssetTool.
    Staged file is placed at the pak game path so create_mod_iostore packs it correctly."""
    base = os.path.join(IMPORT_ROOT, *game_rel.split("/"))
    png  = base + ".png"
    if not os.path.exists(base + ".uasset"):
        raise RuntimeError("no base asset — run 'import' first")
    if not os.path.exists(png):
        decode_png(base)
        if not os.path.exists(png):
            raise RuntimeError("PNG missing and decode failed — re-import this texture")
    pak_gr = pak_game_path(game_rel)
    out_ua = os.path.join(stage, *pak_gr.split("/")) + ".uasset"
    os.makedirs(os.path.dirname(out_ua), exist_ok=True)
    r = uat(["inject_texture", os.path.abspath(base + ".uasset"), os.path.abspath(png),
             os.path.abspath(out_ua), "--usmap", USMAP])
    if not os.path.exists(out_ua):
        raise RuntimeError("inject failed: " + (((r.stderr or "") + (r.stdout or "")).strip()[-200:] or "unknown"))
    return os.path.basename(game_rel)

def build_mod(mod_name, tex_items, mat_items, out_dir, force=True):
    """Pack texture edits (inject) + material param edits (from_json) into one mod.
    tex_items: [game_rel]; mat_items: [{game_rel, colors:{name:[r,g,b,a]}, scalars:{name:val}}]."""
    from atelier.handlers.material import stage_material
    out_dir = os.path.abspath(out_dir); stem = f"{mod_name}_9999999_P"; base = os.path.join(out_dir, stem)
    for ext in (".pak", ".ucas", ".utoc"):
        if os.path.exists(base + ext): os.remove(base + ext)
    stage = os.path.join(_WORK, "build_stage", mod_name)
    shutil.rmtree(os.path.join(_WORK, "build_stage"), ignore_errors=True); os.makedirs(stage)
    applied, skipped = [], []
    for game_rel in tex_items:
        try: applied.append("tex " + stage_inject(stage, game_rel))
        except Exception as e: skipped.append(f"{os.path.basename(game_rel)}: {e}")
    for m in mat_items:
        try: applied.append("mat " + stage_material(stage, m["game_rel"],
                                                    m.get("colors", {}), m.get("scalars", {})))
        except Exception as e: skipped.append(f"{os.path.basename(m.get('game_rel',''))}: {e}")
    if not applied:
        return {"ok": False, "error": "nothing staged: " + "; ".join(skipped)}
    os.makedirs(out_dir, exist_ok=True)
    uat(["create_mod_iostore", os.path.abspath(base), os.path.abspath(stage), "--usmap", USMAP])
    if not os.path.exists(base + ".utoc"):
        return {"ok": False, "error": "create_mod_iostore failed"}
    return {"ok": True, "applied": applied, "skipped": skipped, "pak": base + ".pak"}

# ── CLI commands ───────────────────────────────────────────────────────────────

def cmd_list(arg):
    check_prereqs(need_tool=False)
    arg     = arg.replace("\\", "/")
    skin_id, _, subpath = arg.partition("/")
    entries = skin_entries(skin_id)
    if not entries:
        print(f"No entries found for skin {skin_id}"); return
    if subpath:
        entries = filter_subpath(entries, skin_id, subpath)
    if not entries:
        print(f"No entries matched under {arg!r}"); return
    seen = set()
    for p, _ in sorted(entries, key=lambda x: x[0].lower()):
        line = f"{skin_id}/{skin_rel(p, skin_id)}"
        if line not in seen:
            seen.add(line); print(line)

def cmd_import(arg):
    check_prereqs()
    arg     = arg.replace("\\", "/")
    skin_id, _, subpath = arg.partition("/")
    entries = skin_entries(skin_id)
    if not entries:
        print(f"No entries found for skin {skin_id}"); return
    if subpath:
        entries = filter_subpath(entries, skin_id, subpath)
    if not entries:
        print(f"No entries matched {arg!r}"); return

    cid       = char_id(skin_id)
    dest_root = os.path.abspath(os.path.join(IMPORT_ROOT, "Characters", cid, skin_id))
    print(f"  Destination: {dest_root}")

    names = sorted({os.path.basename(p)[:-7] for p, _ in entries})
    print(f"  Extracting {len(names)} asset(s) from game via UAssetTool...", file=sys.stderr)
    r = uat(["extract_iostore_legacy", PAKS, os.path.abspath(ASSETS), "--filter"] + names)
    if "Extraction complete" not in (r.stdout or ""):
        print(f"  [warn] extract: {((r.stderr or '') + (r.stdout or '')).strip()[-300:]}", file=sys.stderr)
    # Move extracted files from pak location to import location
    pak_skin_dir = os.path.join(ASSETS, "Marvel", "Content", "Marvel", "Characters", cid, skin_id)
    if os.path.isdir(pak_skin_dir) and pak_skin_dir != dest_root:
        os.makedirs(os.path.dirname(dest_root), exist_ok=True)
        shutil.copytree(pak_skin_dir, dest_root, dirs_exist_ok=True)
        shutil.rmtree(pak_skin_dir, ignore_errors=True)
    decode_batch(glob.glob(os.path.join(dest_root, "**", "*.uasset"), recursive=True))

    n_assets = len(glob.glob(os.path.join(dest_root, "**", "*.uasset"), recursive=True))
    n_png    = len(glob.glob(os.path.join(dest_root, "**", "*.png"), recursive=True))
    print(f"Extracted {n_assets} asset(s), decoded {n_png} PNG -> {dest_root}")

def _split_glob_prefix(prefix):
    if "/" in prefix:
        d, f = prefix.rsplit("/", 1)
        return d, f
    return "", prefix

def expand_export_args(args):
    """Resolve export args to [(game_rel_no_ext, display_label), ...], expanding wildcards."""
    results = []
    for arg in args:
        arg = arg.replace("\\", "/")
        if os.path.isabs(arg):
            try: arg = os.path.relpath(arg.replace("/", os.sep), IMPORT_ROOT).replace("\\", "/")
            except ValueError:
                print(f"  [warn] path not under assets/import/: {arg}", file=sys.stderr); continue
        noext = arg[:-7] if arg.lower().endswith(".uasset") else arg
        if re.match(r"^\d{7}(/|$)", noext):
            skin_id  = noext[:7]
            tex_part = noext[8:] if len(noext) > 8 else ""
            if not tex_part:
                print(f"  [warn] no texture path after skin_id in {arg!r}", file=sys.stderr); continue
            if "*" in tex_part:
                dir_part, file_prefix = _split_glob_prefix(tex_part.split("*")[0])
                cid      = char_id(skin_id)
                skin_dir = os.path.join(IMPORT_ROOT, "Characters", cid, skin_id)
                search_dir = os.path.join(skin_dir, *dir_part.split("/")) if dir_part else skin_dir
                if not os.path.isdir(search_dir):
                    print(f"  [warn] directory not found: {search_dir}", file=sys.stderr); continue
                for root_dir, _, files in os.walk(search_dir):
                    for fname in sorted(files):
                        if not fname.lower().endswith(".uasset"): continue
                        if file_prefix and not fname.lower().startswith(file_prefix.lower()): continue
                        r = os.path.relpath(os.path.join(root_dir, fname), skin_dir).replace("\\", "/")
                        r = r[:-7] if r.lower().endswith(".uasset") else r
                        results.append((game_rel_for_skin(skin_id, r), f"{skin_id}/{r}"))
            else:
                results.append((game_rel_for_skin(skin_id, tex_part), f"{skin_id}/{tex_part}"))
        else:
            if "*" in noext:
                dir_part, file_prefix = _split_glob_prefix(noext.split("*")[0])
                search_dir = os.path.join(IMPORT_ROOT, *dir_part.split("/")) if dir_part else IMPORT_ROOT
                if not os.path.isdir(search_dir):
                    print(f"  [warn] directory not found: {search_dir}", file=sys.stderr); continue
                for root_dir, _, files in os.walk(search_dir):
                    for fname in sorted(files):
                        if not fname.lower().endswith(".uasset"): continue
                        if file_prefix and not fname.lower().startswith(file_prefix.lower()): continue
                        r = os.path.relpath(os.path.join(root_dir, fname), IMPORT_ROOT).replace("\\", "/")
                        r = r[:-7] if r.lower().endswith(".uasset") else r
                        results.append((r, r))
            else:
                results.append((noext, noext))
    seen = set(); out = []
    for item in results:
        if item[0] not in seen: seen.add(item[0]); out.append(item)
    return out

def cmd_export(mod_name, tex_args, out_dir, force):
    check_prereqs()
    pairs = expand_export_args(tex_args)
    if not pairs:
        print("No files resolved for export"); return

    out_dir  = os.path.abspath(out_dir)
    stem     = f"{mod_name}_9999999_P"
    existing = [fp for ext in (".pak", ".ucas", ".utoc")
                for fp in (os.path.join(out_dir, stem + ext),) if os.path.exists(fp)]
    if existing and not force:
        print(f"Mod '{stem}' already exists in {out_dir}.")
        try:   ans = input("Overwrite? [y/N] ").strip().lower()
        except EOFError: ans = ""
        if ans != "y":
            print("Aborted."); return
    for fp in existing:
        os.remove(fp)

    stage = os.path.join(_WORK, "cli_export_stage", mod_name)
    shutil.rmtree(stage, ignore_errors=True); os.makedirs(stage)
    try:
        staged = 0; skipped = []
        for game_rel, label in pairs:
            try:
                desc = stage_inject(stage, game_rel)
                staged += 1
                print(f"  staged {label} -> {desc}")
            except Exception as e:
                skipped.append(f"{label}: {e}")
        if skipped:
            for s in skipped: print(f"  [warn] skipped: {s}", file=sys.stderr)
        if not staged:
            print("Nothing staged — check warnings above"); return

        os.makedirs(out_dir, exist_ok=True)
        base = os.path.join(out_dir, stem)
        r    = uat(["create_mod_iostore", os.path.abspath(base), os.path.abspath(stage),
                    "--usmap", USMAP])
        if not os.path.exists(base + ".utoc"):
            print(f"create_mod_iostore failed:\n{((r.stderr or '') + (r.stdout or '')).strip()[:500]}"); return

        if os.path.exists(base + ".utoc"):
            print(f"Packed {staged} texture(s) -> {os.path.abspath(base)}.{{pak,ucas,utoc}}")
        else:
            made = sorted(glob.glob(os.path.join(out_dir, "*_P.utoc")))
            if made:
                base = made[-1][:-5]
                print(f"Packed {staged} texture(s) -> {os.path.abspath(base)}.{{pak,ucas,utoc}}")
            else:
                print(f"retoc exit 0 but no .utoc found in {out_dir}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
