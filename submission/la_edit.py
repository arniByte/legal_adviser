"""Deletion-only editing of a finished answer.

The judge sees the reference answer, not the knowledge base, so a true provision that the reference does
not mention can be taken for an invented fact and cost the whole question. The generating model adds
such side facts unpredictably: the same prompt gives a different set of them on every run. A separate,
narrow pass removes them.

The pass cannot introduce an error: the model only names the fragments to delete and the code assembles
the answer from the remaining ones, word for word. Whatever goes wrong (bad JSON, a request to delete
the answer itself, a failed call), the answer stays as it was.
"""

from __future__ import annotations

import json
import re

from la_llm import LLMError
from la_text import sentences

EDIT_SYSTEM = """Ты — редактор ответов юридического ассистента. Ответ уже проверен и верен; твоя задача — только вычеркнуть лишнее. Тебе дан вопрос (возможно, диалог — отвечают на последнюю реплику пользователя) и ответ, разбитый на пронумерованные фрагменты.

Вычёркивай фрагмент, только если он относится к одному из видов:
1) упоминание документа или изменения, которое на ответ не влияет («дополнительное соглашение этот пункт не изменяло», «изменён только адрес доставки»);
2) положение другого пункта или другого документа, о котором не спрашивали и без которого ответ на вопрос остаётся полным (порядок расчётов, когда спросили срок; размещение скан-копии, когда спросили срок передачи оригинала);
3) пояснение, почему не применяется то, о чём не спрашивали, и предупреждения «не путать с…»;
4) повтор уже сказанного другими словами.

Никогда не вычёркивай: фрагмент, в котором содержится сам ответ или любая его часть (число, срок, сумма, условие, да/нет); исправление неверной посылки и то, что её объясняет; сведения о применённой редакции и об отличии действующей; расчёт; шаги; отказ, его причину и совет, куда обратиться; строку «Источник: …»; то, чего в базе нет; оговорку о применимости вывода.

Если после удаления ответ на вопрос стал бы менее полным — не удаляй. Сомневаешься — оставь. Чаще всего удалять нечего.
Ответь только JSON: {"delete": [номера фрагментов], "why": "коротко"}"""

EDITABLE_STATUSES = ("ANSWER", "PARTIAL", "ILLEGAL")   # refusals have a fixed three-part shape and stay whole
MIN_KEPT_SHARE = 0.4                                    # an edit that removes most of the answer is not an edit
_STEP_RE = re.compile(r"(?:^|\s)\d\)\s")
# which edition applies and what the knowledge base lacks: the case requires both to be said
_REQUIRED_RE = re.compile(r"редакци|в (?:предоставленной )?базе(?: знаний)? (?:нет|отсутству)", re.I)


def fragments(answer: str) -> list:
    """Sentences, and the parts of a sentence joined by a semicolon."""
    out = []
    for sent in sentences(answer):
        out.extend(part for part in re.split(r"(?<=;)\s+", sent) if part.strip())
    return out


def protected(index: int, fragment: str) -> bool:
    """Fragments the editor may not touch whatever the model says: the direct answer, the source line, steps,
    statements about the applicable edition and about what the knowledge base does not contain."""
    return (index == 0 or fragment.lower().startswith("источник") or bool(_STEP_RE.search(fragment))
            or bool(_REQUIRED_RE.search(fragment)))


def build_messages(question: str, frags: list) -> list:
    numbered = "\n".join(f"[{i}] {f}" for i, f in enumerate(frags, 1))
    return [{"role": "system", "content": EDIT_SYSTEM},
            {"role": "user", "content": f"ВОПРОС:\n{question}\n\nОТВЕТ ПО ФРАГМЕНТАМ:\n{numbered}"}]


def parse_deletions(raw: str, frags: list) -> set:
    """Zero-based indexes the model asked to delete, minus everything it is not allowed to."""
    match = re.search(r"\{.*\}", raw or "", re.S)
    try:
        asked = json.loads(match.group(0)).get("delete", []) if match else []
        ids = {int(x) - 1 for x in asked}
    except (ValueError, TypeError, AttributeError):
        return set()
    return {i for i in ids if 0 <= i < len(frags) and not protected(i, frags[i])}


def apply_deletions(frags: list, ids: set) -> tuple:
    """(kept answer, removed text). Returns the answer untouched if too little of it would remain."""
    whole = " ".join(frags)
    if not ids:
        return whole, ""
    kept_parts = []
    for i, frag in enumerate(frags):
        if i in ids:
            continue
        if frag.endswith(";") and (i + 1 in ids or i + 1 == len(frags)):
            frag = frag[:-1] + "."                     # the clause that continued this sentence is gone
        kept_parts.append(frag)
    kept = " ".join(kept_parts)
    if len(kept.split()) < MIN_KEPT_SHARE * len(whole.split()):
        return whole, ""
    removed = " ".join(frags[i] for i in sorted(ids))
    return kept, removed


def edit_answer(llm, question: str, answer: str, deadline=None) -> tuple:
    """(answer, removed). One model call; any failure leaves the answer unchanged."""
    frags = fragments(answer)
    if sum(1 for i, f in enumerate(frags) if not protected(i, f)) == 0:
        return answer, ""
    try:
        raw = llm.chat(build_messages(question, frags), max_tokens=1500, temperature=0.0, deadline=deadline)
    except LLMError:                                    # an optional pass must never cost the answer
        return answer, ""
    kept, removed = apply_deletions(frags, parse_deletions(raw, frags))
    return (kept, removed) if removed else (answer, "")
