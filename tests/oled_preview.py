"""OLED bitmap preview tool — design icons / faces / splashes for the
SSD1306 OLED without flashing the ESP-32 each iteration.

Workflow:
    1. Tweak the cat-face parameters near the top of this file.
    2. Run:   .venv/Scripts/python.exe tests/oled_preview.py
    3. Open  tests/_oled_preview.png   to see the result at 8x scale.
    4. ASCII preview also prints to stdout (down-sampled 2x so it fits).
    5. When the design is locked, copy the printed C-array snippet into
       firmware (e.g. firmware/main/cat_face.h) and call a future
       oled_draw_bitmap() to blit it into the framebuffer.

The 1-bit-per-pixel layout matches firmware/main/oled.c:s_fb exactly —
8 pages of 128 bytes, bit 0 = top pixel, bit 7 = bottom pixel of each
8-row page. So the bytes this script emits are byte-for-byte the same
data the OLED's draw_bitmap call will eat.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit(
        "Install Pillow:  .venv/Scripts/python.exe -m pip install Pillow"
    )

OLED_W = 128
OLED_H = 64
SCALE = 8  # PNG zoom — 1 OLED pixel becomes an SCALExSCALE block.

# ---------------------------------------------------------------------------
# Cat-face parameters — edit these and re-run the script.
# ---------------------------------------------------------------------------

# Eyes — two filled discs.
EYE_RADIUS    = 12
EYE_Y         = 26          # vertical center of both eyes
LEFT_EYE_X    = 38          # horizontal center of left eye
RIGHT_EYE_X   = 90          # horizontal center of right eye

# Mouth — small filled rectangle, horizontally centered.
MOUTH_W       = 5
MOUTH_H       = 3
MOUTH_Y       = 50          # top of mouth

# ---------------------------------------------------------------------------


def make_cat_face() -> Image.Image:
    """Render the parametric face into a 128x64 1-bit image."""
    img = Image.new("1", (OLED_W, OLED_H), color=0)
    draw = ImageDraw.Draw(img)

    for cx in (LEFT_EYE_X, RIGHT_EYE_X):
        draw.ellipse(
            (cx - EYE_RADIUS, EYE_Y - EYE_RADIUS,
             cx + EYE_RADIUS, EYE_Y + EYE_RADIUS),
            fill=1,
        )

    mx = OLED_W // 2
    draw.rectangle(
        (mx - MOUTH_W // 2, MOUTH_Y,
         mx + MOUTH_W // 2, MOUTH_Y + MOUTH_H - 1),
        fill=1,
    )

    return img


def save_png(img: Image.Image, path: Path) -> None:
    big = img.resize((OLED_W * SCALE, OLED_H * SCALE), Image.NEAREST)
    big.save(path)


def print_ascii(img: Image.Image, *, downsample: int = 2) -> None:
    """Print the bitmap as ASCII. Down-samples by `downsample` so the
    width fits a typical terminal — 2x → 64 chars wide."""
    px = img.load()
    for y in range(0, OLED_H, downsample):
        row = []
        for x in range(0, OLED_W, downsample):
            # OR the cells in the down-sampling box so any lit pixel
            # contributes — better for thin strokes.
            on = any(
                px[x + dx, y + dy]
                for dx in range(downsample)
                for dy in range(downsample)
                if x + dx < OLED_W and y + dy < OLED_H
            )
            row.append("#" if on else ".")
        print("".join(row))


def to_c_array(img: Image.Image, name: str) -> str:
    """Pack as SSD1306 horizontal-addressing bytes (page layout matching
    s_fb in firmware/main/oled.c)."""
    px = img.load()
    out: list[int] = []
    pages = OLED_H // 8
    for page in range(pages):
        for x in range(OLED_W):
            b = 0
            for bit in range(8):
                if px[x, page * 8 + bit]:
                    b |= 1 << bit
            out.append(b)

    lines = [f"static const uint8_t {name}[{len(out)}] = {{"]
    for i in range(0, len(out), 16):
        chunk = out[i:i + 16]
        lines.append("    " + ", ".join(f"0x{b:02X}" for b in chunk) + ",")
    lines.append("};")
    return "\n".join(lines)


def main() -> None:
    img = make_cat_face()

    out_path = Path(__file__).resolve().parent / "_oled_preview.png"
    save_png(img, out_path)
    print(f"Wrote {out_path.name}  "
          f"({OLED_W * SCALE}x{OLED_H * SCALE} px, scale {SCALE}x)")
    print()

    print(f"ASCII preview (down-sampled 2x to fit, "
          f"{OLED_W // 2}x{OLED_H // 2}):")
    print_ascii(img, downsample=2)
    print()

    print("C array (paste into firmware/main/cat_face.h):")
    print(to_c_array(img, "cat_face"))


if __name__ == "__main__":
    main()
