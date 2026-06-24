import os, sys, glob, re, json

ROOT = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _load_config():
    try: return json.load(open(os.path.join(ROOT, "mr_config.json"), encoding="utf-8"))
    except Exception: return {}

def _detect_paks():
    cands = [r"C:/Program Files (x86)/Steam/steamapps/common/MarvelRivals/MarvelGame/Marvel/Content/Paks"]
    for vdf in (r"C:/Program Files (x86)/Steam/steamapps/libraryfolders.vdf",
                r"C:/Program Files/Steam/steamapps/libraryfolders.vdf"):
        try:
            for m in re.finditer(r'"path"\s*"([^"]+)"',
                                 open(vdf, encoding="utf-8", errors="ignore").read()):
                lib = m.group(1).replace("\\\\", "/").replace("\\", "/")
                cands.append(lib + "/steamapps/common/MarvelRivals/MarvelGame/Marvel/Content/Paks")
        except Exception: pass
    for c in cands:
        if os.path.isdir(c) and glob.glob(c + "/pakchunk*.utoc"): return c
    return cands[0]

_cfg  = _load_config()
TOOLS = _cfg.get("tools") or os.path.join(ROOT, "Tools")
PAKS  = (_cfg.get("paks") or _detect_paks()).replace("\\", "/")
os.environ["MR_TOOLS"] = TOOLS  # must be set before io_lib is imported anywhere

_usmaps = sorted(glob.glob(os.path.join(TOOLS, "Mappings", "*.usmap")))
USMAP   = next((u for u in _usmaps if "_latest" not in os.path.basename(u).lower()),
               _usmaps[0] if _usmaps else "")
CNW     = 0x08000000 if os.name == "nt" else 0

ASSETS      = os.path.join(ROOT, "assets")
ASSETS_MODS = os.path.join(ROOT, "assets", "mods")
_WORK       = os.path.join(ROOT, "_work")
GUI_DIR     = os.path.join(getattr(sys, "_MEIPASS", ROOT), "gui")

def check_prereqs(need_tool=True):
    issues = []
    if not glob.glob(PAKS + "/pakchunk*.utoc"):
        issues.append(f"No pak files found at: {PAKS}")
    tool_path = os.path.join(TOOLS, "UAssetTool.exe")
    if need_tool and not os.path.exists(tool_path):
        issues.append(f"UAssetTool not found at: {tool_path}")
    if issues:
        raise RuntimeError("\n".join(issues))
