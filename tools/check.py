"""What a game asks of the port, read from its modules before anything is built or copied.

  python tools/check.py <exefs folder>      (main, sdk, subsdk0..: what tools/nsp.py extracts)

It says the SDK the game was made with, how it draws and plays sound, how it starts, and which of the system's services it uses,
and for each whether the port has run a game like it. Nothing here is certain: a game is only known to work once it has run.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edition import EDITION  # noqa: E402
from nso import Nso  # noqa: E402
import nvn_use  # noqa: E402

BOTH = EDITION != 'switch'  # (what a Switch 2 would make of the game is said too: not in nx-relink, which is a Switch's alone)

HERE = os.path.dirname(os.path.abspath(__file__))


def nvn_report(mods):
    """What a game that draws with NVN needs of the port's own NVN (a Switch 2's): the lines to say, and what to watch for."""
    lines, warn = [], []
    used = set()
    for mod in mods.values():
        asked, slot_name, loads = nvn_use.scan(mod)
        used |= {n for s, n in slot_name.items() if loads[s] > 0}
    have = set(re.findall(r'"(nvn[A-Z]\w+)"', open(os.path.join(HERE, '..', 'runtime', 'nvn2.c'), encoding='utf-8').read()))
    missing = sorted(used - have)
    main = mods['main']
    text = len(re.findall(rb'#version \d', main.image()))
    compiles = any(s[0] == 'glslcCompile' for s in main.symbols() if not s[2])
    lines.append(f"  it uses {len(used)} of NVN's functions; the port's own NVN (for a Switch 2) has all but {len(missing)} of them")
    if compiles and text >= 4:
        lines.append(f"  its shaders are GLSL text ({text} of them), compiled as it runs: the port can use them")
        if missing:
            lines.append('  Switch 2: likely a picture with parts missing; not yet in the port: ' + ', '.join(missing[:12]) + (' ...' if len(missing) > 12 else ''))
            warn.append(f"{len(missing)} NVN functions the port doesn't have yet (a Switch 2 only)")
        else:
            lines.append('  Switch 2: every function it uses is there: it may well run (one such game does)')
    else:
        lines.append("  its shaders come already compiled for the Switch's GPU: the port has no way to run those yet")
        lines.append('  Switch 2: NO PICTURE (it starts, and may play its sound)')
    return lines, warn

# SDKs a game has run with, on a Switch (2026-11-23)
RUN = ('4.4.0', '6.5.0', '9.5.0', '10.4.0', '11.4.0', '16.2.0')
STARTS = {'_ZN2nn4init5StartEmmPFvvES2_': 'nn::init::Start, two functions', '_ZN2nn4init5StartEmmPFvvES2_S2_': 'nn::init::Start, three functions'}
# a service's namespace in the SDK: what it is, and how it has gone
USES = [
    ('aoc', 'add-on content (DLC)', 'served from the card (tools/nsp.py --aoc); run'),
    ('account', 'user accounts', 'the console\'s own; run'),
    ('socket', 'the network (sockets)', 'the console\'s own; present in games that ran, online play itself not tried'),
    ('nifm', 'the network connection', 'the console\'s own; present in games that ran'),
    ('ldn', 'local wireless play', 'not tried'),
    ('friends', 'the friend list', 'present in a game that ran, not tried'),
    ('nfp', 'amiibo', 'present in games that ran, not tried'),
    ('mii', 'Mii characters', 'run'),
    ('swkbd', 'the on-screen keyboard', 'present in games that ran, not tried'),
    ('web', 'the web applet', 'not tried'),
    ('bcat', 'downloaded news data', 'present in a game that ran'),
    ('irsensor', 'the IR camera', 'not tried'),
    ('album', 'screenshots to the album', 'not tried'),
    ('ro', 'modules loaded at run time (NRO)', 'imported by a game that ran without loading any; one that does load them hasn\'t been tried'),
    ('nex', 'online play (NEX)', 'not tried'),
]


def check(exefs):
    names = ['main'] + [f'subsdk{k}' for k in range(10)] + ['sdk']
    mods = {n: Nso(f'{exefs}/{n}') for n in names if os.path.isfile(f'{exefs}/{n}')}
    if 'main' not in mods or 'sdk' not in mods:
        sys.exit(f'{exefs}: no main and sdk there: it must be a game\'s ExeFS folder.')
    lines, warn = [], []
    image = mods['sdk'].image()
    m = re.search(rb'NintendoSdk_nnSdk-(\d+)_(\d+)_(\d+)', image)
    sdk = '.'.join(g.decode() for g in m.groups()) if m else None
    if sdk in RUN:
        lines.append(f'SDK {sdk}: a game with this SDK has run')
    elif sdk:
        near = min(RUN, key=lambda v: abs(int(v.split('.')[0]) - int(sdk.split('.')[0])))
        lines.append(f'SDK {sdk}: none with this one has run yet (the nearest that has: {near})')
        warn.append('an SDK no game has run with: it may differ in a way the port hasn\'t met')
    else:
        lines.append('SDK: its version isn\'t named in the sdk module')
        warn.append('the SDK\'s version is unknown')
    defined = {s[0] for s in mods['sdk'].symbols() if s[2]}
    start = next((v for k, v in STARTS.items() if k in defined), None)
    lines.append(f'start: {start}' if start else 'start: the SDK has no nn::init::Start: the older, step by step start is used')
    if not start:
        warn.append('the step by step start has only been run with a test program')
    # (the game's own module's imports: its other modules are libraries it came with, which import more than the game uses)
    imports = {s[0] for s in mods['main'].symbols() if not s[2] and s[0]}
    nvn = 'nvnBootstrapLoader' in imports
    egl = any(i.startswith('egl') for i in imports)
    vk = any(i.startswith('vk') and i[2:3].isupper() for i in imports)
    how = [x for x, on in (('NVN', nvn), ('OpenGL (EGL)', egl), ('Vulkan', vk)) if on]
    lines.append('picture: ' + (', '.join(how) or 'no graphics library found among its imports'))
    if nvn:
        lines.append("  the console's own driver; run" if not BOTH else "  Switch: the console's own driver; run")
        if BOTH:
            more, more_warn = nvn_report(mods)
            lines += more
            warn += more_warn
    if egl and not nvn:
        lines.append('  run' if not BOTH else '  Switch and Switch 2: run (on a Switch 2 through the host\'s OpenGL)')
    if vk:
        warn.append('Vulkan: no game using it has been tried')
    renderer = any('OpenAudioRenderer' in i for i in imports)
    out = any('OpenAudioOut' in i or 'OpenDefaultAudioOut' in i for i in imports)
    lines.append('sound: ' + (', '.join(x for x, on in (('the audio renderer', renderer), ('audio out', out)) if on) or 'none found')
                 + ('' if not (renderer or out) else '; run' if not BOTH else '; Switch: run. Switch 2: through the console\'s own audio service; run'))
    spaces = set()
    for i in imports:
        k = re.match(r'_ZNK?2nn(\d+)([a-z0-9]+)', i)
        if k:
            spaces.add(k.group(2)[:int(k.group(1))])
    for key, what, state in USES:
        if key in spaces:
            lines.append(f'uses {what}: {state}')
            if state.startswith('NOT'):
                warn.append(f'{what}: not handled')
    if any('MountSaveData' in i or 'MountDeviceSaveData' in i or 'EnsureSaveData' in i for i in imports):
        lines.append('saves: kept in a folder of the card (save/, in the port\'s folder); run')
    if any('MountCacheStorage' in i for i in imports):
        lines.append('uses cache storage: not tried')
    size = sum(len(mod.image()) for mod in mods.values()) >> 20
    lines.append(f'code and data: {size} MB in {len(mods)} modules ({", ".join(mods)}); they come out of the memory the game has')
    return lines, warn


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    lines, warn = check(sys.argv[1].replace('\\', '/'))
    for line in lines:
        print(line)
    print()
    if warn:
        print('To watch for:')
        for w in warn:
            print('  - ' + w)
    else:
        print('Nothing here that a game hasn\'t already run with, on a Switch.')


if __name__ == '__main__':
    main()
