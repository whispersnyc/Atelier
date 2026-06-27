"""In-process thumbnail extraction from IoStore — no UAssetTool, no disk writes for .uasset."""

import os, io, struct, threading
from collections import defaultdict
from PIL import Image
from atelier.config import PAKS
import io_lib

_CONTENT_PREFIXES = (
    "Marvel/Content/Marvel/",
    "Marvel/Content/Marvel_LQ/",
)

_DXGI = {b"PF_DXT1": 71, b"PF_DXT5": 77, b"PF_BC5": 83, b"PF_BC7": 98}
_BPB  = {b"PF_DXT1": 8,  b"PF_DXT5": 16, b"PF_BC5": 16, b"PF_BC7": 16}
_BULK = b"\x00\x00\x00\x02"

_toc_cache: dict = {}
_toc_lock  = threading.Lock()

# lazy map: game_rel.lower() -> container_basename  (built from ensure_index on first use)
_gr_to_cont: dict = {}
_gr_map_ready = False
_gr_map_lock  = threading.Lock()


def _path_to_gr(pak_path: str) -> str | None:
    """Raw pak path (from parse_dir_index) -> virtual game_rel, or None."""
    pl = pak_path.replace("\\", "/")
    pl_lower = pl.lower()
    for pfx in _CONTENT_PREFIXES:
        i = pl_lower.find(pfx.lower())
        if i >= 0:
            rest = pl[i + len(pfx):]
            return rest[:-7] if rest.lower().endswith(".uasset") else None
    return None


def _ensure_gr_map():
    global _gr_map_ready
    if _gr_map_ready:
        return
    with _gr_map_lock:
        if _gr_map_ready:
            return
        from atelier.index import ensure_index
        for virt_path, cont, _pfx in ensure_index():
            gr = virt_path[:-7] if virt_path.lower().endswith(".uasset") else virt_path
            _gr_to_cont[gr.lower()] = cont
        _gr_map_ready = True


def _get_toc(cont_basename: str):
    with _toc_lock:
        if cont_basename in _toc_cache:
            return _toc_cache[cont_basename]
        utoc = os.path.join(PAKS, cont_basename)
        ucas = utoc[:-5] + ".ucas"
        if not os.path.exists(utoc):
            _toc_cache[cont_basename] = None
            return None
        t = io_lib.parse_toc(utoc)
        ents = io_lib.parse_dir_index(t)
        # game_rel -> main chunk index
        t._dir = {_path_to_gr(p).lower(): idx for p, idx in ents if _path_to_gr(p)}
        # path_hash -> {type_bytes: chunk_index}
        h2t: dict = defaultdict(dict)
        for i in range(t.entry_count):
            cid = t.chunk_ids[i]
            h2t[cid[:8]][cid[8:12]] = i
        t._h2t = h2t
        entry = (t, ucas)
        _toc_cache[cont_basename] = entry
        return entry


def _make_dds_dx10(w: int, h: int, dxgi_fmt: int, data: bytes) -> bytes:
    DDSD_CAPS = 0x1; DDSD_HEIGHT = 0x2; DDSD_WIDTH = 0x4
    DDSD_PIXELFORMAT = 0x1000; DDSD_LINEARSIZE = 0x80000
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
    hdr  = struct.pack("<7I", 124, flags, h, w, len(data), 0, 0)
    hdr += b"\x00" * 44
    hdr += struct.pack("<2I4s5I", 32, 4, b"DX10", 0, 0, 0, 0, 0)
    hdr += struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    hdr += struct.pack("<5I", dxgi_fmt, 3, 0, 1, 0)  # DX10 ext: format, dim=2D, misc, array, misc2
    return b"DDS " + hdr + data


def _warmup():
    """Pre-warm the gr→container map and all TOCs in parallel background threads."""
    try:
        _ensure_gr_map()
        conts = sorted({cont for cont in _gr_to_cont.values()})
        threads = [threading.Thread(target=_get_toc, args=(c,), daemon=True) for c in conts]
        for th in threads: th.start()
        for th in threads: th.join()
    except Exception:
        pass

def start_warmup():
    threading.Thread(target=_warmup, daemon=True, name="pak_thumb_warmup").start()


def decode_thumb_from_pak(game_rel: str) -> bytes | None:
    """Return PNG bytes for a 128×128 thumbnail read directly from pak, or None."""
    _ensure_gr_map()
    gr_lower = game_rel.lower()
    cont = _gr_to_cont.get(gr_lower)
    if not cont:
        return None

    entry = _get_toc(cont)
    if not entry:
        return None
    t, ucas = entry

    main_idx = t._dir.get(gr_lower)
    if main_idx is None:
        return None

    main_data = io_lib.read_chunk(t, ucas, main_idx)
    fmt_bytes  = None
    for fmt in _DXGI:
        if fmt in main_data:
            fmt_bytes = fmt; break
    if not fmt_bytes:
        return None

    bpb      = _BPB[fmt_bytes]
    dxgi_fmt = _DXGI[fmt_bytes]
    ph       = t.chunk_ids[main_idx][:8]
    bulk_idx = t._h2t.get(ph, {}).get(_BULK)
    if bulk_idx is None:
        return None

    bulk = io_lib.read_chunk(t, ucas, bulk_idx)
    # Last mip in the bulk is always 128×128 (64×64 and smaller are inlined in main chunk).
    # Fall back to 64×64 for unusually small textures.
    for dim in (128, 64):
        mip_sz = (dim // 4) * (dim // 4) * bpb
        if len(bulk) >= mip_sz:
            mip_data = bulk[-mip_sz:]
            break
    else:
        return None

    dds = _make_dds_dx10(dim, dim, dxgi_fmt, mip_data)
    try:
        img = Image.open(io.BytesIO(dds))
        img.load()
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, "PNG", optimize=False)
        return buf.getvalue()
    except Exception:
        return None
