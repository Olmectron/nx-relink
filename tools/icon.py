"""A port's icon made the shape the homebrew menu shows: a JPEG of 256 by 256, from any picture.

    fit(picture)  ->  the JPEG's bytes        (picture: a file's path, or the bytes of one)

The middle of the picture is taken, as wide as the picture is tall (or the reverse), and made 256 by 256. A game's own icon from
a Switch's control data is that already; a Switch 2's is larger (the console's own menu shows it larger), and a picture of the
user's own (a screenshot of the game's title, say) is any shape. Needs the Python package Pillow; without it a picture that is a
JPEG already is passed on as it is, and any other is refused.
"""
import io


def fit(picture):
    data = open(picture, 'rb').read() if isinstance(picture, str) else bytes(picture)
    try:
        from PIL import Image
    except ImportError:
        if data[:2] == bytes([0xFF, 0xD8]):
            return data
        raise SystemExit('The icon isn\'t a JPEG, and making one needs the Python package Pillow:  python -m pip install Pillow')
    im = Image.open(io.BytesIO(data)).convert('RGB')
    w, h = im.size
    side = min(w, h)
    im = im.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side)).resize((256, 256), Image.LANCZOS)
    for quality in (92, 85, 75, 60):  # (the menu's place for it is 128 KB)
        out = io.BytesIO()
        im.save(out, 'JPEG', quality=quality)
        if out.tell() <= 0x20000:
            break
    return out.getvalue()
