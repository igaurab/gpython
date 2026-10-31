"""Show how this terminal places kitty graphics, to debug image output in a new terminal.

    uv run python3 scripts/kitty_probe.py

Draws a row ruler and five small images, each with a label saying where it should be:

  A  red     a=T, transmit and display at the cursor (row 5, col 10)
  B  green   a=t then a=p at the cursor, no cursor keys (row 5, col 40)
  C  blue    what gpython does: DECSC, cursor move, a=p with C=1, DECRC (row 12, col 10)
  D  green   a=p with C=1, then the cursor is moved away afterwards (row 12, col 40)
  E  yellow  a=p with a crop (x, y, w, h) of a yellow|magenta image: only yellow should show (row 19, col 10)

A key press shows page two, images large enough to need several 4 KB chunks, the way a real
figure is sent. The cursor sits at P (row 23, col 60) while they are transmitted:

  F  red      a=t in chunks, then a=p at row 5, col 10
  G  green    a=t in chunks and no placement at all: nothing should show anywhere
  H  blue     a=T in chunks with c and r, cursor at row 5, col 40
  I  yellow   gpython's sequence with the cursor moved first: DECSC, CUP (row 12, col 10), chunked a=t, a=p C=1, DECRC
  J  magenta  chunked a=t, a=p at row 12 col 40, that placement deleted, a=p again at row 19 col 10

Press any key to leave. Take a screenshot of each page and compare with the labels.
"""

from __future__ import annotations

import base64
import io
import sys
import termios
import tty


def png(colors: list[str], width: int = 100, height: int = 50) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), colors[0])
    if len(colors) > 1:
        ImageDraw.Draw(image).rectangle((width // 2, 0, width, height), fill=colors[1])
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def noise(tint: tuple[int, int, int], width: int = 160, height: int = 80) -> bytes:
    """A tinted noisy PNG, too big for one 4 KB chunk."""
    import random

    from PIL import Image

    rng = random.Random(sum(tint))
    pixels = [tuple(min(255, c // 2 + rng.randrange(128)) for c in tint) for _ in range(width * height)]
    image = Image.new("RGB", (width, height))
    image.putdata(pixels)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def g(keys: str, payload: bytes = b"") -> str:
    data = base64.standard_b64encode(payload).decode()
    return f"\x1b_G{keys};{data}\x1b\\" if data else f"\x1b_G{keys}\x1b\\"


def chunked(keys: str, payload: bytes) -> str:
    data = base64.standard_b64encode(payload).decode()
    parts = []
    first = True
    while True:
        chunk, data = data[:4096], data[4096:]
        more = 1 if data else 0
        parts.append(f"\x1b_G{keys},m={more};{chunk}\x1b\\" if first else f"\x1b_Gm={more};{chunk}\x1b\\")
        first = False
        if not more:
            return "".join(parts)


def cup(row: int, col: int) -> str:
    return f"\x1b[{row};{col}H"


def main() -> None:
    red, green, blue, split = png(["red"]), png(["green"]), png(["blue"]), png(["yellow", "magenta"])
    out = ["\x1b[?1049h\x1b[2J\x1b[?25l"]
    for row in range(1, 25):
        out.append(cup(row, 1) + f"{row:2d}")
    out.append(cup(1, 5) + "kitty graphics probe: each box should sit right under its label. Press any key to quit.")
    out.append(cup(4, 10) + "A red (a=T at cursor)" + cup(4, 40) + "B green (a=t, a=p at cursor)")
    out.append(cup(11, 10) + "C blue (DECSC, CUP, a=p C=1, DECRC)" + cup(11, 40) + "D green (a=p C=1, cursor moved after)")
    out.append(cup(18, 10) + "E yellow only (a=p with crop)")

    out.append(cup(5, 10) + g("a=T,f=100,i=1,c=10,r=3,q=2", red))
    out.append(g("a=t,f=100,i=2,q=2", green))
    out.append(cup(5, 40) + g("a=p,i=2,p=1,c=10,r=3,q=2"))
    out.append(g("a=t,f=100,i=3,q=2", blue))
    out.append("\x1b7" + cup(12, 10) + g("a=p,i=3,p=1,c=10,r=3,C=1,q=2") + "\x1b8")
    out.append(cup(12, 40) + g("a=p,i=2,p=2,c=10,r=3,C=1,q=2") + cup(24, 1))
    out.append(g("a=t,f=100,i=4,q=2", split))
    out.append(cup(19, 10) + g("a=p,i=4,p=1,x=0,y=0,w=50,h=50,c=10,r=3,C=1,q=2"))
    out.append(cup(23, 5) + "cursor parked here after the placements")
    sys.stdout.write("".join(out))
    sys.stdout.flush()

    wait_key()
    page_two()
    wait_key()
    sys.stdout.write(g("a=d,d=A,q=2") + "\x1b[?25h\x1b[?1049l")
    sys.stdout.flush()


def page_two() -> None:
    park = cup(23, 60)
    out = [g("a=d,d=A,q=2"), "\x1b[2J"]
    for row in range(1, 25):
        out.append(cup(row, 1) + f"{row:2d}")
    out.append(cup(1, 5) + "page 2, chunked transfers: each box should sit right under its label. Press any key to quit.")
    out.append(cup(4, 10) + "F red (chunked a=t, then a=p)" + cup(4, 40) + "H blue (chunked a=T with c, r)")
    out.append(cup(11, 10) + "I yellow (CUP before the chunks)" + cup(11, 40) + "J magenta must NOT be here")
    out.append(cup(18, 10) + "J magenta (deleted and placed again)" + cup(18, 40) + "G green must not show anywhere")
    out.append(cup(23, 50) + "P: cursor here during transfers")

    out.append(park + chunked("a=t,f=100,i=11,q=2", noise((255, 0, 0))))
    out.append(cup(5, 10) + g("a=p,i=11,p=1,c=10,r=3,q=2"))
    out.append(park + chunked("a=t,f=100,i=12,q=2", noise((0, 255, 0))))
    out.append(cup(5, 40) + chunked("a=T,f=100,i=13,c=10,r=3,q=2", noise((0, 0, 255))))
    out.append(park + "\x1b7" + cup(12, 10) + chunked("a=t,f=100,i=14,q=2", noise((255, 255, 0))))
    out.append(g("a=p,i=14,p=1,c=10,r=3,C=1,q=2") + "\x1b8")
    out.append(park + chunked("a=t,f=100,i=15,q=2", noise((255, 0, 255))))
    out.append(cup(12, 40) + g("a=p,i=15,p=1,c=10,r=3,C=1,q=2"))
    out.append(g("a=d,d=i,i=15,q=2"))
    out.append(cup(19, 10) + g("a=p,i=15,p=1,c=10,r=3,C=1,q=2") + park)
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def wait_key() -> None:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdin.buffer.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
