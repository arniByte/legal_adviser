"""Deterministic verification of a generated answer against the knowledge base.

No LLM here. The checks answer one question: does the answer contain anything
that cannot be traced to the documents shown to the model or to the user's own
words?  Hard issues trigger a repair round in the pipeline.

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
    if len(ops) <= 18:
        for a, b, c in itertools.combinations(ops, 3):
            p = a * b * c
            if hit(p) or hit(p / 100.0) or hit(p / 10000.0):
                return True
    return False


_NAME_RE = re.compile(r"«([^«»]{2,90})»")
_DOCNUM_RE = re.compile(r"№\s*([0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё\-/]{0,24})")
_REF_HINT_RE = re.compile(r"(\bп\.|\bпп\.|\bст\.|\bч\.|№|источник|раздел|редакци|\bстать|\bпункт|\bчаст[ьи])", re.I)


def verify(parsed: dict, kb, context_doc_ids: list, question_text: str, extra_text: str = "") -> dict:
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
        keys = locator_keys(src["locator"])
        unit = doc.unit_by_keys(keys) if keys else None
        if keys and unit is None:
            issues.append({"type": "bad_locator", "hard": True,
                           "detail": f"в документе «{doc.title}» нет положения «{src['locator']}»"})
        span = doc.index.find(src["quote"]) if src["quote"] else None
        if src["quote"] and span is None:
            issues.append({"type": "bad_quote", "hard": False,
                           "detail": f"цитата не найдена в документе «{doc.title}»: «{src['quote'][:80]}»"})
        if span:
            item["start"], item["end"] = span
            if unit is None:                       # locate the unit that contains the quote
                unit = next((u for u in doc.units if u.start <= span[0] < u.end + 1), None)
        elif unit is not None:
            item["start"], item["end"] = unit.start, unit.end
        if unit is not None:
            item["unit_idx"] = unit.idx
            if not item["locator"]:
                item["locator"] = unit.locator
        item["ok"] = (unit is not None or not keys) and (span is not None or not src["quote"])
        resolved.append(item)

    # ---- 2. numbers and dates
    support: set = set()
    for doc_id in context_doc_ids:
        doc = kb.by_id.get(doc_id)
        if doc is not None:
            support |= doc_support(doc)
    question_values = support_values(question_text + "\n" + extra_text)
    support |= question_values
    quote_values: set = set()
    for src in parsed.get("sources", []):
        quote_values |= support_values(src.get("quote", ""))
    operands = _floats(question_values | quote_values)

    checked = 0
    unsupported = []
    for m in number_mentions(answer):
        if m.kind == "marker":
            continue
        if m.kind == "word" and m.alts <= {"1", "0"}:
            continue                                  # "один из", "одна сторона" are not facts
        checked += 1
        if m.supported_by(support):
            continue
        if m.kind != "date" and _derivable(m.alts, operands + _floats(support & _answer_values(answer))):
            continue
        unsupported.append(m.surface)
    for surface in dict.fromkeys(unsupported):
        issues.append({"type": "unsupported_number", "hard": True,
                       "detail": f"число или дата «{surface}» отсутствует в документах и в вопросе"})

    # ---- 3. names and identifiers
    haystack = " ".join([kb.by_id[d].index.joined for d in context_doc_ids if d in kb.by_id]
                        + [" ".join(kb.by_id[d].title.lower().replace("ё", "е").split()) for d in context_doc_ids
                           if d in kb.by_id] + [alnum(question_text), alnum(extra_text)])
    compact = haystack.replace(" ", "")
    for name in dict.fromkeys(_NAME_RE.findall(answer)):
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
