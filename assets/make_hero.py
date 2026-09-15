"""Draw the repository hero image.

Run with:  python3 assets/make_hero.py

Everything here is drawn from code: a low-resolution canvas scaled up with
nearest-neighbour sampling, so the result is made of real pixel blocks rather
than a resized photograph. No external assets and no model-generated imagery.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, SCALE = 480, 240, 3
VOID = (6, 8, 10)
PANEL = (15, 21, 26)
LINE = (34, 48, 58)
AMBER = (255, 176, 46)
ORANGE = (255, 106, 44)
CYAN = (79, 209, 224)
VIOLET = (176, 131, 240)
GREEN = (87, 217, 138)
DIM = (103, 125, 139)
WHITE = (242, 246, 249)


def hexagon(draw, cx, cy, r, outline, fill=None):
    points = [
        (cx + r * math.cos(math.pi / 6 + i * math.pi / 3),
         cy + r * math.sin(math.pi / 6 + i * math.pi / 3))
        for i in range(6)
    ]
    draw.polygon(points, fill=fill, outline=outline)


def build():
    image = Image.new("RGB", (W, H), VOID)
    draw = ImageDraw.Draw(image)

    # Faint instrument grid.
    for x in range(0, W, 12):
        draw.line((x, 0, x, H), fill=(11, 15, 19))
    for y in range(0, H, 12):
        draw.line((0, y, W, y), fill=(11, 15, 19))

    # Left: the compound eye, as a hex lattice of ommatidia with a few lit.
    lit = {(1, 2), (2, 1), (2, 3), (3, 2), (3, 4), (4, 3), (0, 3), (4, 1), (1, 5)}
    for column in range(6):
        for row in range(7):
            cx = 30 + column * 15
            cy = 74 + row * 17 + (column % 2) * 8
            if (cx - 58) ** 2 / 2.4 + (cy - 142) ** 2 > 5000:
                continue
            on = (column, row % 6) in lit
            hexagon(draw, cx, cy, 8, LINE if not on else AMBER,
                    fill=(18, 26, 33) if not on else (58, 37, 8))
            if on:
                draw.point((cx, cy), fill=AMBER)

    # Middle: the pathway, drawn as nodes and connections.
    stages = [
        (150, 96, CYAN, "lamina"),
        (150, 148, CYAN, None),
        (196, 78, VIOLET, "medulla"),
        (196, 122, VIOLET, None),
        (196, 166, VIOLET, None),
        (243, 100, AMBER, "mushroom body"),
        (243, 146, AMBER, None),
        (288, 123, ORANGE, "dopamine"),
        (330, 100, WHITE, "descending"),
        (330, 150, WHITE, None),
    ]
    links = [(0, 2), (0, 3), (1, 3), (1, 4), (2, 5), (3, 5), (3, 6), (4, 6),
             (5, 7), (6, 7), (5, 8), (6, 9), (7, 8), (7, 9)]
    for a, b in links:
        ax, ay = stages[a][0], stages[a][1]
        bx, by = stages[b][0], stages[b][1]
        draw.line((ax, ay, bx, by), fill=(28, 41, 51))
    for index, (x, y, colour, label) in enumerate(stages):
        draw.rectangle((x - 3, y - 3, x + 3, y + 3), fill=colour)
        draw.rectangle((x - 5, y - 5, x + 5, y + 5), outline=(colour[0] // 3, colour[1] // 3, colour[2] // 3))

    # Right: what comes out, as a terminal block.
    draw.rectangle((354, 84, 452, 176), fill=PANEL, outline=LINE)

    # Wordmark, drawn small and scaled up so the letters are pixel blocks.
    try:
        title = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 34)
        small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11)
    except OSError:
        title = ImageFont.load_default()
        small = ImageFont.load_default()
    draw.text((30, 14), "FLYLAB", font=title, fill=WHITE)
    draw.text((32, 51), "A CONNECTOME WORKFLOW PLATFORM", font=small, fill=AMBER)
    for i, (label, colour) in enumerate([
        ("GPIO", GREEN), ("SERIAL", CYAN), ("SERVO", AMBER), ("POINTER", VIOLET),
    ]):
        y = 94 + i * 20
        draw.rectangle((362, y, 368, y + 6), fill=colour)
        draw.text((376, y - 2), label, font=small, fill=colour)
        draw.line((330, y + 3, 356, y + 3), fill=(30, 44, 54))
    draw.text((34, 204), "166,700 NEURONS   25,582,938 CONNECTIONS   MALECNS V1.0",
              font=small, fill=DIM)
    draw.line((32, 199, 448, 199), fill=LINE)

    # Corner registration marks.
    for x, y in ((8, 8), (W - 9, 8), (8, H - 9), (W - 9, H - 9)):
        draw.line((x - 4, y, x + 4, y), fill=LINE)
        draw.line((x, y - 4, x, y + 4), fill=LINE)

    return image.resize((W * SCALE, H * SCALE), Image.NEAREST)


if __name__ == "__main__":
    target = Path(__file__).with_name("flylab.png")
    build().save(target, optimize=True)
    print(f"wrote {target} ({target.stat().st_size} bytes)")
