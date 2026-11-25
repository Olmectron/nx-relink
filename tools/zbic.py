"""ZBIC: how the parts of some Switch 2 modules are packed (flag 0x80 of an NSO's header, from system version 22; each part begins
with the letters "ZBIC").

It is a Zstandard frame with two things changed, and nothing else: the four bytes it begins with, and the way each table of
probabilities is written. Where Zstandard writes a table's counts one after another in a stream of bits (RFC 8878, 4.1.1), ZBIC
writes their running sums, by binary interpolative coding: the sum at the middle of a range first, as a number between the sums at
its two ends, then the same for each half. Those numbers are the digits of one long number in mixed radix, held in the bytes after
a first one that says how many they are.

So nothing is unpacked here. unpack() writes each table again the standard way, puts Zstandard's own first bytes back, and hands
the frame to the zstandard library.

    python tools/zbic.py <file of one packed part> <size unpacked> [out]

The format as Atmosphere reads it (libstratosphere's zstd, built with ZSTD_ZBIC_SUPPORT) and as kinnay/zbic and ChrisColeTech's
Switch2RECOMP-turbo (zbic.py, MIT) describe it; found on 2026-11-25. Written here from that description.
"""
import sys

MAGIC, ZSTD = b'ZBIC', bytes([0x28, 0xB5, 0x2F, 0xFD])
BLOCK_MOST = 128 << 10
MOST = {'weights': 255, 'literal lengths': 35, 'offsets': 31, 'match lengths': 52}


class NotZbic(ValueError):
    """The data isn't what this reads, or is damaged: said in words."""


class Digits:
    """The table's long number: its bytes are taken from the last to the first, each going in under the ones before it, whenever
    what is held has fewer than 40 bits. A digit is what is left of dividing by its radix."""

    def __init__(self, data, first, count):
        self.data, self.first, self.left, self.value = data, first, count, 0
        self.fill()

    def fill(self):
        while self.left and self.value < 1 << 40:
            self.value = self.value << 8 | self.data[self.first + self.left - 1]
            self.left -= 1

    def digit(self, radix):
        self.value, d = divmod(self.value, radix)
        self.fill()
        return d


def middles(low, high, out):
    """The order the sums are written in: a range's middle, then its upper half's, then its lower half's."""
    if high - low >= 2:
        mid = (low + high) // 2
        out.append((low, mid, high))
        middles(mid, high, out)
        middles(low, mid, out)
    return out


def table(data, at, end, what):
    """A table as ZBIC writes it, at data[at:end]: (each symbol's count as Zstandard has them, -1 for "less than one"; the table's
    log; how many bytes it took)."""
    if at >= end:
        raise NotZbic(f'a table of {what} is cut short')
    more, shifted = data[at] & 0x7F, data[at] >> 7
    if at + 1 + more > end:
        raise NotZbic(f'a table of {what} is cut short')
    d = Digits(data, at + 1, more)
    last_symbol = d.digit(52) + 1  # (the highest symbol that has a count)
    if last_symbol > MOST[what]:
        raise NotZbic(f'a table of {what} has {last_symbol + 1} symbols')
    log = d.digit(8) + 5
    whole = 1 << log
    # shifted: every count is written one higher, so that a "less than one" (-1) can be told from a nought
    total = d.digit(whole) + 1 + (last_symbol + 1 if shifted else 0)
    span = 2
    while span <= last_symbol:
        span *= 2
    sums = [0] * (span + 1)
    sums[span] = total
    for low, mid, high in middles(0, span, []):
        a, b = sums[low], sums[high]
        sums[mid] = a + d.digit(b - a + 1) if a != b else a
    if sums[1]:
        d.digit(sums[1] + 1)  # (one digit more is written, between nought and the first sum: it says nothing)
    counts = [sums[k + 1] - sums[k] - shifted for k in range(last_symbol + 1)]
    if d.left or sum(abs(c) for c in counts) != whole or min(counts) < -1:
        raise NotZbic(f'a table of {what} doesn\'t add up')
    return counts, log, more + 1


