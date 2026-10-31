"""Show how this terminal places kitty graphics, to debug image output in a new terminal.

    uv run python3 scripts/kitty_probe.py

Draws a row ruler and five small images, each with a label saying where it should be:

  A  red     a=T, transmit and display at the cursor (row 5, col 10)
  B  green   a=t then a=p at the cursor, no cursor keys (row 5, col 40)
  C  blue    what gpython does: DECSC, cursor move, a=p with C=1, DECRC (row 12, col 10)
  D  green   a=p with C=1, then the cursor is moved away afterwards (row 12, col 40)
  E  yellow  a=p with a crop (x, y, w, h) of a yellow|magenta image: only yellow should show (row 19, col 10)

Press any key to leave. Take a screenshot before that and compare with the labels.
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


def g(keys: str, payload: bytes = b"") -> str:
    data = base64.standard_b64encode(payload).decode()
    return f"\x1b_G{keys};{data}\x1b\\" if data else f"\x1b_G{keys}\x1b\\"


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

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdin.buffer.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write(g("a=d,d=A,q=2") + "\x1b[?25h\x1b[?1049l")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
