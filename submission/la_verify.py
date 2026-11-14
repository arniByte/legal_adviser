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
from la_text import TextIndex, alnum, number_mentions, support_values


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

_INLINE_UNIT_RE = re.compile(
    r"\b(?:пп?\.|пункт\w*|ч\.|част[ьи]\w*|подп\.)\s*(\d+(?:\.\d+)*)"
    r"(?:\s*,\s*подп\.?\s*(\d+(?:\.\d+)*))?", re.I)
_FACT_REF_RE = re.compile(
    r"\b(?:пп?\.|подп\.|ч\.|ст\.|пункт\w*|стать\w*|част[ьи]\w*|раздел\w*)\s*"
    r"\d+(?:\.\d+)*(?:(?:\s*,\s*|\s+и\s+)\d+(?:\.\d+)*)*", re.I)


def exact_unit(doc, keys: tuple):
    """Validation must not invent a child by falling back to an existing parent."""
    if not keys:
        return None
    direct = next((u for u in doc.units if u.keys == keys), None)
    if direct is not None:
        return direct
    if len(keys) == 1:
        aliases = [u for u in doc.units if u.keys and u.keys[-1] == keys[0]]
        if len(aliases) == 1:
            return aliases[0]
        # A parser may represent an implicit parent only by its children.
        children = [u for u in doc.units if len(u.keys) > 1 and u.keys[0] == keys[0]]
        if not aliases and children:
            return children[0]
    return None


def _scope_units(doc, unit):
    if unit.kind in ("point", "part") and len(unit.keys) == 1:
        return [u for u in doc.units if u.keys and u.keys[:1] == unit.keys]
    return [unit]


def _fact_text(text: str) -> str:
    text = _DOCNUM_RE.sub(" ", text)
    text = re.sub(r"\b\d+-ФЗ\b", " ", text, flags=re.I)
    text = _FACT_REF_RE.sub(" ", text)
    return re.sub(r"^\s*\d+(?:\.\d+)*[.)]\s+", " ", text, flags=re.M)


