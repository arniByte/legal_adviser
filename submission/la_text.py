"""Text utilities: normalisation, light Russian stemming, numbers and dates.

Pure standard library. Everything here is deterministic and is used both for
lexical search and for checking that an answer contains only facts that are
present in the knowledge base.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

_TRANSLATE = str.maketrans({
    "ё": "е", "Ё": "Е",
    "\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u2007": " ",
    "–": "-", "—": "-", "−": "-", "‑": "-", "‐": "-",
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "’": "'", "‘": "'",
})   # one-to-one replacements only: character offsets must survive the translation


def norm(text: str) -> str:
    """Lower-case and unify look-alike characters."""
    return re.sub(r"\s+", " ", text.translate(_TRANSLATE).replace("№", " № ").lower()).strip()


def squash(text: str) -> str:
    """Collapse all whitespace to single spaces (keeps case and punctuation)."""
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Stemming and tokenisation
# --------------------------------------------------------------------------- #

_WORD_RE = re.compile(r"[a-zа-я]+|\d+(?:[.,]\d+)*")

_ENDINGS = sorted({
    # adjectives / participles
    "ее", "ие", "ые", "ое", "ими", "ыми", "ей", "ий", "ый", "ой", "ем", "им", "ым", "ом",
    "его", "ого", "ему", "ому", "их", "ых", "ую", "юю", "ая", "яя", "ою", "ею",
    # verbs
    "ала", "ила", "ыла", "ена", "ите", "или", "ыли", "ил", "ыл", "ен", "ило", "ыло", "ено",
    "ят", "ует", "уют", "ит", "ыт", "ены", "ить", "ыть", "ать", "ять", "ишь", "ешь",
    "ла", "на", "ете", "йте", "ли", "ло", "но", "ет", "ют", "ны", "ть", "ал", "ял", "али", "яли",
    "ает", "ают", "яет", "яют", "ется", "ются",
    # nouns
    "а", "ев", "ов", "ье", "е", "иями", "ями", "ами", "еи", "ии", "и", "ией", "ой",
    "иям", "ям", "ием", "ам", "о", "у", "ах", "иях", "ях", "ы", "ь", "ию", "ью", "ю",
    "ия", "ья", "я", "й",
}, key=len, reverse=True)

STOPWORDS = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне
было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до
вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя
их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого
какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно
при наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве
три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда
конечно всю между это эта также либо которые который которая которых которым которой
""".split())


def stem(word: str) -> str:
    """Very light suffix-stripping stemmer for Russian (not linguistically exact)."""
    w = word.lower().replace("ё", "е")
    if len(w) <= 3 or w[0].isdigit() or not ("а" <= w[0] <= "я"):
        return w
    if w.endswith(("ся", "сь")) and len(w) > 5:
        w = w[:-2]
    for ending in _ENDINGS:
        if w.endswith(ending) and len(w) - len(ending) >= 3:
            w = w[: -len(ending)]
            break
    if w.endswith("ь"):
        w = w[:-1]
    if w.endswith("нн"):
        w = w[:-1]
    return w


def words(text: str) -> list[str]:
    """Raw lower-cased word and number tokens."""
    return _WORD_RE.findall(norm(text))


def tokens(text: str) -> list[str]:
    """Stemmed tokens without stop-words, for lexical search."""
    out = []
    for w in words(text):
        if w in STOPWORDS:
            continue
        if w[0].isdigit():
            out.append(w.replace(",", "."))
        elif len(w) > 1:
            out.append(stem(w))
    return out


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #

_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_MONTH_ALT = (r"январ[яеь]|феврал[яеь]|март[аеу]?|апрел[яеь]|ма[яейю]|июн[яеь]|июл[яеь]|"
              r"август[аеу]?|сентябр[яеь]|октябр[яеь]|ноябр[яеь]|декабр[яеь]")
