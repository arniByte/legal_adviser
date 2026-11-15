"""Background art for the deck in PSB colours, generated with numpy and Pillow.

    python3 presentation/art.py        # writes presentation/img/bg-*.jpg and art-fluted-dark.jpg

One dark background per slide, four families in PSB colours:
rays    — luminous symmetric light rays on deep indigo;
ribbons — glowing silk ribbons from violet into orange;
glow    — soft airbrushed light behind frosted glass;
fluted  — glowing blobs seen through vertical ribbed glass.
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


def ribbons(w, h, seed=5, centre=0.75, warm=0.5):
    """Luminous silk ribbons on deep indigo: a few sine curves glowing violet into orange."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xx / w, yy / h
    light = np.zeros_like(u)
    hue = np.zeros_like(u)
    for k in range(5):
        amp, freq, phase = 0.10 + 0.05 * rng.random(), 1.2 + rng.random() * 1.4, rng.random() * 6.28
        base = 0.35 + 0.12 * k + 0.05 * rng.random()
        curve = base + amp * np.sin(2 * np.pi * (u * freq) + phase) * (0.4 + u)
        d = np.abs(v - curve)
        width = 0.010 + 0.025 * u * (0.6 + rng.random())
        glow = np.exp(-(d / width) ** 2) * 0.9 + np.exp(-(d / (width * 6)) ** 2) * 0.35
        fade = np.exp(-((u - centre) / 0.45) ** 2)
        light += glow * fade
        hue += glow * fade * (k / 4)
    hue = hue / (light + 1e-4)
    light = 1 - np.exp(-light * 1.4)
    cold = np.array([[10, 8, 34], [40, 32, 130], [96, 84, 236], [190, 180, 255], [255, 255, 255]], np.float32)
    hot = np.array([[10, 8, 34], [70, 26, 70], [242, 107, 33], [255, 190, 140], [255, 250, 240]], np.float32)
    pos = np.array([0, 0.3, 0.6, 0.85, 1.0], np.float32)
    mix = np.clip(hue * warm * 1.6, 0, 1)[..., None]
    c1 = np.stack([np.interp(light, pos, cold[:, c]) for c in range(3)], -1)
    c2 = np.stack([np.interp(light, pos, hot[:, c]) for c in range(3)], -1)
    img = c1 * (1 - mix) + c2 * mix
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))
    return grain(np.asarray(pil).astype(np.float32), rng, 6)


def fluted_dark(w, h, blobs, rib=46, seed=6):
    """Ribbed glass in the dark: glowing blobs on near-black seen through the ribs."""
    return fluted(w, h, blobs, background=(10, 8, 32), rib=rib, seed=seed, blur=40)


def glow(w, h, spots, seed=7):
    """Soft airbrushed glow spots on deep indigo, like light behind frosted glass."""
    rng = np.random.default_rng(seed)
    img = soft_blobs(w, h, spots, (11, 9, 36), 60)
    return grain(img, rng, 6)


def save(img, name, quality=90):
    OUT.mkdir(exist_ok=True)
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(OUT / name, quality=quality, optimize=True)
    print(f"img/{name}")


if __name__ == "__main__":
    # one dark background per slide; the bright part keeps to the right or bottom, away from the text
    W, H = 1600, 900
    DEEP, VIO, LIL, ORA = (40, 32, 130), (96, 84, 236), (170, 160, 255), (242, 107, 33)
    save(rays(W, H, centre=0.78), "bg-01.jpg")
    save(ribbons(W, H, seed=11, centre=0.8, warm=0.6), "bg-02.jpg")
    save(glow(W, H, [(0.88, 0.2, 0.30, VIO, 0.9), (0.7, 0.95, 0.30, ORA, 0.55), (1.0, 0.75, 0.25, LIL, 0.5)], seed=12), "bg-03.jpg")
    save(fluted_dark(W, H, [(0.82, 0.25, 0.30, VIO, 0.95), (0.6, 0.95, 0.28, ORA, 0.8), (1.0, 0.7, 0.22, LIL, 0.7)], seed=13), "bg-04.jpg")
    save(ribbons(W, H, seed=14, centre=0.7, warm=0.2), "bg-05.jpg")
    save(glow(W, H, [(0.62, 0.5, 0.40, VIO, 0.8), (0.95, 0.15, 0.25, ORA, 0.6), (0.35, 1.0, 0.25, LIL, 0.45)], seed=15), "bg-06.jpg")
    save(fluted_dark(W, H, [(0.95, 0.15, 0.30, ORA, 0.8), (0.75, 0.8, 0.34, VIO, 0.9), (0.45, 1.05, 0.2, LIL, 0.6)], rib=40, seed=16), "bg-07.jpg")
    save(glow(W, H, [(0.85, 0.85, 0.35, ORA, 0.65), (0.98, 0.25, 0.30, VIO, 0.85)], seed=17), "bg-08.jpg")
    save(rays(W, H, seed=18, centre=0.92), "bg-09.jpg")
    save(ribbons(W, H, seed=19, centre=0.85, warm=0.9), "bg-10.jpg")
    save(fluted_dark(W, H, [(0.9, 0.4, 0.34, VIO, 0.9), (0.55, 0.0, 0.26, LIL, 0.6), (0.7, 1.0, 0.26, ORA, 0.7)], rib=52, seed=20), "bg-11.jpg")
    save(rays(W, H, seed=21, centre=0.78), "bg-12.jpg")
    save(fluted_dark(1200, 800, [(0.25, 0.3, 0.35, VIO, 0.95), (0.8, 0.75, 0.32, ORA, 0.85), (0.9, 0.1, 0.25, LIL, 0.7)], rib=40, seed=22), "art-fluted-dark.jpg")
