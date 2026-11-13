"""Check evaluation data integrity without grading its legal conclusions."""

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

from la_verify import verify


ROOT = Path(__file__).resolve().parent.parent
HOLDOUT = ROOT / "eval" / "holdout.jsonl"


def load_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalized_question(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


ROWS = load_rows(HOLDOUT)


def test_holdout_has_24_unique_questions_and_requested_scenarios():
    assert len(ROWS) == 24
    assert len({row["id"] for row in ROWS}) == 24
    questions = {normalized_question(row["question"]) for row in ROWS}
    assert len(questions) == 24
    previous = load_rows(ROOT / "eval" / "devset.jsonl")
    assert questions.isdisjoint(normalized_question(row["question"]) for row in previous)
    from la_prompts import EXAMPLES
    assert questions.isdisjoint(normalized_question(example["q"]) for example in EXAMPLES)
    counts = Counter(row["category"] for row in ROWS)
    assert counts["crossdoc"] == 4
    assert counts["temporal"] == 8
    assert counts["ambiguity"] + counts["premise"] == 4
    assert counts["partial"] + counts["not_in_kb"] == 4
    assert counts["dialog"] == 4
    assert sum(counts.values()) == 24


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["id"])
def test_holdout_row_schema_and_source_resolution(row, kb):
    required = {"id", "category", "group", "question", "golden", "must", "must_not", "expected_sources"}
    assert required <= row.keys()
    for name in ("id", "category", "group", "question", "golden"):
        assert isinstance(row[name], str) and row[name].strip()
    assert re.fullmatch(r"h\d{2}", row["id"])
    for name in ("must", "must_not"):
        assert isinstance(row[name], list)
        assert all(isinstance(value, str) and value.strip() for value in row[name])
    assert isinstance(row["expected_sources"], list)
    by_filename = {doc.filename: doc for doc in kb.docs}
    unique = set()
    for source in row["expected_sources"]:
        assert isinstance(source, dict)
        assert {"filename", "locator"} <= source.keys()
        assert isinstance(source["filename"], str) and source["filename"] in by_filename
        assert isinstance(source["locator"], str)
        quote = source.get("quote", "")
        assert isinstance(quote, str)
        assert source["locator"].strip() or quote.strip(), "Unnumbered sources need an exact quotation"
        key = source["filename"], source["locator"], quote
        assert key not in unique
        unique.add(key)
        doc = by_filename[source["filename"]]
        # An empty answer checks only existence and placement of the source;
        # golden is deliberately never passed to numeric/answer heuristics.
        report = verify({"answer": "", "sources": [{"doc_id": doc.doc_id,
                         "locator": source["locator"], "quote": quote}]}, kb, [doc.doc_id], "")
        assert len(report["sources"]) == 1
        assert report["sources"][0]["ok"], (row["id"], source, report["issues"])
