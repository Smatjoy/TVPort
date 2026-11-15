#!/usr/bin/env python3
"""ExeFS mods: the code patches that mod sites share for a game's modules, applied to the modules before the rewriter (patch_nso.py) sees them, so they are part of the
app's signed code. A cheat that writes code at run time cannot do that on an iPhone or an Apple TV (dm/host/hle_cheats.c); a patch made here can, on every platform.
Formats, as Atmosphère's loader and IPSwitch read them:
  · <build ID>.ips: IPS ("PATCH" … "EOF", 3-byte offsets) or IPS32 ("IPS32" … "EEOF", 4-byte offsets); records of offset, 2-byte size and bytes, or an RLE record (size 0, then a 2-byte
    count and one byte). Offsets are into the NSO file as if its 0x100-byte header were followed by the uncompressed image, so the image offset is the offset - 0x100. The file name is
    the module's build ID (Atmosphère: the first bytes, as many as the name has, up to 32 hex digits).
  · *.pchtxt (IPSwitch's text form): @nsobid-<build ID> names the module; @flag offset_shift <n> shifts every offset (image offset = offset + shift - 0x100, so 0x100 means image
    offsets); patches are read only between @enabled and @stop (an @disabled block is skipped); a patch line is "<hex offset> <hex bytes>" or "<hex offset> \"text\"" (a C string with
    \\n, \\t, \\", \\\\ escapes, no terminating zero); // and # start comments.
A mod folder holds mods, each a folder with an exefs/ folder of those files (the layout Ryujinx and the yuzu family use: <mod>/exefs/*.pchtxt; also Atmosphère's
atmosphere/exefs_patches/<mod>/*.ips): every exefs/ and exefs_patches/<mod>/ folder found under it counts, and a mod whose folder name starts with '-' is off.
Usage: exefs_mods.py <mods folder> <module file> <out file>   (writes the patched module as an uncompressed NSO, or reports that none applies and writes nothing; exit 1 on an error)"""
import re, struct, sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nso

HEADER = 0x100   # the NSO header the IPS offsets count


class Error(ValueError):
    pass


class Patch(NamedTuple):
    offset: int   # into the module's memory image
    data: bytes
    source: str   # file and line, for messages


def matches(name_bid: str, build_id: bytes) -> bool:
    """A file's build ID (as written, any length up to 32 hex digits; trailing zeros as Atmosphère pads them) names this module"""
    want = name_bid.upper().rstrip('0')
    have = build_id.hex().upper()
    return bool(want) and len(name_bid) >= 16 and have.startswith(want)


def read_ips(data: bytes, source: str) -> list:
    if data.startswith(b'IPS32'): at, width, end = 5, 4, b'EEOF'
    elif data.startswith(b'PATCH'): at, width, end = 5, 3, b'EOF'
    else: raise Error(f'{source}: not an IPS or IPS32 file')
    out = []
    while True:
        if data[at:at + len(end)] == end and len(data) - at in (len(end), len(end) + 3): return out   # IPS may carry a 3-byte truncation size after EOF
        if at + width + 2 > len(data): raise Error(f'{source}: cut short at {at:#x} (no {end.decode()})')
        off = int.from_bytes(data[at:at + width], 'big'); size = struct.unpack_from('>H', data, at + width)[0]; at += width + 2
        if size == 0:
            if at + 3 > len(data): raise Error(f'{source}: RLE record cut short at {at:#x}')
            count = struct.unpack_from('>H', data, at)[0]; body = data[at + 2:at + 3] * count; at += 3
        else:
            if at + size > len(data): raise Error(f'{source}: record at {off:#x} cut short')
            body = data[at:at + size]; at += size
        if off < HEADER: raise Error(f'{source}: record at {off:#x} patches the NSO header, which the module image does not have')
        out.append(Patch(off - HEADER, bytes(body), f'{source} @{off:#x}'))


STRING_ESC = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', '0': '\0'}


def read_pchtxt(text: str, source: str):
    """(build ID as written, [Patch]) of an IPSwitch text file"""
    bid, shift, on, out = '', 0, False, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line.startswith('"') and '"' not in line: line = line.split('//')[0].strip()
        if not line or line.startswith('#') or line.startswith('//'): continue
        where = f'{source}:{n}'
        if line.startswith('@'):
            word = line.split()[0].lower()
            if word.startswith('@nsobid-'): bid = line.split()[0][8:]
            elif word == '@flag':
                parts = line.split()
                if len(parts) >= 3 and parts[1] == 'offset_shift': shift = int(parts[2], 0)
            elif word == '@enabled': on = True
            elif word in ('@disabled', '@stop'): on = False
            continue   # other @ lines (an author's name) carry nothing
        if not on: continue
        m = re.match(r'^([0-9A-Fa-f]+)\s+(.*)$', line)
        if not m: raise Error(f'{where}: not a patch line: {raw.strip()}')
        off, value = int(m[1], 16) + shift - HEADER, m[2].strip()
        if value.startswith('"'):
            s = re.match(r'^"((?:[^"\\]|\\.)*)"', value)
            if not s: raise Error(f'{where}: unterminated string')
            body = re.sub(r'\\(.)', lambda e: STRING_ESC.get(e[1], e[1]), s[1]).encode('utf-8')
        else:
            value = value.split('//')[0].strip()
            if not re.fullmatch(r'(?:[0-9A-Fa-f]{2})+', value): raise Error(f'{where}: not hex bytes: {value}')
            body = bytes.fromhex(value)
        if off < 0: raise Error(f'{where}: offset before the module image (offset_shift {shift:#x})')
        out.append(Patch(off, body, where))
    return bid, out


