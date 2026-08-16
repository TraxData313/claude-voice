"""
Turn a full illustration into the copies the panel draws along its bottom.

    python make_art.py abby ..\\original_Abby_and_Max_generated_pics\\Abby.png

**Authoring tool only.** It needs Pillow; nothing that runs at speaking time
does. Run it once, commit the PNGs, and the panel just loads them.

Two widths, and PNG, for reasons that are not arbitrary:

- Tk reads PNG, GIF and little else. The .jpg beside these is for the README,
  and the panel cannot open it at all.
- Tk also scales by whole numbers only -- zoom multiplies, subsample divides --
  so the sizes it can draw are the ones it can reach from a file it has. 384
  and 640 between them reach roughly every 40 pixels across the range a panel
  is ever that wide, which is close enough that the odd few pixels are clipped
  at the sides rather than left as a margin.
"""

import argparse
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
WIDTHS = (384, 640)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("voice_id")
    ap.add_argument("source")
    a = ap.parse_args()

    src = Image.open(a.source).convert("RGB")
    for width in WIDTHS:
        height = round(src.height * width / src.width)
        out = os.path.join(HERE, f"{a.voice_id}-{width}.png")
        src.resize((width, height), Image.LANCZOS).save(out, optimize=True)
        print(f"  wrote {a.voice_id}-{width}.png  ({width}x{height}, "
              f"{os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