_DATE_NUM_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
_DATE_TXT_RE = re.compile(r"(?<!\d)(\d{1,2})(?:-го)?\s+(" + _MONTH_ALT + r")\s+(\d{4})", re.I)
_MONTH_YEAR_RE = re.compile(r"(?<![\d\w])(" + _MONTH_ALT + r")\s+(\d{4})", re.I)
_YEAR_RE = re.compile(r"(?<![\d.,/-])((?:19|20)\d{2})(?![\d/-])\s*(?:г\b|г\.|год)", re.I)


def _month_number(word: str) -> int:
    w = word.lower()
    for prefix, number in sorted(_MONTHS.items(), key=lambda kv: -len(kv[0])):
        if w.startswith(prefix):
            return number
    return 0


def find_dates(text: str) -> list[tuple[int, int, int, int, int]]:
    """Full dates in a text as (year, month, day, start, end)."""
    t = text.translate(_TRANSLATE)
    found = []
    for m in _DATE_NUM_RE.finditer(t):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            found.append((y, mo, d, m.start(), m.end()))
    for m in _DATE_TXT_RE.finditer(t):
        d, mo, y = int(m.group(1)), _month_number(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and mo:
            found.append((y, mo, d, m.start(), m.end()))
    return sorted(found, key=lambda x: x[3])


def find_periods(text: str) -> list[tuple[int, int, int]]:
    """Dates and looser periods mentioned in a text as (year, month, day); 0 = unknown part."""
    t = text.translate(_TRANSLATE)
    spans: list[tuple[int, int]] = []
    out: list[tuple[int, int, int]] = []
    for y, mo, d, s, e in find_dates(t):
        out.append((y, mo, d))
        spans.append((s, e))

    def free(s: int, e: int) -> bool:
        return all(e <= a or s >= b for a, b in spans)

    for m in _MONTH_YEAR_RE.finditer(t):
        if free(m.start(), m.end()):
            out.append((int(m.group(2)), _month_number(m.group(1)), 0))
            spans.append((m.start(), m.end()))
    for m in _YEAR_RE.finditer(t):
        if free(m.start(1), m.end(1)):
            out.append((int(m.group(1)), 0, 0))
    return out


def fmt_date(y: int, mo: int, d: int) -> str:
    if d and mo:
        return f"{d:02d}.{mo:02d}.{y}"
    if mo:
        return f"{mo:02d}.{y}"
    return str(y)


# --------------------------------------------------------------------------- #
# Numbers
# --------------------------------------------------------------------------- #

_NUM_RE = re.compile(r"(?<![\w])(\d{1,3}(?: \d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)")
_SCALE_RE = re.compile(r"\s*(тыс|млн|млрд|миллион|миллиард)", re.I)
_SCALES = {"тыс": 10 ** 3, "млн": 10 ** 6, "миллион": 10 ** 6, "млрд": 10 ** 9, "миллиард": 10 ** 9}

_UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "одно": 1, "одного": 1, "одной": 1, "одному": 1, "одним": 1, "одну": 1,
    "два": 2, "две": 2, "двух": 2, "двум": 2, "двумя": 2,
    "три": 3, "трех": 3, "трем": 3, "тремя": 3,
    "четыре": 4, "четырех": 4, "четырем": 4, "четырьмя": 4,
    "пять": 5, "пяти": 5, "пятью": 5, "шесть": 6, "шести": 6, "шестью": 6,
    "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
    "десять": 10, "десяти": 10, "десятью": 10,
    "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12, "двенадцати": 12,
    "тринадцать": 13, "тринадцати": 13, "четырнадцать": 14, "четырнадцати": 14,
    "пятнадцать": 15, "пятнадцати": 15, "шестнадцать": 16, "шестнадцати": 16,
    "семнадцать": 17, "семнадцати": 17, "восемнадцать": 18, "восемнадцати": 18,
    "девятнадцать": 19, "девятнадцати": 19,
    "двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30, "сорок": 40, "сорока": 40,
    "пятьдесят": 50, "пятидесяти": 50, "шестьдесят": 60, "шестидесяти": 60,
    "семьдесят": 70, "семидесяти": 70, "восемьдесят": 80, "восьмидесяти": 80,
    "девяносто": 90, "девяноста": 90, "сто": 100, "ста": 100,
    "двести": 200, "двухсот": 200, "триста": 300, "трехсот": 300, "четыреста": 400, "четырехсот": 400,
    "пятьсот": 500, "пятисот": 500, "шестьсот": 600, "шестисот": 600, "семьсот": 700, "семисот": 700,
    "восемьсот": 800, "восьмисот": 800, "девятьсот": 900, "девятисот": 900,
}
_WORD_SCALES = {"тысяча": 10 ** 3, "тысячи": 10 ** 3, "тысяч": 10 ** 3, "тысячу": 10 ** 3,
                "миллион": 10 ** 6, "миллиона": 10 ** 6, "миллионов": 10 ** 6,
                "миллиард": 10 ** 9, "миллиарда": 10 ** 9, "миллиардов": 10 ** 9}
