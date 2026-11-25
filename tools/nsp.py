"""A game's NSP opened with the console's own keys: its ExeFS (main, sdk, subsdk0.., main.npdm) for tools/build.py, and its RomFS.

  python tools/nsp.py <game.nsp>                         what it holds
  python tools/nsp.py <game.nsp> <out folder> [--romfs]  the ExeFS into <out>/exefs (and the RomFS into <out>/romfs.bin); and
                                                         the game's name, version and icon, for the port's own: <out>/info.json,
                                                         <out>/icon.jpg (the update's, when there is one)
      --aoc <dlc.nsp>        an add-on content's files too (DLC; as many of these as there are): <out>/aoc<index>.romfs
      --update <update.nsp>  the game as its update leaves it: the update's ExeFS (an update brings all of it), and the game's
                             RomFS with the update's patch over it, as one image
      --keys <prod.keys>     default: ~/.switch/prod.keys, then ~/.3ds-nx/prod.keys

The keys are your console's (Lockpick_RCM writes prod.keys); none is in this project, and the tool never prints one. It reads
games as they are dumped: a title key from the NSP's own ticket (a common one), or the NCA's key area; and an update's patch
over the game's RomFS ("BKTR"), and the compression later games' RomFS has (LZ4 in blocks; needs the lz4 package). Not handled:
tickets tied to one console, NCA2 (firmware 1.0's), and sparse sections. The RomFS written is read back before the tool ends: its
header and every file's place must be inside it.

Needs pycryptodomex or pycryptodome (pip install pycryptodomex).
"""
import bisect
import io
import json
import os
import struct
import sys

try:
    from Cryptodome.Cipher import AES
    from Cryptodome.Util import Counter
except ImportError:
    try:
        from Crypto.Cipher import AES
        from Crypto.Util import Counter
    except ImportError:
        sys.exit('This needs pycryptodomex:  pip install pycryptodomex')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import progress  # noqa: E402

CONTENT_TYPES = ['Program', 'Meta', 'Control', 'Manual', 'Data', 'PublicData']


def load_keys(path):
    keys = {}
    for line in open(path, encoding='utf-8', errors='replace'):
        if '=' in line:
            k, v = line.split('=', 1)
            try:
                keys[k.strip().lower()] = bytes.fromhex(v.strip())
            except ValueError:
                pass
    return keys


def find_keys(args):
    if '--keys' in args:
        i = args.index('--keys')
        path = args[i + 1]
        del args[i:i + 2]
        return path
    for p in ('~/.switch/prod.keys', '~/.3ds-nx/prod.keys'):
        p = os.path.expanduser(p)
        if os.path.isfile(p):
            return p
    sys.exit('No prod.keys found (~/.switch/prod.keys). Give it with --keys <file>.')


def pfs0(read, size):
    """The files of a PFS0 (an NSP, an ExeFS): {name: (offset, size)}, offsets from the PFS0's start."""
    head = read(0, 16)
    if head[:4] != b'PFS0':
        raise ValueError(f'not a PFS0 (magic {head[:4]!r})')
    n, strsize = struct.unpack_from('<II', head, 4)
    table = read(16, n * 24 + strsize)
    data = 16 + n * 24 + strsize
    out = {}
    for k in range(n):
        off, sz, name = struct.unpack_from('<QQI', table, k * 24)
        end = table.index(b'\0', n * 24 + name)
        out[table[n * 24 + name:end].decode('utf-8')] = (data + off, sz)
    return out


