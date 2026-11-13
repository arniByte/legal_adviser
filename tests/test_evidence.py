"""Counterexamples for citation integrity; no model or network is involved."""

import pytest

from la_verify import exact_quote_span, verify


def check(kb, doc_id, locator, quote, answer="Основание приведено в документе."):
    return verify({"status": "ANSWER", "answer": answer,
                   "sources": [{"doc_id": doc_id, "locator": locator, "quote": quote}]},
                  kb, [doc_id], "Уточните условие договора.", require_sources=True)


@pytest.mark.parametrize("old,new", [("450 000", "500 000"), ("включая НДС", "не включая НДС")])
def test_changed_quote_is_rejected_even_when_most_words_match(kb, old, new):
    doc = kb.by_id["D1"]
    quote = doc.unit_by_keys(("3.1",)).text.replace(old, new)
    report = check(kb, doc.doc_id, "п. 3.1", quote)
    assert report["hard"] and not report["sources"][0]["ok"]
    assert any(i["type"] == "bad_quote" for i in report["issues"])


def test_quote_must_belong_to_the_named_clause(kb):
    quote = kb.by_id["D1"].unit_by_keys(("5.1",)).text
    report = check(kb, "D1", "п. 3.1", quote)
    assert report["hard"] and not report["sources"][0]["ok"]


def test_real_quote_does_not_rescue_a_nonexistent_clause(kb):
    quote = kb.by_id["D1"].unit_by_keys(("3.1",)).text
    report = check(kb, "D1", "п. 3.999", quote)
    assert any(i["type"] == "bad_locator" and i["hard"] for i in report["issues"])
    assert not report["sources"][0]["ok"]


def test_source_uses_canonical_locator_and_original_text(kb):
    doc = kb.by_id["D1"]
    original = doc.unit_by_keys(("3.1",)).text
    report = check(kb, doc.doc_id, "пункт 3.1", original.upper().replace(" ", "\u00a0"))
    source = report["sources"][0]
    assert not report["hard"] and source["ok"]
    assert source["locator"] == "п. 3.1"
    assert source["quote"] == doc.text[source["start"]:source["end"]] == original


def test_precise_existing_clause_without_quote_is_resolved(kb):
    report = check(kb, "D1", "п. 3.1", "")
    assert not report["hard"] and "450 000" in report["sources"][0]["quote"]


def test_quoted_amended_clause_is_a_real_numbered_provision(kb):
    doc = kb.by_id["D4"]
    unit = doc.unit_by_keys(("3.2",))
    assert unit is not None and unit.text.startswith('«3.2.')
    assert doc.text[unit.start:unit.end] == unit.text
    report = check(kb, doc.doc_id, "п. 3.2", unit.text)
    assert not report["hard"] and report["sources"][0]["ok"]


def test_document_only_without_evidence_is_rejected(kb):
    report = check(kb, "D1", "", "")
    assert report["hard"] and not report["sources"][0]["ok"]


def test_missing_sources_block_answer_but_not_diagnostic_reference_checks(kb):
    parsed = {"status": "ANSWER", "answer": "Плата — 450 000 рублей.", "sources": []}
    assert verify(parsed, kb, ["D1"], "")["hard"] == 0
    assert verify(parsed, kb, ["D1"], "", require_sources=True)["hard"] > 0
    parsed["status"] = "NOT_IN_KB"
    assert verify(parsed, kb, ["D1"], "", require_sources=True)["hard"] == 0


def test_quote_without_locator_is_resolved_to_its_actual_unit(kb):
    quote = kb.by_id["D1"].unit_by_keys(("5.1",)).text
    source = check(kb, "D1", "", quote)["sources"][0]
    assert source["ok"] and source["locator"] == "п. 5.1"


def test_wrong_article_cannot_be_attached_to_a_real_quote(kb):
    doc = next(d for d in kb.law_docs if d.meta["cite"] == "ТК РФ, ст. 70")
    report = check(kb, doc.doc_id, "ст. 99, ч. 5", doc.unit_by_keys(("5",)).text)
    assert report["hard"] and not report["sources"][0]["ok"]


def test_ambiguous_subpoint_needs_its_parent(kb):
    doc = next(d for d in kb.law_docs if d.meta["cite"] == "НК РФ, ст. 164")
    duplicates = [u for u in doc.units if u.kind == "subpoint" and u.keys[-1] == "1"]
    assert len(duplicates) > 1
    quote = duplicates[0].text
    assert check(kb, doc.doc_id, "подп. 1", quote)["hard"]
    report = check(kb, doc.doc_id, duplicates[0].locator, quote)
    assert not report["hard"] and report["sources"][0]["ok"]


def test_parent_point_includes_children_but_not_the_next_point(kb):
    doc = next(d for d in kb.law_docs if d.meta["cite"] == "НК РФ, ст. 112")
    child = doc.unit_by_keys(("1", "2.1"))
    assert not check(kb, doc.doc_id, "п. 1", child.text)["hard"]
    next_point = doc.unit_by_keys(("2",))
    assert check(kb, doc.doc_id, "п. 1", next_point.text)["hard"]


def test_two_peer_points_are_not_silently_treated_as_a_subpoint(kb):
    doc = next(d for d in kb.law_docs if d.meta["cite"] == "НК РФ, ст. 112")
    child = doc.unit_by_keys(("1", "2"))
    assert check(kb, doc.doc_id, "п. 1, п. 2", child.text)["hard"]
    assert not check(kb, doc.doc_id, "п. 1, подп. 2", child.text)["hard"]


@pytest.mark.parametrize("text,quote", [
    ("450 000 рублей", "50 000 рублей"),
    ("не разрешено", "разрешен"),
    ("ставка -5%", "ставка 5%"),
    ("от 1 до 5%", "от 1 до 5"),
    ("Ставка 0,5%.", "5%"),
    ("Ставка -5%.", "5%"),
    ("Сумма 450 000 рублей.", "000 рублей"),
])
def test_quote_never_drops_digits_or_word_boundaries(text, quote):
    assert exact_quote_span(text, quote) is None


def test_typographic_variants_preserve_original_offsets():
    text = 'Начало. «Срок — 5\u00a0дней». Конец.'
    quote = '"срок - 5 дней"'
    span = exact_quote_span(text, quote)
    assert span and text[span[0]:span[1]] == '«Срок — 5\u00a0дней»'


def test_few_shot_sources_pass_strict_verification(kb):
    from la_prompts import EXAMPLES, _resolve
    for example in EXAMPLES:
        sources = [{"doc_id": _resolve(kb, key), "locator": loc, "quote": quote}
                   for key, loc, quote in example["sources"]]
        report = verify({"status": "ANSWER" if sources else "NOT_IN_KB", "answer": example["answer"],
                         "sources": sources}, kb, [d.doc_id for d in kb.docs], example["q"], require_sources=True)
        assert report["hard"] == 0, (example["q"], report["issues"])
