"""Render the README figures to docs/assets/*.svg from the knowledge base.

    python scripts/figures.py

Figures are plain SVG (gradients and filters only, no scripts or web fonts), so GitHub renders them as images.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "submission"))
sys.path.insert(0, str(ROOT))

from app.cells import bounds, layout, outline, short_label  # noqa: E402
from la_kb import load_kb  # noqa: E402

OUT = ROOT / "docs" / "assets"
SERIF = "'Source Serif 4', 'PT Serif', Georgia, serif"
SANS = "Inter, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"

DEFS = """
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#050404"/><stop offset=".45" stop-color="#1a1312"/>
  <stop offset=".8" stop-color="#6e5551"/><stop offset="1" stop-color="#c7a9a2"/>
</linearGradient>
<radialGradient id="cellLit" cx=".38" cy=".3" r=".85">
  <stop offset="0" stop-color="#e9c3b8"/><stop offset=".5" stop-color="#b07f74"/><stop offset="1" stop-color="#4a302c"/>
</radialGradient>
<radialGradient id="cellRest" cx=".4" cy=".32" r=".85">
  <stop offset="0" stop-color="#3a3333"/><stop offset=".6" stop-color="#211c1c"/><stop offset="1" stop-color="#0d0b0b"/>
</radialGradient>
<filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="10"/></filter>
<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="22"/></filter>
"""


def _mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def cell_gradients(cells: list) -> str:
    """Per-cell shading: like the reference, cells low in the cluster catch more of the warm light."""
    ys = [c["cy"] for c in cells]
    lo, hi = min(ys), max(ys)
    out = []
    for c in cells:
        t = (c["cy"] - lo) / ((hi - lo) or 1)
        core = _mix("#4a3534", "#b58a80", t)
        mid = _mix("#2c2020", "#6e4b46", t)
        out.append(f'<radialGradient id="c{c["id"]}" cx=".42" cy=".62" r=".75" fx=".45" fy=".7">'
                   f'<stop offset="0" stop-color="{core}"/><stop offset=".62" stop-color="{mid}"/>'
                   f'<stop offset="1" stop-color="#140e0e"/></radialGradient>')
    return "".join(out)


def cluster_svg(cells: list, lit=(), rest=(), labels=True, scale=1.0) -> str:
    """The cluster as an SVG group in layout coordinates."""
    parts = [f"<defs>{cell_gradients(cells)}</defs>"]
    circles = outline(cells)
    parts.append('<g opacity=".7" filter="url(#soft)">' + "".join(
        f'<circle cx="{x}" cy="{y + 14}" r="{r + 6}" fill="#000"/>' for x, y, r in circles) + "</g>")
    # glassy membrane: a light rim drawn under the fill, so only the outer edge of the union shows
    parts.append("<g>" + "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="#d8bdb6" stroke-opacity=".28" stroke-width="2.5"/>'
        for x, y, r in circles) + "".join(
        f'<circle cx="{x}" cy="{y}" r="{r - 1.2}" fill="#3a2c2b"/>' for x, y, r in circles) + "</g>")
    for c in cells:
        if c["id"] in lit:
            parts.append(f'<path d="{c["path"]}" fill="#e7b8aa" opacity=".55" filter="url(#glow)"/>')
    for c in cells:
        fill = "url(#cellLit)" if c["id"] in lit else ("url(#cellRest)" if c["id"] in rest else f"url(#c{c['id']})")
        parts.append(f'<path d="{c["path"]}" fill="{fill}"/>')
        parts.append(f'<path d="{c["path"]}" fill="none" stroke="#f1d9d2" stroke-opacity=".10" stroke-width="1.6"/>')
        if labels:
            tone = "#2a1a17" if c["id"] in lit else ("#6d6262" if c["id"] in rest else "#e8d6d1")
            size = 15 if c["r"] > 70 else 13
            lines = c["label"].split("\n")
            y0 = c["cy"] - (len(lines) - 1) * size * 0.6
            for k, line in enumerate(lines):
                parts.append(f'<text x="{c["cx"]}" y="{y0 + k * size * 1.2 + size * 0.35:.1f}" text-anchor="middle" '
                             f'font-family="{SANS}" font-size="{size}" fill="{tone}" fill-opacity=".85">{line}</text>')
    return "\n".join(parts)


def hero(cells: list) -> str:
    x, y, w, h = bounds(cells, 50)
    W, H = 1280, 760
    s = min(560 / h, 560 / w)
    tx, ty = 760 - x * s, 100 - y * s
    lapsed = [c["id"] for c in cells if c.get("state") == "lapsed"]
    lit = [c["id"] for c in cells if c.get("lit")]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
<defs>{DEFS}</defs>
<rect width="{W}" height="{H}" fill="url(#bg)"/>
<text x="84" y="250" font-family="{SERIF}" font-size="96" fill="#f3e8e4" letter-spacing="-2">Параграф</text>
<text x="88" y="318" font-family="{SERIF}" font-size="30" fill="#cdb6b0">Ответ со ссылкой на пункт —</text>
<text x="88" y="358" font-family="{SERIF}" font-size="30" fill="#8f7a76">или честное «в базе этого нет».</text>
<g font-family="{SANS}" font-size="15" fill="#d9c5c0">
  <circle cx="96" cy="560" r="7" fill="url(#cellLit)"/><text x="114" y="565">документ, на который сослался ответ</text>
  <circle cx="96" cy="596" r="7" fill="#7a5853" stroke="#8d7470"/><text x="114" y="601">документ базы знаний</text>
  <circle cx="96" cy="632" r="7" fill="url(#cellRest)" stroke="#5a4c4a"/><text x="114" y="637">утратившая силу редакция</text>
</g>
<text x="88" y="700" font-family="{SANS}" font-size="13" fill="#3a2b29">21 документ · одна клетка — один документ · площадь — объём текста</text>
<g transform="translate({tx:.1f} {ty:.1f}) scale({s:.4f})">
{cluster_svg(cells, lit=lit, rest=lapsed)}
</g>
</svg>'''


def items_from_kb(kb) -> list:
    items = []
    for d in kb.docs:
        items.append({"id": d.doc_id, "size": d.size, "group": 0 if d.category == "company" else 1,
                      "label": short_label(d), "state": "lapsed" if d.meta.get("valid_to") else "",
                      "lit": False})
    return items


def main() -> None:
    kb = load_kb(ROOT / "data" / "knowledge-base.zip")
    items = items_from_kb(kb)
    # the hero shows the answer to "a contract for 12 million signed in August 2024": both regulation editions are cited
    for it in items:
        it["lit"] = "Регламент" in kb.by_id[it["id"]].title and not it["state"]
    cells = layout(items)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "hero.svg").write_text(hero(cells), encoding="utf-8")
    (OUT / "how-it-works.svg").write_text(how_it_works(cells), encoding="utf-8")
    deck = ROOT / "presentation" / "img"
    deck.mkdir(parents=True, exist_ok=True)
    lapsed = [c["id"] for c in cells if c.get("state") == "lapsed"]
    regulations = [c["id"] for c in cells if "Регламент" in c["label"]]
    (deck / "cluster.svg").write_text(standalone(cells, rest=lapsed, labels=True), encoding="utf-8")
    (deck / "cluster-lit.svg").write_text(standalone(cells, lit=regulations, rest=lapsed, labels=True), encoding="utf-8")
    (deck / "cluster-plain.svg").write_text(standalone(cells, rest=lapsed, labels=False), encoding="utf-8")
    print("written:", ", ".join(p.name for p in sorted(OUT.glob("*.svg"))))



def _wrap(text: str, width: int) -> list:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + ([cur] if cur else [])


STEPS = [
    ("01", "Вопрос", "Разбираем вопрос или диалог. Код узнаёт контрагента, номер договора и дату события."),
    ("02", "Поиск", "Находим нужные положения: короткие документы целиком, большие статьи — фрагментами."),
    ("03", "Ответ", "Модель отвечает по найденному тексту, со ссылкой на пункт. Сведений нет — так и говорит."),
    ("04", "Сверка", "Код сверяет с документами каждое число, дату и ссылку. Нашёл расхождение — ответ исправляется."),
]


def how_it_works(cells: list) -> str:
    W, H = 1280, 520
    col = (W - 160) / 4
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
             f"<defs>{DEFS}</defs>", f'<rect width="{W}" height="{H}" fill="url(#bg)"/>',
             f'<text x="80" y="92" font-family="{SERIF}" font-size="44" fill="#f3e8e4">Как получается ответ</text>',
             ]
    # a small cluster per step: the knowledge base, with more cells lit as the answer narrows down
    sample = cells[:]
    lit_sets = [(), [c["id"] for c in sample[:9]], [c["id"] for c in sample if c.get("lit")], [c["id"] for c in sample if c.get("lit")]]
    x0, y0, w, h = bounds(sample, 30)
    s = 150 / max(w, h)
    for k, (num, title, text) in enumerate(STEPS):
        cx = 80 + col * k
        parts.append(f'<g transform="translate({cx + 10 - x0 * s:.1f} {170 - y0 * s:.1f}) scale({s:.4f})">'
                     f'{cluster_svg(sample, lit=lit_sets[k], rest=[c["id"] for c in sample if c.get("state") == "lapsed"], labels=False)}'
                     + ("".join(f'<path d="{c["path"]}" fill="none" stroke="#f6e3dc" stroke-width="7"/>'
                                for c in sample if c.get("lit")) if k == 3 else "") + "</g>")
        parts.append(f'<text x="{cx:.1f}" y="372" font-family="{SANS}" font-size="14" fill="#8f7a76" letter-spacing="2">{num}</text>')
        parts.append(f'<text x="{cx:.1f}" y="408" font-family="{SERIF}" font-size="30" fill="#f3e8e4">{title}</text>')
        for i, line in enumerate(_wrap(text, 31)):
            parts.append(f'<text x="{cx:.1f}" y="{442 + i * 22}" font-family="{SANS}" font-size="15" fill="#3b2a28">{line}</text>')
    parts.append("</svg>")
    return "\n".join(parts)



def standalone(cells: list, lit=(), rest=(), labels=True) -> str:
    """The cluster alone on a transparent background, for slides."""
    x, y, w, h = bounds(cells, 60)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x:.0f} {y:.0f} {w:.0f} {h:.0f}">'
            f"<defs>{DEFS}</defs>{cluster_svg(cells, lit=lit, rest=rest, labels=labels)}</svg>")


if __name__ == "__main__":
    main()
