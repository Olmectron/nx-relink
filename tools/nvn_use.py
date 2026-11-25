"""Which NVN functions a game's module uses. A game asks NVN for each of its functions by name and keeps the pointer in a global;
the names it asks for are the whole API, so what counts is which of those globals its code then reads.
  python tools/nvn_use.py <exefs folder> [module ...] [--list]      (default: main and every subsdk)

tools/check.py uses this to say how many of them the port's own NVN (runtime/nvn2.c, for a Switch 2) doesn't have yet.
"""
import collections
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nso import Nso  # noqa: E402


def scan(mod):
    img = mod.image()
    text_end = mod.ro_off
    words = struct.unpack_from(f'<{text_end // 4}I', img, 0)

    def cstr(a):
        if a < 0 or a >= len(img):
            return None
        e = img.find(b'\0', a, a + 96)
        s = img[a:e] if e > a else b''
        return s.decode('ascii', 'replace') if s and all(32 <= c < 127 for c in s) else None

    page = [None] * 32      # what adrp last put in each register
    addr = [None] * 32      # ... or adrp+add
    pending = None          # the NVN name just taken, waiting for the store of its pointer
    got = [None] * 32       # the table entry a register was loaded from (the pointer's global is reached through it)
    slot_name, loads, asked, own = {}, collections.Counter(), [], collections.Counter()
    stores_at = {}
    for i, w in enumerate(words):
        pc = i * 4
        rd = w & 31
        if w & 0x9F000000 == 0x90000000:  # adrp
            imm = ((w >> 5) & 0x7FFFF) << 2 | (w >> 29) & 3
            if imm >> 20:
                imm -= 1 << 21
            page[rd] = ((pc >> 12) + imm) << 12
            addr[rd] = None
        elif w & 0xFFC00000 == 0x91000000:  # add x, x, #imm
            rn = (w >> 5) & 31
            if page[rn] is not None:
                a = page[rn] + ((w >> 10) & 0xFFF)
                addr[rd] = a
                s = cstr(a)
                if s and re.fullmatch(r'nvn[A-Z]\w+', s):
                    pending = s
                    asked.append(s)
            if rd != rn:
                page[rd] = None
        elif w & 0xFFC00000 == 0xF9000000:  # str x, [x, #imm]
            rn = (w >> 5) & 31
            if pending and (w >> 10) & 0xFFF == 0 and got[rn] is not None:  # through the table: str x0, [x8]
                slot_name[got[rn]] = pending
                own[got[rn]] = 1
                pending = None
            elif page[rn] is not None and pending:
                slot = page[rn] + ((w >> 10) & 0xFFF) * 8
                if slot >= mod.data_off:
                    slot_name[slot] = pending
                    stores_at[slot] = pc
                    pending = None
        elif w & 0xFFC00000 == 0xF9400000:  # ldr x, [x, #imm]
            rn = (w >> 5) & 31
            src = page[rn] + ((w >> 10) & 0xFFF) * 8 if page[rn] is not None else None
            if src is not None:
                loads[src] += 1
            page[rd] = addr[rd] = None
            got[rd] = src
        elif w & 0xFC000000 in (0x94000000, 0x14000000) or w & 0xFFFFFC1F in (0xD63F0000, 0xD61F0000) or w == 0xD65F03C0:
            if w & 0xFC000000 == 0x94000000 or w & 0xFFFFFC1F == 0xD63F0000:  # a call: the scratch registers are gone
                for r in range(0, 19):
                    page[r] = addr[r] = None
        else:
            # anything else that writes a register forgets what adrp put there (roughly: the common forms)
            if w & 0x1F000000 in (0x0A000000, 0x0B000000, 0x11000000, 0x12000000, 0x13000000, 0x1A000000, 0x1B000000) or w & 0x3B000000 == 0x38000000:
                page[rd] = addr[rd] = None
    for slot in own:  # (the loader's own read of the table entry isn't a use)
        loads[slot] -= 1
    return asked, slot_name, loads


def main():
    exefs = sys.argv[1]
    mods = [x for x in sys.argv[2:] if not x.startswith('--')] or [n for n in ['main'] + [f'subsdk{k}' for k in range(10)] if os.path.isfile(f'{exefs}/{n}')]
    for name in mods:
        mod = Nso(f'{exefs}/{name}')
        asked, slot_name, loads = scan(mod)
        if not asked:
            print(f'{name}: no NVN names asked for')
            continue
        used = {n: loads[s] for s, n in slot_name.items() if loads[s]}
        print(f'{name}: {len(set(asked))} NVN names asked for, {len(slot_name)} kept in globals, {len(used)} of those read by the code')
        groups = collections.Counter()
        for n in used:
            m = re.match(r'nvn([A-Z][a-z]+(?:[A-Z][a-z]+)?)', n)
            g = re.match(r'nvn(CommandBuffer|TextureBuilder|TextureView|Texture|SamplerBuilder|SamplerPool|Sampler|TexturePool|BufferBuilder|Buffer|'
                         r'MemoryPoolBuilder|MemoryPool|DeviceBuilder|Device|QueueBuilder|Queue|WindowBuilder|Window|Program|Sync|EventBuilder|Event|'
                         r'BlendState|ChannelMaskState|ColorState|DepthStencilState|MultisampleState|PolygonState|VertexAttribState|VertexStreamState|'
                         r'DebugDomain|RawStorage|DrawCallback)?', n).group(1) or 'other'
            groups[g] += 1
        print('  by object: ' + ', '.join(f'{g} {c}' for g, c in groups.most_common()))
        if '--list' in sys.argv:
            for n in sorted(used):
                print('   ', n, used[n])


if __name__ == '__main__':
    main()
