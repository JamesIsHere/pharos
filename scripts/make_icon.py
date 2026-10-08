"""Draw the lighthouse icon the desktop shortcut uses (scripts/pharos.ico).

There is no lighthouse emoji to borrow, so it is drawn here. Run once, commit
the .ico; Pillow is needed only for this, so it is not a project dependency:

    uv run --with pillow python scripts/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024                       # drawn large, downsampled per icon size
NAVY, BEAM, WHITE, RED, DARK = "#14213d", "#ffd166", "#f8f9fa", "#d62828", "#0b1326"


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=180, fill=NAVY)

    lamp_y = 300
    # beams first, so the lantern sits on top of them
    d.polygon([(512, lamp_y), (40, lamp_y - 170), (40, lamp_y + 110)], fill=BEAM)
    d.polygon([(512, lamp_y), (984, lamp_y - 170), (984, lamp_y + 110)], fill=BEAM)

    # tapered tower in three bands, red-white-red from the base up
    top, base, half_top, half_base = 400, 900, 120, 200
    bands = [(RED, 0.0, 1 / 3), (WHITE, 1 / 3, 2 / 3), (RED, 2 / 3, 1.0)]
    for color, lo, hi in bands:
        y_hi = base - (base - top) * hi
        y_lo = base - (base - top) * lo
        w_hi = half_base - (half_base - half_top) * hi
        w_lo = half_base - (half_base - half_top) * lo
        d.polygon([(512 - w_lo, y_lo), (512 + w_lo, y_lo), (512 + w_hi, y_hi), (512 - w_hi, y_hi)], fill=color)

    d.rectangle((512 - 160, top - 30, 512 + 160, top), fill=DARK)               # gallery
    d.rectangle((512 - 95, lamp_y - 70, 512 + 95, top - 30), fill=BEAM)         # lantern glass
    d.rectangle((512 - 95, lamp_y - 70, 512 + 95, lamp_y - 50), fill=DARK)
    d.polygon([(512 - 120, lamp_y - 70), (512 + 120, lamp_y - 70), (512, lamp_y - 190)], fill=RED)  # roof
    d.rectangle((40, base, S - 40, base + 50), fill=DARK)                        # rock
    return img


if __name__ == "__main__":
    out = Path(__file__).with_name("pharos.ico")
    draw().save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    draw().resize((256, 256), Image.LANCZOS).save(Path(__file__).parents[1] / "scratch" / "pharos-icon-preview.png")
    print(f"wrote {out}")
