import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "submission"))


@pytest.fixture(scope="session")
def kb():
    from la_kb import load_kb
    return load_kb(ROOT / "materials" / "knowledge-base.zip")


@pytest.fixture(scope="session")
def retriever(kb):
    from la_retrieval import Retriever
    return Retriever(kb)


@pytest.fixture(scope="session")
def demo_questions():
    import csv
    with (ROOT / "materials" / "demo_questions.csv").open(encoding="utf-8-sig", newline="") as f:
        return [(r["id"], r["question"]) for r in csv.DictReader(f)]


@pytest.fixture(scope="session")
def demo_answers():
    import csv
    with (ROOT / "materials" / "demo_output.csv").open(encoding="utf-8-sig", newline="") as f:
        return {r["id"]: r["answer"] for r in csv.DictReader(f)}
