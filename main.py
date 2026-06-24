#!/usr/bin/env python3
"""Atelier CLI — Marvel Rivals asset tool.

Usage:
  python main.py list   <skin_id>[/subpath]                    List assets in paks (recursive)
  python main.py import <skin_id>[/subpath[/*]]                Extract to assets/Marvel/Content/...
  python main.py export <mod_name> <skin_id/tex_path> [...]    Pack from assets/ to assets/mods/
                        [--dir <output_dir>] [--override]

skin_id is the 7-digit ID (e.g. 1029304); character ID (1029) is derived automatically.
Import recreates the full game path under assets/ so meshes, VFX, UI etc. all coexist.
Export packs to <mod_name>_9999999_P.{pak,ucas,utoc}. If those files exist you will be
prompted to confirm overwrite; pass --override to skip the prompt.

Examples:
  python main.py list   1029304
  python main.py list   1029304/Textures
  python main.py import 1029304
  python main.py import 1029304/Textures/*
  python main.py export MagikWeapon 1029304/Texture/T_1029304_Body_D
  python main.py export MagikWeapon "1029304/Textures/*"
  python main.py export MagikWeapon "1029304/Textures/*" --dir D:/Mods --override
"""
import os, sys

ROOT = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from atelier.config import ASSETS_MODS
from atelier.handlers.texture import cmd_list, cmd_import, cmd_export

def main():
    if len(sys.argv) < 3:
        print(__doc__.strip()); sys.exit(1)
    cmd  = sys.argv[1].lower()
    rest = sys.argv[2:]

    try:
        if cmd == "list":
            cmd_list(rest[0])
        elif cmd == "import":
            cmd_import(rest[0])
        elif cmd == "export":
            out_dir    = ASSETS_MODS
            force      = False
            positional = []
            i = 0
            while i < len(rest):
                if rest[i] == "--dir" and i + 1 < len(rest):
                    out_dir = rest[i + 1]; i += 2
                elif rest[i] == "--override":
                    force = True; i += 1
                else:
                    positional.append(rest[i]); i += 1
            if len(positional) < 2:
                print("export requires: <mod_name> <tex_path> [...]\n")
                print(__doc__.strip()); sys.exit(1)
            cmd_export(positional[0], positional[1:], out_dir, force)
        else:
            print(f"Unknown command: {cmd!r}\n"); print(__doc__.strip()); sys.exit(1)
    except (RuntimeError, OSError) as e:
        print(f"[error] {e}", file=sys.stderr); sys.exit(1)

if __name__ == "__main__":
    main()
