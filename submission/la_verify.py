"""Deterministic verification of a generated answer against the knowledge base.

No LLM here. These are structural and numeric checks, not a proof of the
legal inference. User-provided values remain inputs, not documentary evidence.
Hard issues trigger a repair round and block publication if they remain.

Checks:
  1. every cited source exists: document label, clause / part, and the quote;
  2. every number, sum, percentage, term and date in the answer is present in
     the documents or in the question, or is simple arithmetic over them;
  3. every «quoted name» and every "№ ..." identifier exists in the documents
     or in the question.
"""

from __future__ import annotations

import itertools
import re
import unicodedata

from la_prompts import locator_keys
from la_text import alnum, number_mentions, support_values


def doc_support(doc) -> set:
    """Numbers and dates a document can vouch for (cached on the document)."""
    cached = getattr(doc, "_support", None)
    if cached is None:
        cached = support_values(doc.text + "\n" + doc.title)
        doc._support = cached
    return cached


def _floats(values) -> list:
    out = []
    for v in values:
        if v.startswith("D"):
            continue
        try:
            out.append(float(v))
        except ValueError:
            pass
    return out


def _derivable(targets: set, operands: list) -> bool:
    """Is one of `targets` a simple arithmetic combination of two or three operands?"""
    goal = _floats(targets)
    ops = sorted(set(x for x in operands if x != 0))[:40]
    if not goal or len(ops) < 2:
        return False

    def hit(x: float) -> bool:
        return any(abs(x - g) <= max(1e-9, abs(g) * 1e-6) or abs(round(x, 2) - g) <= 1e-9 for g in goal)

    for a, b in itertools.permutations(ops, 2):
        if hit(a * b) or hit(a * b / 100.0) or hit(a + b) or hit(a - b) or (b and hit(a / b)) \
                or (b and hit(a / b * 100.0)) or hit(a + a * b / 100.0) or hit(a - a * b / 100.0):
            return True
    if len(ops) <= 22:
        for a, b, c in itertools.combinations(ops, 3):
            p = a * b * c
            if hit(p) or hit(p / 100.0) or hit(p / 10000.0):
                return True
        for a, b, c in itertools.permutations(ops, 3):       # "312 000 + 15% x 1 600 000"
            if b < c and (hit(a + b * c / 100.0) or hit(a - b * c / 100.0) or hit(a + b * c) or hit(a - b * c)):
                return True
    return False


_NAME_RE = re.compile(r"«([^«»]{2,90})»")
_DOCNUM_RE = re.compile(r"№\s*([0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё\-/]{0,24})")
_REF_HINT_RE = re.compile(r"(\bп\.|\bпп\.|\bст\.|\bч\.|№|источник|раздел|редакци|\bстать|\bпункт|\bчаст[ьи])", re.I)


_TYPOGRAPHY = str.maketrans({"ё": "е", "«": '"', "»": '"', "“": '"', "”": '"',
                           "‘": "'", "’": "'", "—": "-", "–": "-", "−": "-"})
_ARTICLE_RE = re.compile(r"\bст(?:атья|атьи|\.)\s*(\d+(?:\.\d+)*)", re.I)
_QUOTE_NUMBER_RE = re.compile(r"(?<!\w)[+-]?\d+(?: \d{3})*(?:[.,:/-]\d+)*(?: ?%)?")


def _quote_text(text: str) -> tuple:
    """Normalise typography with offsets; never discard numbers, words, signs or punctuation."""
    chars, offsets = [], []
    for pos, char in enumerate(text):
        for normalized in unicodedata.normalize("NFKC", char).lower().translate(_TYPOGRAPHY):
            if normalized.isspace():
                if not chars or chars[-1] == " ":
                    continue
                normalized = " "
            chars.append(normalized)
            offsets.append(pos)
    return "".join(chars), offsets


