"""The files of a RomFS image, by name.

  python tools/romfs.py <romfs.bin> [text]       the files (those whose path has the text in it), with their sizes

files(image) gives (path, offset in the image, size) of every file. The image's header: 64 bit words; the directory table is at
[3] (size [4]), the file table at [7] (size [8]), the files' data at [9]. A directory entry: parent, sibling, first child
directory, first file, hash link, name length, name. A file entry: parent, sibling, data offset (64), size (64), hash link, name
length, name. 0xFFFFFFFF ends a chain.
"""
import struct
import sys


def files(mm):
    head = struct.unpack_from('<10Q', mm, 0)
    dirs, dirs_size, ftab, ftab_size, data = head[3], head[4], head[7], head[8], head[9]
    out = []
    stack = [(0, '')]
    while stack:
        at, path = stack.pop()
        if at + 24 > dirs_size:
            continue
        parent, sibling, child, first, link, n = struct.unpack_from('<6I', mm, dirs + at)
        name = bytes(mm[dirs + at + 24:dirs + at + 24 + n]).decode('utf-8', 'replace')
        here = f'{path}/{name}' if name else path
        if sibling != 0xFFFFFFFF:
            stack.append((sibling, path))
        if child != 0xFFFFFFFF:
            stack.append((child, here))
        f = first
        while f != 0xFFFFFFFF and f + 32 <= ftab_size:
            fparent, fsibling, offset, size, flink, fn = struct.unpack_from('<IIQQII', mm, ftab + f)
            fname = bytes(mm[ftab + f + 32:ftab + f + 32 + fn]).decode('utf-8', 'replace')
            out.append((f'{here}/{fname}', data + offset, size))
            f = fsibling
    return out


def main():
    import collections
    import mmap
    import os
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    with open(sys.argv[1], 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        listing = files(mm)
        want = sys.argv[2].lower() if len(sys.argv) > 2 else None
        kinds = collections.Counter()
        for path, at, size in sorted(listing):
            kinds[os.path.splitext(path)[1].lower()] += size
            if want and want in path.lower():
                print(f'{size:12d}  {path}')
        print(f'{len(listing)} files; by ending (MB): ' + ', '.join(f'{k or "(none)"} {v >> 20}' for k, v in kinds.most_common(14)))


if __name__ == '__main__':
    main()