_HALF = {"полтора": "1.5", "полутора": "1.5", "полторы": "1.5"}


def canon(value) -> str:
    """Canonical string for a number: '450000', '0.1', '2.4'."""
    try:
        d = Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return str(value)
    d = d.normalize()
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


class Mention:
    """A number mentioned in a text.

    `alts`  - equivalent values, any of which confirms the mention;
    `parts` - for compound numerals ("двадцать четыре"): the component values,
              all of which together also confirm it.
    """

    __slots__ = ("surface", "alts", "parts", "kind")

    def __init__(self, surface: str, alts: Iterable[str], kind: str, parts: Iterable[str] = ()):
        self.surface = surface
        self.alts = set(alts)
        self.parts = set(parts)
        self.kind = kind

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Mention({self.surface!r}, {sorted(self.alts)}, {self.kind})"

    def supported_by(self, values: set) -> bool:
        return bool(self.alts & values) or bool(self.parts and self.parts <= values)


def number_mentions(text: str) -> list[Mention]:
    """All numeric mentions: dates, digit numbers (with тыс/млн scaling) and numeral words."""
    t = text.translate(_TRANSLATE).replace("ё", "е")
    mentions: list[Mention] = []
    masked = list(t)

    for y, mo, d, s, e in find_dates(t):
        mentions.append(Mention(t[s:e], {f"D{y:04d}-{mo:02d}-{d:02d}"}, "date"))
        for i in range(s, e):
            masked[i] = " "
    t2 = "".join(masked)

    consumed: list[tuple[int, int]] = []
    for m in _NUM_RE.finditer(t2):
        raw = m.group(1)
        base = canon(raw)
        alts = {base}
        scale = _SCALE_RE.match(t2, m.end())
        if scale:
            try:
                alts.add(canon(Decimal(base) * _SCALES[scale.group(1).lower()]))
                consumed.append((scale.start(1), scale.end(1)))
            except InvalidOperation:
                pass
        # list markers like "1) ..." are not facts
        after = t2[m.end(): m.end() + 1]
        kind = "marker" if (after == ")" and len(raw) <= 2 and "." not in raw and "," not in raw) else "digit"
        mentions.append(Mention(raw, alts, kind))

    # numeral words, including compounds such as "двадцать четыре" or "восемь миллионов четыреста тысяч"
    toks = [(m.group(0), m.start()) for m in re.finditer(r"[а-я]+", t2.lower())
            if not any(a <= m.start() < b for a, b in consumed)]
    i = 0
    while i < len(toks):
        w = toks[i][0]
        if w in _HALF:
            mentions.append(Mention(w, {_HALF[w]}, "word"))
            i += 1
            continue
        if w not in _UNITS and w not in _WORD_SCALES:
            i += 1
            continue
        total, current, parts, surface, has_unit = 0, 0, set(), [], False
        j = i
        while j < len(toks) and (toks[j][0] in _UNITS or toks[j][0] in _WORD_SCALES):
            if j > i and toks[j][1] - (toks[j - 1][1] + len(toks[j - 1][0])) > 2:
                break
            word = toks[j][0]
            surface.append(word)
            if word in _UNITS:
                has_unit = True
                current += _UNITS[word]
                parts.add(canon(_UNITS[word]))
            else:
                total += max(current, 1) * _WORD_SCALES[word]
                current = 0
            j += 1
        value = total + current
        if not has_unit:  # a bare "тысяч" / "миллионов" is not a number by itself
            i = j
            continue
        mentions.append(Mention(" ".join(surface), {canon(value)}, "word",
                                parts if (len(parts) > 1 and total == 0) else ()))
        i = j
    return mentions


