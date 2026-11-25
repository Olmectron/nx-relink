"""A Switch executable (NSO0): its three segments decompressed, and what the relinker and the loader need from it.

  python tools/nso.py <file.nso>          what it holds

A segment is packed as one LZ4 block or, in some Switch 2 games (flag 0x80 of the header), as ZBIC, which tools/zbic.py reads.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zbic  # noqa: E402


def lz4_block(src, out_size):
    """LZ4 block decompression (the NSO's segments are single blocks)."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | src[i + 1] << 8
        i += 2
        length = token & 15
        if length == 15:
            while True:
                b = src[i]
                i += 1
                length += b
                if b != 255:
                    break
        length += 4
        start = len(out) - off
        if off >= length:
            out += out[start:start + length]
        else:  # overlapping: the pattern repeats
            for k in range(length):
                out.append(out[start + k])
    if len(out) != out_size:
        raise ValueError(f'LZ4: {len(out)} bytes out, {out_size} expected')
    return bytes(out)


class Nso:
    def __init__(self, path):
        d = open(path, 'rb').read()
        if d[:4] != b'NSO0':
            raise ValueError(f'{path}: not an NSO (magic {d[:4]!r})')
        self.path = path
        flags, = struct.unpack_from('<I', d, 0x0C)
        self.seg = []  # (mem_off, bytes) for text, rodata, data
        comp = struct.unpack_from('<3I', d, 0x60)
        for k in range(3):
            file_off, mem_off, size = struct.unpack_from('<3I', d, 0x10 + k * 0x10)
            raw = d[file_off:file_off + comp[k]]
            if not flags >> k & 1:
                part = raw[:size]
            elif flags & 0x80 and raw[:4] == zbic.MAGIC:
                part = zbic.unpack(raw, size)
            else:
                part = lz4_block(raw, size)
            self.seg.append((mem_off, part))
        self.bss_size, = struct.unpack_from('<I', d, 0x3C)
        self.module_id = d[0x40:0x60]
        self.dynstr = struct.unpack_from('<2I', d, 0x90)  # (offset in rodata, size)
        self.dynsym = struct.unpack_from('<2I', d, 0x98)
        (self.text_off, self.text), (self.ro_off, self.ro), (self.data_off, self.data) = self.seg
        if self.text_off != 0:
            raise ValueError('text does not start the module')
        # MOD0: where the dynamic section and the bss are
        mod0, = struct.unpack_from('<I', self.text, 4)
        img = self.image()
        if img[mod0:mod0 + 4] != b'MOD0':
            raise ValueError(f'no MOD0 at {mod0:#x}')
        dyn, bss0, bss1, eh0, eh1, modobj = struct.unpack_from('<6i', img, mod0 + 4)
        self.mod0 = mod0
        self.dynamic = mod0 + dyn
        self.bss_start, self.bss_end = mod0 + bss0, mod0 + bss1
        self.module_object = mod0 + modobj
        self.end = max(self.data_off + len(self.data) + self.bss_size, self.bss_end)
        # The dynamic symbols and their names, as places in the module. The header says where they are; one that doesn't (elf2nso's
        # leave it empty) has them in its dynamic section, and their count in its hash table.
        self.symtab, self.nsyms = self.ro_off + self.dynsym[0], self.dynsym[1] // 24
        self.strtab, self.strsz = self.ro_off + self.dynstr[0], self.dynstr[1]
        if not self.nsyms:
            t = self.dyn_entries()
            self.symtab, self.strtab, self.strsz = t.get(6, 0), t.get(5, 0), t.get(10, 0)
            if 4 in t:
                self.nsyms = struct.unpack_from('<I', img, t[4] + 4)[0]
            elif self.strtab > self.symtab:
                self.nsyms = (self.strtab - self.symtab) // 24

    def image(self):
        """The module as it sits in memory, without the bss."""
        img = bytearray(self.data_off + len(self.data))
        for off, b in self.seg:
            img[off:off + len(b)] = b
        return bytes(img)

    def dyn_entries(self):
        img = self.image()
        out = {}
        a = self.dynamic
        while a + 16 <= len(img):
            tag, val = struct.unpack_from('<qQ', img, a)
            if tag == 0:
                break
            out.setdefault(tag, val)
            a += 16
        return out

    def symbols(self):
        """(name, value, shndx, info) of every dynamic symbol."""
        img = self.image()
        strs = img[self.strtab:self.strtab + self.strsz]
        out = []
        for k in range(self.nsyms):
            name, info, other, shndx, value, sz = struct.unpack_from('<IBBHQQ', img, self.symtab + k * 24)
            end = strs.find(bytes(1), name)
            out.append((strs[name:end].decode('latin1'), value, shndx, info))
        return out


def main():
    n = Nso(sys.argv[1])
    print(f'{n.path}: module id {n.module_id[:20].hex()}')
    for name, (off, b) in zip(('text', 'rodata', 'data'), n.seg):
        print(f'  {name:6} at {off:#010x}, {len(b):#x} bytes')
    print(f'  bss    at {n.bss_start:#010x}, {n.bss_end - n.bss_start:#x} bytes (header says {n.bss_size:#x})')
    print(f'  MOD0 at {n.mod0:#x}, dynamic at {n.dynamic:#x}')
    syms = n.symbols()
    defined = [s for s in syms if s[2]]
    print(f'  {len(syms)} dynamic symbols, {len(defined)} defined here, {len(syms) - len(defined)} imported')
    tags = n.dyn_entries()
    print('  dynamic tags: ' + ', '.join(f'{t}={v:#x}' for t, v in sorted(tags.items())))
    words = struct.unpack_from(f'<{len(n.text) // 4}I', n.text)
    svcs = {}
    for w in words:
        if w & 0xFFE0001F == 0xD4000001:
            svcs[(w >> 5) & 0xFFFF] = svcs.get((w >> 5) & 0xFFFF, 0) + 1
    print('  system calls in the code: ' + ' '.join(f'{k:02x}' + (f'x{v}' if v > 1 else '') for k, v in sorted(svcs.items())))


if __name__ == '__main__':
    main()