def standard(counts, log):
    """The same table as Zstandard writes it (RFC 8878, 4.1.1): the bytes."""
    bits, used = log - 5, 4

    def put(value, n):
        nonlocal bits, used
        bits |= value << used
        used += n

    remaining, threshold, width = (1 << log) + 1, 1 << log, log + 1
    k = 0
    while remaining > 1:
        count = counts[k]
        k += 1
        value, limit = count + 1, 2 * threshold - 1 - remaining
        if value < limit:
            put(value, width - 1)
        elif value < threshold:
            put(value, width)
        else:
            put(value + limit, width)
        remaining -= abs(count)
        while remaining < threshold:
            width -= 1
            threshold >>= 1
        if count == 0:
            # (after a nought: how many more follow it, two bits at a time, a 3 meaning three and more to come)
            run = 0
            while k + run < len(counts) and counts[k + run] == 0:
                run += 1
            k += run
            while run >= 3:
                put(3, 2)
                run -= 3
            put(run, 2)
    return bits.to_bytes((used + 7) // 8, 'little')


SIZES = {0: (3, 10), 1: (3, 10), 2: (4, 14), 3: (5, 18)}  # a packed literals section's header: (its bytes, the bits of each size)


def literals(data, at, end, out):
    """A block's literals, written to out with their tree's table the standard way: where the sequences begin."""
    kind, form = data[at] & 3, data[at] >> 2 & 3
    if kind < 2:  # as they are, or one byte repeated: nothing of ZBIC's in them
        head = 1 if form in (0, 2) else 2 if form == 1 else 3
        size = int.from_bytes(data[at:at + head], 'little') >> (3 if head == 1 else 4)
        stop = at + head + (size if kind == 0 else 1)
        out += data[at:stop]
        return stop
    head, bits = SIZES[form]
    v = int.from_bytes(data[at:at + head], 'little')
    unpacked, packed = v >> 4 & ((1 << bits) - 1), v >> (4 + bits) & ((1 << bits) - 1)
    stop = at + head + packed
    if stop > end:
        raise NotZbic('a block\'s literals are cut short')
    body = data[at + head:stop]
    if kind == 2 and body and body[0] < 128:
        # the tree's weights, packed with a table of their own: the table, then their stream, which stays as it is
        n = body[0]
        counts, log, took = table(body, 1, 1 + n, 'weights')
        weights = standard(counts, log) + body[1 + took:1 + n]
        if len(weights) > 127:
            raise NotZbic('a tree\'s description doesn\'t fit the standard form')
        body = bytes([len(weights)]) + weights + body[1 + n:]
    while form and form < 3 and max(unpacked, len(body)) >> SIZES[form][1]:
        form += 1
    head, bits = SIZES[form]
    if max(unpacked, len(body)) >> bits:
        raise NotZbic('a block\'s literals don\'t fit the standard form')
    out += (kind | form << 2 | unpacked << 4 | len(body) << (4 + bits)).to_bytes(head, 'little')
    out += body
    return stop


def block(data, at, end):
    """A packed block, the standard way."""
    out = bytearray()
    at = literals(data, at, end, out)
    if at >= end:
        raise NotZbic('a block\'s sequences are cut short')
    n = 1 if data[at] < 128 else 2 if data[at] < 255 else 3
    out += data[at:at + n]
    count = data[at]
    at += n
    if count:
        modes = data[at]
        out.append(modes)
        at += 1
        for shift, what in ((6, 'literal lengths'), (4, 'offsets'), (2, 'match lengths')):
            mode = modes >> shift & 3
            if mode == 1:  # one symbol always: its byte
                out.append(data[at])
                at += 1
            elif mode == 2:
                counts, log, took = table(data, at, end, what)
                out += standard(counts, log)
                at += took
    if at > end:
        raise NotZbic('a block\'s sequences are cut short')
    out += data[at:end]
    return out


def as_zstd(data):
    """ZBIC frames, as the Zstandard frames they stand for."""
    data = bytes(data)
    out, at = bytearray(), 0
    while at < len(data):
        if data[at:at + 4] != MAGIC:
            raise NotZbic(f'no ZBIC frame at byte {at}')
        flags = data[at + 4]
        one_segment = flags >> 5 & 1
        head = 1 + (0 if one_segment else 1) + (0, 1, 2, 4)[flags & 3] + (1 if one_segment else 0, 2, 4, 8)[flags >> 6]
        out += ZSTD + data[at + 4:at + 4 + head]
        at += 4 + head
        while True:
            if at + 3 > len(data):
                raise NotZbic('the frame is cut short')
            h = int.from_bytes(data[at:at + 3], 'little')
            last, kind, size = h & 1, h >> 1 & 3, h >> 3
            n = 1 if kind == 1 else size
            if kind == 3 or at + 3 + n > len(data):
                raise NotZbic('the frame is cut short, or damaged')
            if kind == 2:
                body = block(data, at + 3, at + 3 + size)
                if len(body) > BLOCK_MOST:
                    raise NotZbic('a block doesn\'t fit the standard form')
                out += (len(body) << 3 | 2 << 1 | last).to_bytes(3, 'little') + body
            else:
                out += data[at:at + 3 + n]
            at += 3 + n
            if last:
                break
        if flags & 4:  # the sum of what is unpacked: the same either way
            out += data[at:at + 4]
            at += 4
    return bytes(out)


def unpack(data, size):
    """A part packed as ZBIC, unpacked: size bytes. Needs the zstandard package."""
    try:
        import zstandard
    except ImportError:
        raise NotZbic('the Python package "zstandard" is needed to unpack it:  python -m pip install zstandard') from None
    frames = as_zstd(data)
    try:
        out = zstandard.ZstdDecompressor(max_window_size=1 << 31).decompress(frames, max_output_size=size)
    except zstandard.ZstdError as e:
        raise NotZbic(f'it can\'t be unpacked ({e})') from None
    if len(out) != size:
        raise NotZbic(f'it unpacks to {len(out)} bytes, not the {size} its header says')
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out = unpack(open(sys.argv[1], 'rb').read(), int(sys.argv[2], 0))
    print(f'{len(out)} bytes')
    if len(sys.argv) > 3:
        open(sys.argv[3], 'wb').write(out)


if __name__ == '__main__':
    main()