def exact_quote_span(text: str, quote: str, start: int = 0, end: int = None):
    """Exact quotation in a structural range, allowing whitespace and typographic variants only."""
    haystack, offsets = _quote_text(text[start:end])
    needle = _quote_text(quote)[0].strip()
    if not needle:
        return None
    number_spans = [m.span() for m in _QUOTE_NUMBER_RE.finditer(haystack)]
    pos = haystack.find(needle)
    while pos >= 0:
        last = pos + len(needle)
        # A quoted amount of 50 must not match the suffix of 450, nor a truncated word.
        left_ok = not (needle[0].isalnum() and pos and haystack[pos - 1].isalnum())
        right_ok = not (needle[-1].isalnum() and last < len(haystack) and haystack[last].isalnum())
        complete_numbers = not any(a < boundary < b for a, b in number_spans for boundary in (pos, last))
        if left_ok and right_ok and complete_numbers:
            return start + offsets[pos], start + offsets[last - 1] + 1
        pos = haystack.find(needle, pos + 1)
    return None


def _citation_unit(doc, locator: str):
    """Resolve a unique unit without Doc.unit_by_keys' retrieval-oriented first-match fallback."""
    label = locator.lower().replace("ё", "е")
    keys = locator_keys(label)
    if not keys:
        return None
    # "п. 1, п. 2" enumerates two peers; it must not become "п. 1, подп. 2".
    if len(keys) > 1 and keys[0] != "прим" and not re.search(r"\bподп(?:\.|ункт)", label):
        return None
    if re.search(r"\bраздел", label):
        return next((u for u in doc.units if u.kind == "section" and locator_keys(u.locator) == keys), None)
    candidates = [u for u in doc.units if u.keys]
    if re.search(r"\bподп(?:\.|ункт)", label):
        candidates = [u for u in candidates if u.kind == "subpoint"]
    elif re.search(r"\bч(?:\.|асть|асти)\s*\d", label):
        candidates = [u for u in candidates if u.locator.startswith("ч.")]
    elif re.search(r"\bп(?:\.|ункт|ункта)\s*\d", label):
        candidates = [u for u in candidates if u.locator.startswith("п.")]
    exact = [u for u in candidates if u.keys == keys]
    if len(exact) == 1:
        return exact[0]
    if len(keys) == 1:
        short = [u for u in candidates if u.keys[-1] == keys[0]]
        if len(short) == 1:
            return short[0]
    return None


def _unit_range(doc, unit) -> tuple:
    """A parent provision includes its explicitly numbered children, never its next peer."""
    end = unit.end
    if unit.kind == "section":
        following = next((u.start for u in doc.units[unit.idx + 1:] if u.kind == "section"), len(doc.text))
        return unit.start, following
    if unit.keys:
        for child in doc.units[unit.idx + 1:]:
            if len(child.keys) <= len(unit.keys) or child.keys[:len(unit.keys)] != unit.keys:
                break
            end = child.end
    return unit.start, end