def _source_binding_issues(answer, kb, resolved, question_text, extra_text):
    """Hard-check cross-document values; retain soft missing-citation cases.

    This is not semantic entailment. A value found elsewhere in the same cited
    document is a citation-completeness note, not a reason to reject a true fact.
    """
    known = [s for s in resolved if s["doc_id"] in kb.by_id and s["unit_idx"] is not None]
    if not known:
        return []
    out = []
    doc_ids = list(dict.fromkeys(s["doc_id"] for s in resolved if s["doc_id"] in kb.by_id))
    units = [(kb.by_id[s["doc_id"]], kb.by_id[s["doc_id"]].units[s["unit_idx"]]) for s in known]
    inline = []
    for m in _INLINE_UNIT_RE.finditer(answer):
        # "Пункта 9.5 в договоре нет" is a correction, not a claim that 9.5 exists.
        before, after = answer[max(0, m.start() - 35):m.start()], answer[m.end():m.end() + 180]
        denied = re.search(r"(?:нет|отсутствует|не существует)\s*$", before, re.I) or re.search(
            r"^[^.!?;:]{0,150}\b(?:нет|отсутствует|не существует|не предусмотрен)", after, re.I)
        keys = tuple(k for k in m.groups() if k)
        if not denied and keys not in inline:
            inline.append(keys)
    if len(doc_ids) == 1:
        doc = kb.by_id[doc_ids[0]]
        original_units = {u.keys for _, u in units}
        for keys in inline:
            unit = exact_unit(doc, keys)
            if unit is None:
                # Another clearly named source may have been omitted from <sources>.
                # Leave ambiguous multi-source prose to the existing checks.
                if any(exact_unit(d, keys) is not None for d in kb.docs if d.doc_id not in doc_ids):
                    continue
                out.append(dict(type="bad_inline_locator", hard=True,
                                detail=f"в документе «{doc.title}» нет указанного в ответе положения {'.'.join(keys)}"))
                continue
            related = any(unit.keys[:len(k)] == k or k[:len(unit.keys)] == unit.keys for k in original_units)
            if len(inline) == 1 and len(original_units) == 1 and unit.keys not in original_units and not related:
                out.append(dict(type="citation_conflict", hard=True,
                                detail=f"ответ ссылается на {unit.locator} документа «{doc.title}», "
                                       f"но подтверждающий источник указан как {known[0]['locator']}; согласуй ссылку с фактом"))
            else:
                units.append((doc, unit))
    narrow = set()
    for doc, unit in units:
        for u in _scope_units(doc, unit):
            narrow |= support_values(_fact_text(u.text + "\n" + u.heading + "\n" + "\n".join(u.notes)))
    user_values = support_values(question_text + "\n" + extra_text)
    document_values = set()
    for did in doc_ids:
        document_values |= doc_support(kb.by_id[did])
    # Keep operands relevant to the shown calculation. Entire tax articles can
    # contain hundreds of unrelated numbers and drown out the actual operands.
    answer_values = support_values(_fact_text(answer))
    operands = _floats(user_values | ((narrow | document_values) & answer_values))
    pending = []
    for m in number_mentions(_fact_text(answer)):
        if m.kind == "marker" or (m.kind == "word" and m.alts <= {"0", "1"}):
            continue
        if m.supported_by(narrow | user_values):
            continue
        if m.supported_by(document_values):
            continue  # genuine same-document fact; don't convert incomplete metadata into a false refusal
        pending.append(m)
    changed = True
    while changed and pending:
        changed = False
        for m in list(pending):
            if m.kind != "date" and _derivable(m.alts, operands):
                operands.extend(_floats(m.alts))
                pending.remove(m)
                changed = True
    for surface in dict.fromkeys(m.surface for m in pending):
        out.append(dict(type="source_number_mismatch", hard=True,
                        detail=f"число или дата «{surface}» не подтверждается документами, на которые ссылается ответ; "
                               "проверь контрагента, положение и значение, при необходимости добавь правильный источник"))
    return out


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
        numbered = any(u.keys for u in doc.units)
        loose = bool(re.search(r"(абз|раздел|преамбул|шапк|реквизит|приложени)", src["locator"].lower()))
        base_locator = re.sub(r"\s*[,;(]\s*примечания\s+редакци\w*.*$", "", src["locator"], flags=re.I)
        keys = locator_keys(base_locator) if (numbered and not loose) else ()
        unit = exact_unit(doc, keys) if keys else None
        span = doc.index.find(src["quote"]) if src["quote"] else None
        if keys and unit is None:
            issues.append({"type": "bad_locator", "hard": True,
                           "detail": f"в документе «{doc.title}» нет положения «{src['locator']}»"})
        if src["quote"] and span is None:
            issues.append({"type": "bad_quote", "hard": False,
                           "detail": f"цитата не найдена в документе «{doc.title}»: «{src['quote'][:80]}»"})
        # A short quote can occur in several clauses: prefer the declared clause
        # instead of treating the document's first occurrence as authoritative.
        if unit is not None and src["quote"]:
            matched_scope = False
            for u in _scope_units(doc, unit):
                local = TextIndex(u.text).find(src["quote"])
                if local:
                    span = (u.start + local[0], u.start + local[1])
                    matched_scope = True
                    break
            if not matched_scope and unit.heading:
                matched_scope = TextIndex(unit.heading + "\n" + unit.text).find(src["quote"]) is not None
            if not matched_scope and span and not any(
                    u.start <= span[0] < span[1] <= u.end + 1 for u in _scope_units(doc, unit)):
                issues.append(dict(type="source_quote_conflict", hard=True,
                                   detail=f"цитата найдена в другом положении документа «{doc.title}», "
                                          f"а не в указанном источнике {src['locator']}; исправь адрес или цитату"))
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
    catalog = getattr(kb, "_catalog_support", None)          # titles of all documents are shown in the catalogue
    if catalog is None:
        catalog = kb._catalog_support = support_values("\n".join(d.title for d in kb.docs))
    support |= catalog
    question_values = support_values(question_text + "\n" + extra_text)
    support |= question_values
    quote_values: set = set()
    for src in parsed.get("sources", []):
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

    issues.extend(_source_binding_issues(answer, kb, resolved, question_text, extra_text))
    return {"sources": resolved, "issues": issues, "hard": sum(1 for i in issues if i["hard"]),
            "checked_numbers": checked, "has_reference": bool(_REF_HINT_RE.search(answer))}


def _answer_values(answer: str) -> set:
    return support_values(answer)


def issues_text(report: dict) -> str:
    return "\n".join(f"- {i['detail']}" for i in report["issues"] if i["hard"])
