import struct, ctypes, os

_TOOLS = os.environ.get("MR_TOOLS") or os.path.join(os.path.dirname(__file__), "Tools")
if os.path.exists(AES_PATH := os.path.join(_TOOLS, "AES_KEY.txt")):
    with open(AES_PATH) as AES_FILE: AES_KEY = bytes.fromhex(AES_FILE.read().strip())
# Oodle (oodle_*/read_chunk) removed: the app extracts/decodes via UAssetTool, not in-process
# chunk reads. io_lib now only builds the pak path index (parse_toc + parse_dir_index, AES-decrypted).

# --- AES-256-ECB via Windows CNG (bcrypt) ---
_bcrypt = ctypes.windll.bcrypt
def _aes_ecb(data, decrypt):
    BCRYPT_AES_ALGORITHM = ctypes.c_wchar_p("AES")
    hAlg = ctypes.c_void_p()
    _bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(hAlg), BCRYPT_AES_ALGORITHM, None, 0)
    chain = ctypes.create_unicode_buffer("ChainingModeECB")
    _bcrypt.BCryptSetProperty(hAlg, ctypes.c_wchar_p("ChainingMode"),
                              ctypes.cast(chain, ctypes.c_void_p), (len(chain.value)+1)*2, 0)
    hKey = ctypes.c_void_p()
    keyobj = ctypes.create_string_buffer(0)  # let provider manage
    r = _bcrypt.BCryptGenerateSymmetricKey(hAlg, ctypes.byref(hKey), None, 0,
                                           AES_KEY, len(AES_KEY), 0)
    out = ctypes.create_string_buffer(len(data))
    cb = ctypes.c_ulong(0)
    fn = _bcrypt.BCryptDecrypt if decrypt else _bcrypt.BCryptEncrypt
    st = fn(hKey, data, len(data), None, None, 0, out, len(data), ctypes.byref(cb), 0)
    _bcrypt.BCryptDestroyKey(hKey); _bcrypt.BCryptCloseAlgorithmProvider(hAlg, 0)
    if st != 0: raise RuntimeError(f"BCrypt status {st:#x}")
    return out.raw[:cb.value]
def aes_decrypt(data): return _aes_ecb(data, True)

FLAGBITS = {1:"Compressed",2:"Encrypted",4:"Signed",8:"Indexed",16:"OnDemand"}

class Toc:
    pass

def parse_toc(path):
    buf = open(path, "rb").read()
    assert buf[:16] == b"-==--==--==--==-", "bad magic"
    t = Toc(); t.buf = buf; t.path = path
    t.version = buf[16]
    g = lambda o: struct.unpack_from("<I", buf, o)[0]
    g64 = lambda o: struct.unpack_from("<Q", buf, o)[0]
    b = 20
    t.hdr_size = g(b); t.entry_count = g(b+4); t.cblk_count = g(b+8); t.cblk_entry_size = g(b+12)
    t.cm_name_count = g(b+16); t.cm_name_len = g(b+20); t.cblk_size = g(b+24)
    t.dir_index_size = g(b+28); t.partition_count = g(b+32); t.container_id = g64(b+36)
    t.enc_guid = buf[b+44:b+60]; t.flags = buf[b+60]
    t.phash_seed_count = g(b+64); t.partition_size = g64(b+68); t.chunks_wo_phash = g(b+76)
    t.encrypted = bool(t.flags & 2); t.signed = bool(t.flags & 4); t.indexed = bool(t.flags & 8)
    p = 144
    t.off_chunkids = p
    t.chunk_ids = [buf[p+i*12:p+i*12+12] for i in range(t.entry_count)]; p += 12*t.entry_count
    t.off_offlen = p
    t.offlen = []
    for i in range(t.entry_count):
        r = buf[p+i*10:p+i*10+10]
        t.offlen.append((int.from_bytes(r[0:5],"big"), int.from_bytes(r[5:10],"big")))
    p += 10*t.entry_count
    if t.phash_seed_count: p += 4*t.phash_seed_count
    if t.chunks_wo_phash: p += 4*t.chunks_wo_phash
    t.off_blocks = p
    t.blocks = []
    for i in range(t.cblk_count):
        e = buf[p+i*12:p+i*12+12]
        t.blocks.append([int.from_bytes(e[0:5],"little"), int.from_bytes(e[5:8],"little"),
                         int.from_bytes(e[8:11],"little"), e[11]])
    p += 12*t.cblk_count
    t.off_methods = p
    t.methods = [buf[p+i*t.cm_name_len:p+i*t.cm_name_len+t.cm_name_len].split(b"\x00")[0].decode("latin1")
                 for i in range(t.cm_name_count)]
    p += t.cm_name_count*t.cm_name_len
    if t.signed:
        hs = struct.unpack_from("<I", buf, p)[0]; p += 4
        p += hs  # toc signature
        p += hs  # block signature
        p += 20*t.cblk_count  # per-block sha1
    t.off_dirindex = p; p += t.dir_index_size
    t.off_meta = p
    t.meta = [buf[p+i*33:p+i*33+33] for i in range(t.entry_count)]
    t.flagstr = ",".join(v for bit,v in FLAGBITS.items() if t.flags & bit) or "None"
    return t

def parse_dir_index(t):
    """Decode the TOC directory index -> list of (path, toc_entry_index)."""
    blob = t.buf[t.off_dirindex:t.off_dirindex + t.dir_index_size]
    if t.encrypted:
        blob = aes_decrypt(blob[:(len(blob) // 16) * 16])
    o = [0]
    def u32():
        v = struct.unpack_from("<I", blob, o[0])[0]; o[0] += 4; return v
    def i32():
        v = struct.unpack_from("<i", blob, o[0])[0]; o[0] += 4; return v
    def fstr():
        n = i32()
        if n == 0: return ""
        if n > 0:
            s = blob[o[0]:o[0] + n - 1].decode("latin1"); o[0] += n; return s
        n = -n; s = blob[o[0]:o[0] + (n - 1) * 2].decode("utf-16-le"); o[0] += n * 2; return s
    mount = fstr()
    dirs = [(u32(), u32(), u32(), u32()) for _ in range(u32())]
    files = [(u32(), u32(), u32()) for _ in range(u32())]
    strings = [fstr() for _ in range(u32())]
    INV = 0xFFFFFFFF
    out = []
    def walk(di, prefix):
        name, firstchild, _ns, firstfile = dirs[di]
        p = prefix if name == INV else prefix + "/" + strings[name]
        f = firstfile
        while f != INV:
            fn, nxt, ud = files[f]
            out.append((p + "/" + strings[fn], ud)); f = nxt
        c = firstchild
        while c != INV:
            walk(c, p); c = dirs[c][2]
    if dirs:
        walk(0, mount.rstrip("/"))
    return out