def xts_decrypt(key, data, sector, sector_size=0x200):
    """AES-XTS as the NCA header has it: the tweak is the sector number, big endian."""
    k1, k2 = AES.new(key[:16], AES.MODE_ECB), AES.new(key[16:], AES.MODE_ECB)
    out = bytearray()
    for s in range(0, len(data), sector_size):
        t = int.from_bytes(k2.encrypt((sector + s // sector_size).to_bytes(16, 'big')), 'little')
        for b in range(s, s + sector_size, 16):
            tb = t.to_bytes(16, 'little')
            block = bytes(x ^ y for x, y in zip(data[b:b + 16], tb))
            out += bytes(x ^ y for x, y in zip(k1.decrypt(block), tb))
            t <<= 1
            if t >> 128:
                t = (t & ((1 << 128) - 1)) ^ 0x87
    return bytes(out)


class Nca:
    def __init__(self, f, base, size, keys, title_keys):
        self.f, self.base, self.size = f, base, size
        if 'header_key' not in keys:
            sys.exit('prod.keys has no header_key')
        f.seek(base)
        h = xts_decrypt(keys['header_key'], f.read(0xC00), 0)
        self.magic = h[0x200:0x204]
        if self.magic != b'NCA3':
            raise ValueError(f'not an NCA3 (it reads {self.magic!r}: wrong keys, or a format this tool doesn\'t handle)')
        self.content_type = h[0x205]
        self.program_id, = struct.unpack_from('<Q', h, 0x210)
        generation = max(h[0x206], h[0x220])
        self.key_index = max(generation - 1, 0)
        self.rights_id = h[0x230:0x240]
        self.key = None
        self.why = ''
        if any(self.rights_id):
            enc = title_keys.get(self.rights_id)
            kek = keys.get(f'titlekek_{self.key_index:02x}')
            if enc is None:
                self.why = 'its title key is in a ticket this NSP doesn\'t have'
            elif kek is None:
                self.why = f'prod.keys has no titlekek_{self.key_index:02x} (keys from an older firmware than the game?)'
            else:
                self.key = AES.new(kek, AES.MODE_ECB).decrypt(enc)
        else:
            kaek = keys.get(f'key_area_key_application_{self.key_index:02x}')
            if kaek is None:
                self.why = f'prod.keys has no key_area_key_application_{self.key_index:02x}'
            else:
                self.key = AES.new(kaek, AES.MODE_ECB).decrypt(h[0x300:0x340])[0x20:0x30]
        self.sections = []
        for i in range(4):
            start, end = struct.unpack_from('<II', h, 0x240 + i * 0x10)
            if not end:
                continue
            fh = h[0x400 + i * 0x200:0x600 + i * 0x200]
            sec = {'index': i, 'start': start * 0x200, 'end': end * 0x200, 'fs': fh[2], 'hash': fh[3], 'crypto': fh[4],
                   'ctr': fh[0x140:0x148][::-1]}
            if fh[3] == 2:  # hierarchical SHA-256: a PFS0 behind its hash table
                sec['data_off'], sec['data_size'] = struct.unpack_from('<QQ', fh, 0x40)
            elif fh[3] == 3 and fh[8:12] == b'IVFC':  # the RomFS behind its six levels of hashes
                sec['data_off'], sec['data_size'] = struct.unpack_from('<QQ', fh, 8 + 0x10 + 5 * 0x18)[:2]
                sec['levels'] = [struct.unpack_from('<QQI', fh, 8 + 0x10 + k * 0x18) for k in range(6)]  # offset, size, log2 of the block
            if fh[0x188:0x18C] == b'BKTR' and struct.unpack_from('<Q', fh, 0x180)[0]:  # compressed: its table, by offset and size
                sec['packed'] = struct.unpack_from('<QQ', fh, 0x178)
            if any(fh[0x148:0x178]):
                sec['sparse'] = True
            if fh[0x110:0x114] == b'BKTR' and fh[0x130:0x134] == b'BKTR':  # an update's patch: its two tables, by offset and size
                sec['tables'] = struct.unpack_from('<QQ', fh, 0x100), struct.unpack_from('<QQ', fh, 0x120)
            self.sections.append(sec)
        self.under = None  # for an update: the game's NCA and its RomFS section, which the patch is over

    def raw(self, sec, pos, n, prefix=None):
        """n bytes at pos in the section, decrypted (AES-CTR; prefix: the counter's upper half, the section's own if none)."""
        if self.key is None:
            raise ValueError(self.why)
        pos += sec['start']
        self.f.seek(self.base + (pos & ~15))
        data = self.f.read(((pos + n + 15) & ~15) - (pos & ~15))
        ctr = Counter.new(64, prefix=prefix or sec['ctr'], initial_value=(pos & ~15) >> 4)
        return AES.new(self.key, AES.MODE_CTR, counter=ctr).decrypt(data)[pos & 15:(pos & 15) + n]

    def tables(self, sec):
        """An update's patch section: where each stretch of the patched RomFS is (in the patch, or in the game's own RomFS), and
        the counter each stretch of the patch was encrypted with."""
        if 'where' in sec:
            return
        (ro, rs), (so, ss) = sec['tables']
        where, counters = [], []
        t = self.raw(sec, ro, rs)
        for b in range(struct.unpack_from('<I', t, 4)[0]):
            at = 0x4000 * (b + 1)
            for e in range(struct.unpack_from('<I', t, at + 4)[0]):
                where.append(struct.unpack_from('<QQI', t, at + 0x10 + e * 0x14))  # in the patched RomFS, in its source, 1: the patch
        sec['size'] = struct.unpack_from('<Q', t, 8)[0]
        t = self.raw(sec, so, ss)
        for b in range(struct.unpack_from('<I', t, 4)[0]):
            at = 0x4000 * (b + 1)
            for e in range(struct.unpack_from('<I', t, at + 4)[0]):
                o, _, c = struct.unpack_from('<QII', t, at + 0x10 + e * 0x10)
                counters.append((o, c))
        counters.append((ro, int.from_bytes(sec['ctr'][4:8], 'big')))  # the tables themselves: the section's own counter
        sec['where'], sec['where_at'] = where, [w[0] for w in where]
        sec['counters'], sec['counters_at'] = counters, [c[0] for c in counters]

    def patched(self, sec, pos, n):
        """n bytes at pos in the patched section."""
        self.tables(sec)
        out = bytearray()
        while n:
            k = bisect.bisect_right(sec['where_at'], pos) - 1
            virt, phys, in_patch = sec['where'][k]
            end = sec['where_at'][k + 1] if k + 1 < len(sec['where']) else sec['size']
            take = min(n, end - pos)
            if take <= 0:
                raise ValueError(f'the patch has nothing at {pos:#x}')
            src = phys + (pos - virt)
            if not in_patch:
                if self.under is None:
                    raise ValueError("this is an update: it needs the game it is over (give the game's NSP, and this one with --update)")
                out += self.under[0].raw(self.under[1], src, take)
            else:
                left = take
                while left:
                    c = bisect.bisect_right(sec['counters_at'], src) - 1
                    stop = sec['counters_at'][c + 1] if c + 1 < len(sec['counters']) else src + left
                    part = min(left, stop - src)
                    out += self.raw(sec, src, part, sec['ctr'][:4] + sec['counters'][c][1].to_bytes(4, 'big'))
                    src, left = src + part, left - part
            pos, n = pos + take, n - take
        return bytes(out)

    def read(self, sec, off, n):
        """n bytes at off in the section's data, decrypted."""
        if 'tables' in sec:
            return self.patched(sec, sec.get('data_off', 0) + off, n)
        pos = sec['start'] + sec.get('data_off', 0) + off
        self.f.seek(self.base + (pos & ~15))
        raw = self.f.read(((pos + n + 15) & ~15) - (pos & ~15))
        if sec['crypto'] == 1:
            plain = raw
        elif sec['crypto'] == 3:
            if self.key is None:
                raise ValueError(self.why)
            ctr = Counter.new(64, prefix=sec['ctr'], initial_value=(pos & ~15) >> 4)
            plain = AES.new(self.key, AES.MODE_CTR, counter=ctr).decrypt(raw)
        else:
            raise ValueError(f'section {sec["index"]} uses encryption type {sec["crypto"]}, which isn\'t handled (an update\'s patch section?)')
        return plain[pos & 15:(pos & 15) + n]


def unpack(nca, sec, out, said=('extract', '')):
    """A compressed RomFS written out as it is read by a game: the section's data is a run of blocks, each kept as it was, as
    nothing (zeros) or as LZ4, and a table at its end says which, and where each goes. Returns the size written."""
    try:
        import lz4.block
    except ImportError:
        sys.exit('This game\'s RomFS is compressed (LZ4), which needs the lz4 package:  pip install lz4')
    off, size = sec['packed']
    t = nca.read(sec, off, size)
    total = struct.unpack_from('<q', t, 8)[0]
    entries = []
    for k in range(struct.unpack_from('<i', t, 4)[0]):
        at = 0x4000 * (k + 1)
        for e in range(struct.unpack_from('<i', t, at + 4)[0]):
            entries.append(struct.unpack_from('<qqBbxxI', t, at + 0x10 + e * 0x18))  # where it goes, where it is, kind, level, size kept
    entries.sort()
    window, window_at = b'', 0

    def kept(pos, n):
        nonlocal window, window_at
        if pos < window_at or pos + n > window_at + len(window):
            window_at = pos
            window = nca.read(sec, pos, min(max(n, 1 << 24), sec['data_size'] - pos))
        return window[pos - window_at:pos - window_at + n]
    written = 0
    for i, (virt, phys, kind, _, kept_size) in enumerate(entries):
        n = (entries[i + 1][0] if i + 1 < len(entries) else total) - virt
        if virt != written:
            sys.exit(f'The compression table has a gap at {written:#x}: no RomFS written.')
        if kind == 1:
            data = bytes(n)
        elif kind == 0:
            data = kept(phys, n)
        elif kind == 3:
            data = lz4.block.decompress(kept(phys, kept_size), uncompressed_size=n)
        else:
            sys.exit(f'A block of the RomFS is compressed in a way this tool doesn\'t know (kind {kind}): no RomFS written.')
        if len(data) != n:
            sys.exit(f'A block of the RomFS came out {len(data)} bytes for {n}: no RomFS written.')
        out.write(data)
        written += n
        progress(said[0], said[1], written, total)
    return written


def check_romfs(path):
    """A RomFS image read back: its tables and every file's data must be inside it. Returns the count of files; ends the tool, and
    removes the image, if it isn't right (a wrong one, given to a game, ends it with "the data is corrupted")."""
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        h = struct.unpack('<10Q', f.read(0x50))
        bad = None
        if h[0] != 0x50:
            bad = f'its header says it is {h[0]:#x} bytes long, not 0x50'
        for k in range(4):
            if not bad and h[1 + 2 * k] + h[2 + 2 * k] > size:
                bad = f'its table {k} is at {h[1 + 2 * k]:#x}, beyond its {size:#x} bytes'
        files = 0
        if not bad:
            f.seek(h[7])
            t = f.read(h[8])
            at = 0
            while at + 0x20 <= len(t):
                _, _, off, n, _, name = struct.unpack_from('<IIQQII', t, at)
                if h[9] + off + n > size or name > 0x300:
                    bad = f'a file\'s data is at {h[9] + off:#x}, {n:#x} bytes, beyond the image\'s {size:#x}'
                    break
                files += 1
                at += 0x20 + ((name + 3) & ~3)
    if bad:
        os.remove(path)
        sys.exit(f'The RomFS written isn\'t a right one ({bad}): removed.')
    return files


class Opened(io.FileIO):
    data = ()
    control = None


def romfs_root(image):
    """The files in a small RomFS image's root folder: {name: bytes}."""
    h = struct.unpack_from('<10Q', image, 0)
    t = image[h[7]:h[7] + h[8]]
    out, at = {}, 0
    while at + 0x20 <= len(t):
        _, _, off, n, _, name = struct.unpack_from('<IIQQII', t, at)
        out[t[at + 0x20:at + 0x20 + name].decode('utf-8', 'replace')] = image[h[9] + off:h[9] + off + n]
        at += 0x20 + ((name + 3) & ~3)
    return out


def write_control(nca, out, title_id):
    """The game's name, its maker's, its version and its icon, from its control data: <out>/info.json and <out>/icon.jpg."""
    info = {'title': '', 'author': '', 'version': '', 'title_id': f'{title_id:016x}'}
    try:
        sec = next(s for s in nca.sections if s['fs'] == 0 and 'data_off' in s)
        files = romfs_root(nca.read(sec, 0, sec['data_size']))
    except (StopIteration, ValueError, struct.error) as e:
        print(f'  the control data couldn\'t be read ({e}): the port will have the name it is given, and no icon')
        files = {}
    nacp = files.get('control.nacp', b'')
    for lang in range(16):  # the first language that has a name (American English is the first of them)
        name = nacp[lang * 0x300:lang * 0x300 + 0x200].split(b'\0')[0].decode('utf-8', 'replace')
        if name:
            info['title'] = name
            info['author'] = nacp[lang * 0x300 + 0x200:lang * 0x300 + 0x300].split(b'\0')[0].decode('utf-8', 'replace')
            break
    info['version'] = nacp[0x3060:0x3070].split(b'\0')[0].decode('utf-8', 'replace')
    icons = sorted(n for n in files if n.startswith('icon_') and n.endswith('.dat'))
    icon = 'icon_AmericanEnglish.dat' if 'icon_AmericanEnglish.dat' in icons else (icons[0] if icons else None)
    if icon and files[icon][:2] == b'\xff\xd8':
        open(f'{out}/icon.jpg', 'wb').write(files[icon])
        info['icon'] = 'icon.jpg'
    json.dump(info, open(f'{out}/info.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{out}/info.json: {info["title"]!r}, version {info["version"]!r}, by {info["author"]!r}' + ('; icon.jpg' if icon else '; no icon'))


def write_romfs(nca, sec, path, said=('extract', '')):
    """A RomFS section written out as a game reads it, and read back. Returns its size and its count of files."""
    if sec.get('sparse') or (nca.under and nca.under[1].get('sparse')):
        sys.exit('This RomFS is a sparse one, which this tool doesn\'t read: no RomFS written.')
    with open(path, 'wb') as o:
        if 'packed' in sec:
            size = unpack(nca, sec, o, said)
        else:
            size = sec['data_size']
            for at in range(0, size, 1 << 24):
                o.write(nca.read(sec, at, min(1 << 24, size - at)))
                progress(said[0], said[1], min(size, at + (1 << 24)), size)
    return size, check_romfs(path)


def open_nsp(path, keys):
    """What an NSP holds, said; its program NCA (or None) and the file, left open."""
    f = Opened(path, 'rb')

    def raw(off, n):
        f.seek(off)
        return f.read(n)
    files = pfs0(raw, os.path.getsize(path))
    title_keys = {}
    for name, (off, size) in files.items():
        if name.endswith('.tik'):
            t = raw(off, size)
            # a ticket with an RSA-2048 signature: the title key block at 0x180, the rights id at 0x2A0
            if struct.unpack_from('<I', t, 0)[0] == 0x10004 and t[0x281] == 0:
                title_keys[t[0x2A0:0x2B0]] = t[0x180:0x190]
            else:
                print(f"{name}: a ticket tied to one console, or of a kind this tool doesn't read")
    program, data = None, []
    for name, (off, size) in sorted(files.items()):
        if not name.endswith('.nca'):
            print(f'  {name}: {size} bytes')
            continue
        try:
            nca = Nca(f, off, size, keys, title_keys)
        except ValueError as e:
            print(f'  {name}: {e}')
            continue
        kind = CONTENT_TYPES[nca.content_type] if nca.content_type < len(CONTENT_TYPES) else '?'
        parts = ', '.join(('RomFS' if s['fs'] == 0 else 'PFS0') + f' {s.get("data_size", 0) >> 10} KB' + (' (a patch)' if 'tables' in s else '')
                          for s in nca.sections)
        print(f'  {name}: {kind}, title {nca.program_id:016x}, {size >> 10} KB: {parts}' + (f' ({nca.why})' if nca.why else ''))
        if nca.content_type == 0 and (program is None or size > program.size):
            program = nca
        if nca.content_type in (4, 5):
            data.append(nca)
        if nca.content_type == 2:
            f.control = nca
    f.data = data  # (an add-on content's NCAs)
    return program, f


def main():
    args = sys.argv[1:]
    romfs = '--romfs' in args
    if romfs:
        args.remove('--romfs')
    if not args:
        sys.exit(__doc__)
    keys = load_keys(find_keys(args))
    aocs = []
    while '--aoc' in args:
        i = args.index('--aoc')
        aocs.append(args[i + 1])
        del args[i:i + 2]
    update = None
    if '--update' in args:
        i = args.index('--update')
        update = args[i + 1]
        del args[i:i + 2]
    path, out = args[0], (args[1] if len(args) > 1 else None)
    program, f = open_nsp(path, keys)
    control, title_id = f.control, (program.program_id if program else 0)
    if update:
        print(f'the update, {update}:')
        patch, f2 = open_nsp(update, keys)
        control = f2.control or control
        if patch is None:
            sys.exit('No program NCA could be opened in the update.')
        if program is not None:
            patch.under = next(((program, sec) for sec in program.sections if sec['fs'] == 0), None)
        program = patch
    if not out:
        return
    if program is None:
        sys.exit('No program NCA could be opened in this NSP.')
    os.makedirs(f'{out}/exefs', exist_ok=True)
    done = False
    for sec in program.sections:
        if sec['fs'] != 1 or 'data_off' not in sec:
            continue
        try:
            inner = pfs0(lambda o, n: program.read(sec, o, n), sec['data_size'])
        except ValueError as e:
            print(f'section {sec["index"]}: {e}')
            continue
        if 'main' not in inner:
            continue  # (the logo's section is a PFS0 too)
        for name, (off, size) in inner.items():
            open(f'{out}/exefs/{name}', 'wb').write(program.read(sec, off, size))
            print(f'{out}/exefs/{name}: {size} bytes')
        done = True
    if not done:
        sys.exit('The program NCA has no ExeFS this tool could read.')
    if control is not None:
        write_control(control, out, title_id)
    if romfs:
        for sec in program.sections:
            if sec['fs'] == 0 and 'data_off' in sec:
                size, files = write_romfs(program, sec, f'{out}/romfs.bin')
                print(f'{out}/romfs.bin: {size >> 20} MB, {files} files, each inside the image')
    for path in aocs:
        print(f'the add-on content, {path}:')
        _, fa = open_nsp(path, keys)
        done = False
        for nca in fa.data:
            for sec in nca.sections:
                if sec['fs'] == 0 and 'data_off' in sec:
                    index = nca.program_id & 0xFFF
                    size, files = write_romfs(nca, sec, f'{out}/aoc{index}.romfs', ('aoc', str(index)))
                    print(f'{out}/aoc{index}.romfs: {size >> 10} KB, {files} files: add-on content {index} of {nca.program_id:016x}')
                    done = True
        if not done:
            print('  no data in it that this tool could read')


if __name__ == '__main__':
    main()
