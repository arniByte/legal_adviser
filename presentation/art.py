"""Background art for the deck in PSB colours, generated with numpy and Pillow.

    python3 presentation/art.py        # writes presentation/img/art-*.jpg

fluted — soft airbrushed blobs seen through vertical ribbed glass (light background);
rays   — luminous symmetric light rays on deep indigo (dark background).
Everything is deterministic: the same seed gives the same picture.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

OUT = Path(__file__).resolve().parent / "img"
INDIGO, VIOLET, LILAC, ORANGE, PEACH = (23, 20, 72), (80, 70, 216), (198, 193, 255), (242, 107, 33), (255, 196, 160)


def grain(img: np.ndarray, rng, amount: float) -> np.ndarray:
    return np.clip(img + rng.normal(0, amount, img.shape[:2])[..., None], 0, 255)


def soft_blobs(w, h, blobs, background, blur):
    """Gaussian discs composited over a background, then blurred like airbrush."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.ones((h, w, 3), np.float32) * np.array(background, np.float32)
    for cx, cy, r, color, strength in blobs:
        a = strength * np.exp(-(((xx - cx * w) ** 2 + (yy - cy * h) ** 2) / (r * min(w, h)) ** 2) ** 1.6)
        img = img * (1 - a[..., None]) + np.array(color, np.float32) * a[..., None]
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(pil).astype(np.float32)


def fluted(w, h, blobs, background=(246, 245, 250), rib=34, seed=1, blur=26):
    """Ribbed glass: each rib is a small lens that samples a wider strip behind it."""
    rng = np.random.default_rng(seed)
    base = soft_blobs(w, h, blobs, background, blur)
    x = np.arange(w)
    u = (x % rib) / rib                                   # position inside the rib, 0..1
    src = np.clip((x + (u - 0.5) * rib * 1.7).round().astype(int), 0, w - 1)
    img = base[:, src]
    shade = 1 + 0.07 * np.sin(2 * np.pi * u) - 0.10 * np.exp(-((u - 0.02) / 0.03) ** 2)   # highlight and seam
    img = img * shade[None, :, None]
    # a faint second, finer set of ribs gives the woven sheen of real fluted glass
    img = img * (1 + 0.025 * np.sin(2 * np.pi * x / (rib / 5)))[None, :, None]
    return grain(img, rng, 5.5)


def rays(w, h, seed=2, centre=0.5):
    """Symmetric beams of light from the centre on deep indigo, with a soft horizontal wing."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w * centre, h * 0.47
    dx, dy = (xx - cx) / w, (yy - cy) / h
    ang = np.arctan2(np.abs(dx) * 1.0, dy)                # mirrored around the vertical axis
    r = np.hypot(dx * 1.1, dy)
    light = np.zeros_like(dx)
    for a, width, power in ((0.35, 0.05, 1.0), (0.55, 0.09, 0.8), (1.0, 0.16, 0.9), (1.45, 0.22, 0.75), (2.2, 0.12, 0.7), (2.7, 0.06, 0.9)):
        light += power * np.exp(-((ang - a) / width) ** 2) * np.exp(-r * 1.8)
    light += 1.4 * np.exp(-(dy / 0.07) ** 2) * np.exp(-np.abs(dx) * 2.2)      # the wing
    light += 2.2 * np.exp(-(r / 0.07) ** 2)                                    # the core
    light = 1 - np.exp(-light * 1.25)
    stops = np.array([[12, 10, 40], [38, 30, 120], [80, 70, 216], [160, 150, 255], [255, 228, 214], [255, 255, 255]], np.float32)
    pos = np.array([0, 0.22, 0.45, 0.68, 0.88, 1.0], np.float32)
    img = np.stack([np.interp(light, pos, stops[:, c]) for c in range(3)], -1)
    vign = np.clip(1.15 - np.hypot((xx / w - 0.5) * 1.2, dy * 1.4) * 0.9, 0.55, 1)
    img = img * vign[..., None]
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
    return grain(np.asarray(pil).astype(np.float32), rng, 6)


def save(img, name, quality=90):
    OUT.mkdir(exist_ok=True)
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(OUT / name, quality=quality, optimize=True)
    print(f"img/{name}")


if __name__ == "__main__":
    save(fluted(900, 1800, [(0.35, 0.22, 0.42, INDIGO, 1), (0.85, 0.55, 0.40, VIOLET, 0.95), (0.25, 0.86, 0.34, ORANGE, 0.9),
                            (1.0, 0.05, 0.25, LILAC, 0.8)], rib=40, seed=3), "art-fluted-tall.jpg")
    save(fluted(1600, 900, [(0.18, 0.3, 0.42, VIOLET, 0.9), (0.62, 0.78, 0.38, ORANGE, 0.85), (0.92, 0.2, 0.36, INDIGO, 0.9),
                            (0.45, 0.1, 0.25, LILAC, 0.7)], rib=44, seed=4), "art-fluted-wide.jpg")
    save(rays(1600, 900, centre=0.78), "art-rays.jpg")      # light behind the mascot on the last slide