def support_values(text: str) -> set[str]:
    """Every value a text can vouch for (all alternatives of all mentions)."""
    values: set[str] = set()
    for m in number_mentions(text):
        values |= m.alts
        values |= m.parts
        if m.kind == "date":
            for alt in list(m.alts):
                y, mo, d = alt[1:].split("-")
                values |= {canon(y), canon(mo), canon(d)}
    return values


# --------------------------------------------------------------------------- #
# Fuzzy containment
# --------------------------------------------------------------------------- #

def alnum(text: str) -> str:
    """Normalised text with punctuation removed."""
    return re.sub(r"[^0-9a-zа-я]+", " ", norm(text)).strip()


_ALNUM_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")


def _word_spans(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    ws, spans = [], []
    for m in _ALNUM_RE.finditer(text):
        ws.append(m.group(0).lower().replace("ё", "е"))
        spans.append((m.start(), m.end()))
    return ws, spans


class TextIndex:
    """Finds (possibly slightly altered) quotes in a text and maps them back to character offsets."""

    def __init__(self, text: str):
        self.text = text
        self.words, self.spans = _word_spans(text)
        self.joined = " ".join(self.words)
        self.positions: dict[str, list[int]] = {}
        for i, w in enumerate(self.words):
            self.positions.setdefault(w, []).append(i)

    def find(self, quote: str, min_cover: float = 0.75):
        """Return (start_char, end_char) of the best match for `quote`, or None."""
        q, _ = _word_spans(quote)
        if not q:
            return None
        n = len(q)
        # exact word-sequence match
        first = self.positions.get(q[0], [])
        for p in first:
            if self.words[p:p + n] == q:
                return self.spans[p][0], self.spans[p + n - 1][1]
        if n < 4:
            return None
        # anchor on the rarest informative word of the quote, then align a window around it
        anchors = sorted((len(self.positions.get(w, ())), i, w) for i, w in enumerate(q)
                         if len(w) >= 4 and w in self.positions)
        if not anchors:
            return None
        from difflib import SequenceMatcher
        best = None
        for _, qi, w in anchors[:3]:
            for p in self.positions[w][:40]:
                lo = max(0, p - qi - 6)
                hi = min(len(self.words), p - qi + n + 12)
                window = self.words[lo:hi]
                blocks = SequenceMatcher(None, q, window, autojunk=False).get_matching_blocks()
                matched = sum(b.size for b in blocks)
                if matched / n >= min_cover:
                    used = [b for b in blocks if b.size]
                    start = lo + used[0].b
                    end = lo + used[-1].b + used[-1].size - 1
                    score = matched / n
                    if best is None or score > best[0]:
                        best = (score, self.spans[start][0], self.spans[end][1])
            if best:
                break
        return (best[1], best[2]) if best else None

    def has(self, quote: str, min_cover: float = 0.75) -> bool:
        return self.find(quote, min_cover) is not None


def sentences(text: str) -> list[str]:
    """Rough sentence split that keeps abbreviations like 'п. 3.1' and 'ст. 70' intact."""
    protected = re.sub(r"\b(п|пп|ст|ч|подп|абз|г|руб|ред|стр|д|ул|т|н|е|пр|тыс|млн|кв|м|им|см)\.",
                       lambda m: m.group(1) + "\u0001", text)
    protected = re.sub(r"(\d)\.(\d)", "\\1\u0001\\2", protected)
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z«\"(\d])", protected)
    return [p.replace("\u0001", ".").strip() for p in parts if p.strip()]
