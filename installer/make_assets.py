"""
make_assets.py — generate the installer's branding assets from the VOX logo.

Creates, in the installer/ folder:
  - voxkey.ico          app + installer icon (multi-size), the waveform mark
  - wizard_large.bmp    left banner on the wizard welcome/finish pages
  - wizard_small.bmp    small header icon on interior wizard pages
  - LICENSE.txt         shown on the licence page (also copied to project root)

Everything is derived from the real logo assets committed under whispr/assets/:
  - logo_full.png   the full "VOX / VOICE ZONE" lockup (black bg, white art)
  - logo_white.png  same art, white on transparent
  - mark_white.png  just the waveform mark, white on transparent

Brand palette taken from the logo: true black background, pure white art.
Requires Pillow (installed by the build script).
"""
from __future__ import annotations
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ASSETS = ROOT / "whispr" / "assets"

# Brand palette — straight from the logo
BLACK = (11, 11, 13)
WHITE = (252, 253, 253)


def _load(name: str):
    from PIL import Image
    p = ASSETS / name
    if p.exists():
        return Image.open(p).convert("RGBA")
    return None


def _mark_on_black(size: int):
    """The waveform mark, white, centred on a rounded black tile."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # rounded black tile
    tile = Image.new("RGBA", (size, size), BLACK + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.24), fill=255)
    img.paste(tile, (0, 0), mask)
    # paste the mark
    mark = _load("mark_white.png")
    if mark is None:
        # fallback: draw simple bars
        d = ImageDraw.Draw(img)
        cy = size / 2
        bw = max(2, int(size * 0.05))
        for i, h in enumerate([0.18, 0.36, 0.54, 0.36, 0.18]):
            x = int(size * (0.30 + i * 0.10))
            half = size * h / 2
            d.rounded_rectangle([x - bw // 2, int(cy - half), x + bw // 2,
                                 int(cy + half)], radius=bw // 2, fill=WHITE + (255,))
        return img
    m = int(size * 0.62)
    mk = mark.resize((m, m), Image.LANCZOS)
    img.paste(mk, ((size - m) // 2, (size - m) // 2), mk)
    return img


def make_icon():
    from PIL import Image  # noqa
    base = _mark_on_black(256)
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ASSETS.mkdir(parents=True, exist_ok=True)
    base.save(HERE / "voxkey.ico", sizes=sizes)
    base.save(ASSETS / "voxkey.ico", sizes=sizes)
    print("  voxkey.ico")


def make_wizard_images():
    from PIL import Image
    # Large banner 164x314: black bg with the full white lockup
    large = Image.new("RGB", (164, 314), BLACK)
    logo = _load("logo_white.png")
    if logo:
        lw = 140
        ratio = logo.size[1] / logo.size[0]
        lh = int(lw * ratio)
        lg = logo.resize((lw, lh), Image.LANCZOS)
        large.paste(lg, (12, (314 - lh) // 2), lg)
    large.save(HERE / "wizard_large.bmp")

    # Small header 55x58: just the mark, white on black
    small = _mark_on_black(58).convert("RGB")
    small = small.resize((55, 58), Image.LANCZOS)
    small.save(HERE / "wizard_small.bmp")
    print("  wizard_large.bmp, wizard_small.bmp")


def make_license():
    text = """VoxKey (VOX Voice Zone) - End User Notice

VoxKey is a local speech-to-text dictation tool. All speech recognition
runs on your own computer; no audio or transcript data is sent anywhere.

This software is provided "as is", without warranty of any kind. You may
use it for personal and commercial purposes.

Speech recognition is powered by the open-source Whisper model family via
faster-whisper (MIT-licensed). By installing, you agree to use the software
responsibly and at your own risk.
"""
    (HERE / "LICENSE.txt").write_text(text, encoding="utf-8")
    (ROOT / "LICENSE.txt").write_text(text, encoding="utf-8")
    print("  LICENSE.txt")


def main():
    try:
        import PIL  # noqa
    except ImportError:
        print("Pillow not installed; run: pip install pillow")
        raise SystemExit(1)
    print("Generating branding assets from the VOX logo...")
    make_icon()
    make_wizard_images()
    make_license()


if __name__ == "__main__":
    main()
