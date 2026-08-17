"""Draw docs/banner.svg -- the picture at the top of the README.

An authoring tool, like docs/icons/make_icons.py: run it by hand when the
banner should change, and commit what it writes. Nothing at speaking time
imports it. Unlike make_icons it needs no Pillow, only the standard library.

    python docs/make_banner.py

The portrait and the garden are embedded in the SVG rather than linked. GitHub
serves an SVG in a README through an <img>, and an <img> loads no external
references at all -- a linked picture would simply be a hole in the banner.
That is also the whole reason this file exists: GitHub strips every scrap of
CSS out of a README, so the banner is the only surface on the repository page
we get to paint. Whatever the garden is to do there, it has to do from in here.

Embedding costs what it costs. The two together take the file to a little over
200 KB, which is why the garden is written at 1240px for an 820px banner rather
than a full 2x, and at quality 72: it sits under a scrim with a speech bubble
over the middle of it, and none of that survives being seen.

docs/art/garden-banner.jpg is cut from docs/art/garden.jpg, which is the same
painting the docs page uses as its background. Recut it with:

    python -c "from PIL import Image; im = Image.open('art/garden.jpg'); \
      im.crop((0, 200, 1600, 200 + round(1600 / 3.5))).resize((1240, 354), \
      Image.LANCZOS).save('art/garden-banner.jpg', quality=72, optimize=True)"
"""

import base64
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PORTRAIT = os.path.join(HERE, "icons", "abby-256.png")
GARDEN = os.path.join(HERE, "art", "garden-banner.jpg")
OUT = os.path.join(HERE, "banner.svg")

W, H = 820, 240
INK = "#12212e"          # the outline on everything, as a cartoon has
PAPER = "#fdfaf3"
TEAL = "#57cfc0"
DEEP = "#0e2a3a"
SKY = "#173d52"


def main():
    with open(PORTRAIT, "rb") as fh:
        portrait = base64.b64encode(fh.read()).decode("ascii")
    with open(GARDEN, "rb") as fh:
        garden = base64.b64encode(fh.read()).decode("ascii")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"
     width="{W}" height="{H}" role="img"
     aria-label="claude-voice: Claude Code, out loud, locally. Abby speaking.">
  <defs>
    <!-- The gradient that used to be the background, now the veil over it:
         same two colours on the same diagonal, simply no longer opaque. The
         white bubble and the teal type both have to stay readable against
         whatever part of the painting they happen to land on. -->
    <linearGradient id="sky" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{SKY}" stop-opacity="0.58"/>
      <stop offset="100%" stop-color="{DEEP}" stop-opacity="0.88"/>
    </linearGradient>
    <radialGradient id="glow">
      <stop offset="60%" stop-color="{TEAL}" stop-opacity="0.20"/>
      <stop offset="100%" stop-color="{TEAL}" stop-opacity="0"/>
    </radialGradient>
    <clipPath id="frame">
      <rect x="4" y="4" width="{W - 8}" height="{H - 8}" rx="30"/>
    </clipPath>
  </defs>

  <!-- The garden, taken in to the frame's rounded corners. The stroke is drawn
       after and outside the clip, because a clipped stroke loses its outer half
       and the outline round everything here is the one thing holding the
       cartoon together. -->
  <g clip-path="url(#frame)">
    <image x="4" y="4" width="{W - 8}" height="{H - 8}"
           preserveAspectRatio="xMidYMid slice"
           href="data:image/jpeg;base64,{garden}"/>
    <rect x="4" y="4" width="{W - 8}" height="{H - 8}" fill="url(#sky)"/>
  </g>
  <rect x="4" y="4" width="{W - 8}" height="{H - 8}" rx="30"
        fill="none" stroke="{INK}" stroke-width="5"/>

  <!-- Abby, who is the face of this. The portrait brings its own rim, so it is
       drawn whole rather than clipped into another one. -->
  <circle cx="126" cy="110" r="94" fill="url(#glow)"/>
  <image x="46" y="30" width="160" height="160" href="data:image/png;base64,{portrait}"/>
  <text x="126" y="219" text-anchor="middle" font-size="21" font-weight="bold"
        font-family="Verdana, 'Trebuchet MS', sans-serif" fill="{PAPER}"
        stroke="{INK}" stroke-width="4" paint-order="stroke"
        stroke-linejoin="round">Abby</text>

  <!-- what she is saying -->
  <g>
    <path d="M214 118 L252 94 L252 142 Z" fill="{PAPER}" stroke="{INK}" stroke-width="5"
          stroke-linejoin="round"/>
    <rect x="248" y="40" width="484" height="126" rx="28"
          fill="{PAPER}" stroke="{INK}" stroke-width="5"/>
    <text x="280" y="102" font-family="Verdana, 'Trebuchet MS', sans-serif"
          font-size="46" font-weight="bold" fill="{INK}" letter-spacing="-1">claude-voice</text>
    <text x="284" y="138" font-family="Verdana, 'Trebuchet MS', sans-serif"
          font-size="18" fill="#4b6a7d">Claude Code, out loud. Locally.</text>
    <g>
      <rect x="586" y="112" width="122" height="34" rx="12"
            fill="{DEEP}" stroke="{INK}" stroke-width="3"/>
      <text x="602" y="135" font-family="Consolas, 'Courier New', monospace"
            font-size="16" fill="{TEAL}">/voice on</text>
    </g>
  </g>

  <!-- little sound waves off the bubble, so it reads as speech and not a sign -->
  <g fill="none" stroke="{TEAL}" stroke-width="5" stroke-linecap="round" opacity="0.85">
    <path d="M748 86 q14 34 0 68"/>
    <path d="M770 72 q20 48 0 96"/>
  </g>

  <text x="258" y="200" font-family="Verdana, 'Trebuchet MS', sans-serif"
        font-size="15" fill="{TEAL}" opacity="0.9">no cloud
    <tspan fill="{PAPER}" opacity="0.45"> · </tspan>no API key<tspan fill="{PAPER}"
      opacity="0.45"> · </tspan>nothing leaves the machine</text>
</svg>
"""
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote {OUT} ({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    main()