def verify(parsed: dict, kb, context_doc_ids: list, question_text: str, extra_text: str = "", *,
           require_sources: bool = False) -> dict:
    """Return {'sources': [...resolved...], 'issues': [...], 'hard': n, 'checked_numbers': n}."""
    answer = parsed.get("answer", "")
    issues: list = []
    resolved: list = []

    # ---- 1. cited sources
    for src in parsed.get("sources", []):
        doc = kb.by_id.get(src["doc_id"])
        item = {"doc_id": src["doc_id"], "locator": src["locator"], "quote": src["quote"],
                "title": doc.title if doc else "", "ok": False, "start": None, "end": None, "unit_idx": None}
        if doc is None:
            issues.append({"type": "unknown_doc", "hard": True,
                           "detail": f"метка документа {src['doc_id']} отсутствует в базе"})
            resolved.append(item)
            continue
        article = _ARTICLE_RE.search(src["locator"])
        bad_article = article is not None and article.group(1) != str(doc.meta.get("article", ""))
        keys = locator_keys(src["locator"])
        unit = _citation_unit(doc, src["locator"]) if keys else None
        named_range = bool(keys)
        # An article-only citation can name this whole law; arbitrary textual locators need a quote.
        bad_locator = bad_article or (named_range and unit is None)
        if bad_locator:
            issues.append({"type": "bad_locator", "hard": True,
                           "detail": f"в документе «{doc.title}» нет однозначного положения «{src['locator']}»"})
        scope = _unit_range(doc, unit) if unit is not None else (0, len(doc.text))
        span = exact_quote_span(doc.text, src["quote"], *scope) if src["quote"] else None
        if src["quote"] and span is None:
            issues.append({"type": "bad_quote", "hard": True,
                           "detail": f"дословная цитата не найдена в указанном положении документа «{doc.title}»: "
                                     f"«{src['quote'][:80]}»"})
        if unit is None and not src["quote"]:
            issues.append({"type": "missing_evidence", "hard": True,
                           "detail": f"для документа «{doc.title}» нужны точный пункт или дословная цитата"})
        if span:
            item["start"], item["end"] = span
            if unit is None:                       # locate the unit that contains the quote
                unit = next((u for u in doc.units if u.start <= span[0] < u.end + 1), None)
        elif unit is not None:
            item["start"], item["end"] = scope
        if unit is not None:
            item["unit_idx"] = unit.idx
            # Display a locator from the registry, never a model's unvalidated label.
            item["locator"] = unit.locator
        item["ok"] = not bad_locator and (span is not None or (unit is not None and not src["quote"]))
        if item["ok"]:
            item["quote"] = doc.text[item["start"]:item["end"]]
        resolved.append(item)

    if require_sources and parsed.get("status") in ("ANSWER", "PARTIAL") and not any(s["ok"] for s in resolved):
        issues.append({"type": "missing_source", "hard": True,
                       "detail": "юридический ответ должен содержать хотя бы одно проверенное положение"})

    # ---- 2. numbers and dates
    support: set = set()
    for doc_id in context_doc_ids:
        doc = kb.by_id.get(doc_id)
        if doc is not None:
            support |= doc_support(doc)
    catalog = getattr(kb, "_catalog_support", None)          # titles of all documents are shown in the catalogue
    if catalog is None:
        catalog = kb._catalog_support = support_values("\n".join(d.title for d in kb.docs))
    support |= catalog
    question_values = support_values(question_text + "\n" + extra_text)
    support |= question_values
    quote_values: set = set()
    for src in resolved:
        if src["ok"]:
            quote_values |= support_values(src.get("quote", ""))
    operands = _floats(question_values | quote_values)

    checked = 0
    pending = []
    for m in number_mentions(answer):
        if m.kind == "marker":
            continue
        if m.kind == "word" and m.alts <= {"1", "0"}:
            continue                                  # "один из", "одна сторона" are not facts
        checked += 1
        if not m.supported_by(support):
            pending.append(m)
    # numbers obtained by arithmetic: intermediate results become operands for the next step
    pool = operands + _floats(support & _answer_values(answer))
    changed = True
    while changed and pending:
        changed = False
        for m in list(pending):
            if m.kind != "date" and _derivable(m.alts, pool):
                pool = pool + _floats(m.alts)
                pending.remove(m)
                changed = True
    unsupported = [m.surface for m in pending]
    for surface in dict.fromkeys(unsupported):
        issues.append({"type": "unsupported_number", "hard": True,
                       "detail": f"число или дата «{surface}» отсутствует в документах и в вопросе"})

    # ---- 3. names and identifiers
    haystack = " ".join([kb.by_id[d].index.joined for d in context_doc_ids if d in kb.by_id]
                        + [" ".join(kb.by_id[d].title.lower().replace("ё", "е").split()) for d in context_doc_ids
                           if d in kb.by_id] + [alnum(question_text), alnum(extra_text)])
    compact = haystack.replace(" ", "")
    for name in dict.fromkeys(_NAME_RE.findall(answer)):
        if not (name[:1].isupper() or any(ch.isdigit() for ch in name)):
            continue                                   # a quoted turn of phrase, not a name or a title
        key = alnum(name)
        if len(key) >= 3 and key not in haystack and key.replace(" ", "") not in compact:
            issues.append({"type": "unsupported_name", "hard": True,
                           "detail": f"наименование «{name}» отсутствует в документах и в вопросе"})
    for num in dict.fromkeys(_DOCNUM_RE.findall(answer)):
        key = alnum(num).replace(" ", "")
        if len(key) >= 2 and key not in compact:
            issues.append({"type": "unsupported_identifier", "hard": True,
                           "detail": f"номер «№ {num}» отсутствует в документах и в вопросе"})

    return {"sources": resolved, "issues": issues, "hard": sum(1 for i in issues if i["hard"]),
            "checked_numbers": checked, "has_reference": bool(_REF_HINT_RE.search(answer))}


def _answer_values(answer: str) -> set:
    return support_values(answer)


def issues_text(report: dict) -> str:
    return "\n".join(f"- {i['detail']}" for i in report["issues"] if i["hard"])
