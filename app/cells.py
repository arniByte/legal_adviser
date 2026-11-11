"""Layout of the knowledge base as a cluster of cells: one cell per document.

Cell area follows the document size (log scale), company documents and laws form two
neighbouring groups. Positions come from a short deterministic relaxation; each cell's
outline is its circle clipped by the power-diagram bisectors with its neighbours, which
gives the packed, membrane-like look. Pure Python, no randomness: the same knowledge base
always produces the same picture.
"""

from __future__ import annotations

import math

VIEW = 1000.0          # layout is computed in a VIEW x VIEW box
GAP = 7.0              # gap between neighbouring cells


def _radii(sizes: list) -> list:
    logs = [math.log(max(s, 300)) for s in sizes]
    lo, hi = min(logs), max(logs)
    span = (hi - lo) or 1.0
    return [52 + 46 * (v - lo) / span for v in logs]


def _relax(radii: list, groups: list, steps: int = 900) -> list:
    n = len(radii)
    golden = math.pi * (3 - math.sqrt(5))
    pts = []
    for i in range(n):
        side = -1 if groups[i] == 0 else 1
        a = i * golden
        d = 40 * math.sqrt(i + 1)
        pts.append([VIEW / 2 + d * math.cos(a) * 0.6, VIEW / 2 + side * 120 + d * math.sin(a)])
    for step in range(steps):
        heat = 1.0 - step / steps
        moves = [[0.0, 0.0] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                dx, dy = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
                d = math.hypot(dx, dy) or 1e-6
                want = (radii[i] + radii[j]) * 0.88
                if d < want:
                    push = (want - d) * 0.5
                    ux, uy = dx / d, dy / d
                    moves[i][0] -= ux * push
                    moves[i][1] -= uy * push
                    moves[j][0] += ux * push
                    moves[j][1] += uy * push
        for i in range(n):
            # gravity: an upright ellipse, the two groups pulled to the upper and lower halves
            ty = VIEW / 2 + (-1 if groups[i] == 0 else 1) * 150
            gx = (VIEW / 2 - pts[i][0]) * 0.034
            gy = (ty - pts[i][1]) * 0.022
            pts[i][0] += moves[i][0] + gx * (0.3 + heat)
            pts[i][1] += moves[i][1] + gy * (0.3 + heat)
    # centre the cluster
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx = (min(x - r for x, r in zip(xs, radii)) + max(x + r for x, r in zip(xs, radii))) / 2
    cy = (min(y - r for y, r in zip(ys, radii)) + max(y + r for y, r in zip(ys, radii))) / 2
    return [[p[0] - cx + VIEW / 2, p[1] - cy + VIEW / 2] for p in pts]


def _clip(poly: list, nx: float, ny: float, c: float) -> list:
    """Keep the part of a polygon where nx*x + ny*y <= c."""
    out = []
    for k in range(len(poly)):
        a, b = poly[k], poly[(k + 1) % len(poly)]
        fa = nx * a[0] + ny * a[1] - c
        fb = nx * b[0] + ny * b[1] - c
        if fa <= 0:
            out.append(a)
        if (fa < 0) != (fb < 0):
            t = fa / (fa - fb)
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out


def _rounded(poly: list, radius: float) -> str:
    """SVG path of a polygon with rounded corners."""
    n = len(poly)
    if n < 3:
        return ""
    parts = []
    for k in range(n):
        p0, p1, p2 = poly[k - 1], poly[k], poly[(k + 1) % n]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*v1) or 1e-6, math.hypot(*v2) or 1e-6
        r = min(radius, l1 * 0.45, l2 * 0.45)
        a = (p1[0] + v1[0] / l1 * r, p1[1] + v1[1] / l1 * r)
        b = (p1[0] + v2[0] / l2 * r, p1[1] + v2[1] / l2 * r)
        parts.append(("M" if k == 0 else "L") + f"{a[0]:.1f},{a[1]:.1f}")
        parts.append(f"Q{p1[0]:.1f},{p1[1]:.1f} {b[0]:.1f},{b[1]:.1f}")
    return " ".join(parts) + " Z"


def layout(items: list) -> list:
    """items: [{"id", "size", "group": 0|1}] -> same order, with cx, cy, r, path, label point."""
    radii = _radii([it["size"] for it in items])
    groups = [it.get("group", 0) for it in items]
    pts = _relax(radii, groups)
    out = []
    for i, it in enumerate(items):
        px, py = pts[i]
        reach = radii[i] * 1.06
        poly = [(px + reach * math.cos(t * math.pi / 32), py + reach * math.sin(t * math.pi / 32)) for t in range(64)]
        for j in range(len(items)):
            if j == i:
                continue
            qx, qy = pts[j]
            d = math.hypot(qx - px, qy - py)
            if d > (radii[i] + radii[j]) * 1.1:
                continue
            # power bisector: |x-p|^2 - ri^2 = |x-q|^2 - rj^2, shifted by half the gap towards p
            nx, ny = (qx - px) / d, (qy - py) / d
            t = (d * d + radii[i] ** 2 - radii[j] ** 2) / (2 * d)
            c = nx * px + ny * py + t - GAP / 2
            poly = _clip(poly, nx, ny, c)
            if len(poly) < 3:
                break
        out.append(dict(it, cx=round(px, 1), cy=round(py, 1), r=round(radii[i], 1),
                        path=_rounded(poly, 26)))
    return out


def outline(cells: list, pad: float = 16.0) -> list:
    """Circles whose union is the membrane around the cluster: [(cx, cy, r)]."""
    return [(c["cx"], c["cy"], c["r"] * 1.06 + pad) for c in cells]


def bounds(cells: list, pad: float = 40.0) -> tuple:
    xs0 = min(c["cx"] - c["r"] for c in cells) - pad
    ys0 = min(c["cy"] - c["r"] for c in cells) - pad
    xs1 = max(c["cx"] + c["r"] for c in cells) + pad
    ys1 = max(c["cy"] + c["r"] for c in cells) + pad
    return xs0, ys0, xs1 - xs0, ys1 - ys0


def short_label(doc) -> str:
    """Two-line caption of a document inside its cell."""
    m = doc.meta
    if doc.category == "law":
        code = (m.get("code") or "").replace(" РФ", "")
        art = m.get("article") or ""
        return f"{code}\nст. {art}" if art else code
    title = doc.title.lower()
    number = m.get("number") or ""
    if "регламент" in title:
        year = "2023" if m.get("valid_to") else "2025"
        return f"Регламент\n{year}"
    if "соглашени" in title:
        return f"Допсоглашение\n№ 1" if "1" in title else "Допсоглашение"
    if "письмо" in title:
        return f"Письмо\n{number}"
    if "записка" in title:
        return f"Записка\n{number}"
    if "аренд" in title:
        return f"Аренда\n{number}"
    if "поставк" in title:
        return f"Поставка\n{number}"
    return number or doc.title[:14]
