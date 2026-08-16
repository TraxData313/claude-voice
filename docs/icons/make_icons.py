"""
Turn a portrait into the round icons the panel and README use.

    python make_icons.py abby C:\\path\\Abby.png --ring 5eead4
    python make_icons.py max  C:\\path\\Max.png  --ring f59e0b --cy 0.30

**Authoring tool only.** It needs Pillow; nothing that runs at speaking time does.
Run it once per voice, commit the PNGs it writes, and the panel just loads them.

Writes <id>-<size>.png for each size into this folder, plus <id>.png as the 256.
"""

import argparse
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = (256, 96, 48, 24)
SS = 4          # supersample factor, so the circle and ring are not jagged


def square_on_face(im, cx, cy, span):
    """Crop a square around a point given in fractions of the image."""
    w, h = im.size
    side = int(min(w, h) * span)
    x, y = int(w * cx), int(h * cy)
    left = max(0, min(w - side, x - side // 2))
    top = max(0, min(h - side, y - side // 2))
    return im.crop((left, top, left + side, top + side))


def round_with_ring(square, size, ring_rgb, ring_px):
    """Circular crop on transparency, with a ring in the voice's colour."""
    big = size * SS
    face = square.resize((big, big), Image.LANCZOS).convert("RGBA")

    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    face.putalpha(mask)

    if ring_px:
        w = ring_px * SS
        ImageDraw.Draw(face).ellipse(
            (w // 2, w // 2, big - 1 - w // 2, big - 1 - w // 2),
            outline=ring_rgb + (255,), width=w)

    return face.resize((size, size), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("voice_id")
    ap.add_argument("source")
    ap.add_argument("--ring", default="5eead4", help="hex colour of the rim")
    ap.add_argument("--cx", type=float, default=0.50, help="face centre across, 0-1")
    ap.add_argument("--cy", type=float, default=0.33, help="face centre down, 0-1")
    ap.add_argument("--span", type=float, default=0.62,
                    help="crop width as a fraction of the shorter side")
    a = ap.parse_args()

    rgb = tuple(int(a.ring[i:i + 2], 16) for i in (0, 2, 4))
    src = Image.open(a.source).convert("RGB")
    square = square_on_face(src, a.cx, a.cy, a.span)

    for size in SIZES:
        # A 3px rim on a 256px icon is a hairline at 24px, so scale it.
        ring_px = max(1, round(size * 0.023))
        icon = round_with_ring(square, size, rgb, ring_px)
        icon.save(os.path.join(HERE, f"{a.voice_id}-{size}.png"))
        if size == 256:
            icon.save(os.path.join(HERE, f"{a.voice_id}.png"))
        print(f"  wrote {a.voice_id}-{size}.png")


if __name__ == "__main__":
    main()