def mod_folders(root: Path) -> list:
    """(mod name, folder of its files) of every mod under root, sorted by name; a mod whose folder starts with '-' is off"""
    found = set()
    for d in root.rglob('*'):
        if not d.is_dir(): continue
        if d.name.lower() == 'exefs': found.add((d.parent.name, d))
        elif d.parent.name.lower() == 'exefs_patches': found.add((d.name, d))
    return sorted((n, d) for n, d in found if not n.startswith('-') and not any(p.name.startswith('-') for p in d.relative_to(root).parents if p.name))


def patches_for(root: Path, build_id: bytes) -> list:
    """[(mod name, file, [Patch])] of every enabled mod file naming this build"""
    out = []
    for name, d in mod_folders(root):
        for f in sorted(d.iterdir()):
            if f.name.startswith('._') or not f.is_file(): continue   # macOS AppleDouble files on exFAT drives
            if f.suffix.lower() == '.pchtxt':
                bid, ps = read_pchtxt(f.read_text(encoding='utf-8', errors='replace'), f'{name}/{f.name}')
                if bid and matches(bid, build_id) and ps: out.append((name, f, ps))
            elif f.suffix.lower() == '.ips' and matches(f.stem, build_id):
                ps = read_ips(f.read_bytes(), f'{name}/{f.name}')
                if ps: out.append((name, f, ps))
    return out


def apply(module: nso.Module, found: list) -> list:
    """Applies the patches to the image in place; returns the patched ranges [(offset, size, source)]. Patches outside text/ro/data or two mods writing the same bytes differently are errors"""
    spans, ranges = module.segments, []
    for name, f, ps in found:
        for p in ps:
            end = p.offset + len(p.data)
            if not any(mo <= p.offset and end <= mo + sz for mo, sz in spans): raise Error(f'{p.source}: {p.offset:#x}..{end:#x} is outside the module\'s segments')
            for o, s, src in ranges:
                if o < end and p.offset < o + s:
                    lo, hi = max(o, p.offset), min(o + s, end)
                    if bytes(module.image[lo:hi]) != p.data[lo - p.offset:hi - p.offset] and not src.startswith(name + '/'):
                        raise Error(f'{p.source} and {src} write {lo:#x}..{hi:#x} differently: turn one of the two mods off')
            module.image[p.offset:end] = p.data
            ranges.append((p.offset, len(p.data), p.source))
    return ranges


def write_nso(module: nso.Module, path: Path) -> None:
    """An uncompressed NSO of the image: segments stored at their memory offsets after the header, no compression or hash flags (as a modified main is shipped), the same build ID"""
    (t, ts), (r, rs), (d, ds) = module.segments
    h = bytearray(HEADER)
    h[0:4] = b'NSO0'
    for i, (mo, sz) in enumerate(module.segments):
        struct.pack_into('<III', h, 0x10 + i * 0x10, HEADER + mo, mo, sz)
        struct.pack_into('<I', h, 0x60 + i * 4, sz)
    struct.pack_into('<I', h, 0x1c, 0)   # module name offset (unused: the profile names the modules)
    struct.pack_into('<I', h, 0x2c, 0)
    struct.pack_into('<I', h, 0x3c, module.bss)
    h[0x40:0x60] = module.build_id
    path.write_bytes(bytes(h) + bytes(module.image))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3: sys.exit(__doc__.rsplit('Usage: ', 1)[1])
    root, src, dst = map(Path, argv)
    try:
        m = nso.read(src)
        found = patches_for(root, m.build_id)
        if not found: print(f'exefs_mods: {src.name}: no mod names build {m.build_id[:16].hex().upper()}'); return 0
        ranges = apply(m, found)
        write_nso(m, dst)
        check = nso.read(dst)
        if check.image != m.image or check.build_id != m.build_id: raise Error(f'{dst}: the written module does not read back as patched')
    except (Error, nso.Error, OSError) as e:
        print(f'[RED] exefs_mods: {e}'); return 1
    for name, f, ps in found: print(f'exefs_mods: {src.name}: {name} ({f.name}): {len(ps)} patches, {sum(len(p.data) for p in ps)} bytes')
    print(f'exefs_mods: {src.name}: {len(ranges)} patches from {len(found)} mods → {dst}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
