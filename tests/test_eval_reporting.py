"""Evaluation reports must expose missing judgments and compare the same questions."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("legal_run_eval", ROOT / "eval" / "run_eval.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def verdict(score):
    return {"score": score, "raw": score, "fabricated": False, "reasons": []}


def row(rid):
    return {"id": rid, "question": "Что предусмотрено договором?", "golden": "Нужно согласие.",
            "category": "company", "source": "team"}


def answer():
    return {"answer": "Нужно согласие.", "status": "ANSWER", "error": "", "hard_issues": [],
            "passes": 1, "repaired": False, "seconds": 0.1}


def artifact(ids, repeats):
    return {"ours": {rid: answer() for rid in ids}, "judge": {"golden": repeats},
            "baselines": {}, "metadata": {"inputs": {
                "kb_sha256": "same-kb", "question_sha256": {rid: "same-" + rid for rid in ids}}}}


def test_partial_judge_failure_reports_coverage_and_uses_common_ids(tmp_path):
    rows = [row("a"), row("b")]
    repeats = [{"a": verdict(1.0), "b": verdict(0.0)},
               {"a": verdict(0.5), "b": {"error": "quota exhausted"}}]
    table = "\n".join(evaluation.judge_table(rows, {"golden": repeats}))
    assert "Оценено / всего" in table
    assert "| golden | 1 | 2 / 2 |" in table
    assert "| golden | 2 | 1 / 2 |" in table
    # On the common ID a: scores are 1.0 and 0.5, so the mean is 0.75.
    # Averaging scores on unequal successful sets would incorrectly give 0.5.
    assert "| **golden** | **среднее, общие ID** | **1 / 2** | **0.750**" in table
    assert "`b`" in table
    evaluation.report(rows, {rid: answer() for rid in ("a", "b")}, {}, {"golden": repeats}, tmp_path, "test")
    report = (tmp_path / "report.md").read_text()
    assert "Исключённые ID: `b`" in report
    assert "quota exhausted" in report
    assert "2 / 4" in report  # Group denominator includes both repeats.


def test_paired_comparison_excludes_failed_and_unshared_questions():
    left = artifact(["a", "b", "c"], [
        {"a": verdict(0.0), "b": verdict(1.0), "c": verdict(0.0)},
        {"a": verdict(0.0), "b": verdict(1.0), "c": {"error": "timeout"}},
    ])
    right = artifact(["a", "b", "c", "d"], [
        {"a": verdict(1.0), "b": verdict(0.0), "c": verdict(1.0), "d": verdict(0.0)},
        {"a": verdict(1.0), "b": verdict(0.5), "c": verdict(1.0), "d": verdict(0.0)},
        {"a": verdict(0.0), "b": verdict(0.0), "c": verdict(0.0), "d": verdict(0.0)},
    ])
    comparison = evaluation.compare_artifacts(left, right)
    result = comparison["baselines"]["golden"]
    assert result["common_ids"] == ["a", "b"]
    assert result["excluded_ids"] == ["c", "d"]
    assert result["unpaired_right_repeats"] == 1
    assert result["repeats"][0]["delta"] == 0.0
    assert result["repeats"][1]["delta"] == pytest.approx(0.325)
    assert result["mean_delta"] == pytest.approx(0.1625)
    assert result["improved_ids"] == ["a"]
    assert result["worsened_ids"] == ["b"]
    text = "\n".join(evaluation.comparison_lines(comparison, "before", "after"))
    assert "3 / 3" in text and "4 / 4" in text
    assert "Общие оценённые вопросы: 2" in text
    assert "без вывода о статистической значимости" in text


def test_comparison_rejects_changed_question_and_baseline():
    left = artifact(["a", "b", "c"], [{rid: verdict(0.5) for rid in ("a", "b", "c")}])
    right = artifact(["a", "b", "c"], [{rid: verdict(1.0) for rid in ("a", "b", "c")}])
    right["metadata"]["inputs"]["question_sha256"]["a"] = "changed-reference"
    left["baselines"]["golden"] = {"b": "old baseline"}
    right["baselines"]["golden"] = {"b": "new baseline"}
    comparison = evaluation.compare_artifacts(left, right)
    assert comparison["changed_question_ids"] == ["a"]
    result = comparison["baselines"]["golden"]
    assert result["changed_baseline_ids"] == ["b"]
    assert result["common_ids"] == ["c"]


def test_no_common_judgments_has_no_numeric_delta():
    left = artifact(["a"], [{"a": {"error": "timeout"}}])
    right = artifact(["a"], [{"a": verdict(1.0)}])
    result = evaluation.compare_artifacts(left, right)["baselines"]["golden"]
    assert result["common_ids"] == []
    assert result["mean_delta"] is None
    assert result["repeats"][0]["delta"] is None


def test_metadata_allowlist_and_artifacts_preserve_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_API_KEY", "private-key-do-not-save")
    monkeypatch.setenv("LLM_BASE_URL", "https://private-api.example.invalid/v1")
    monkeypatch.setenv("JUDGE_API_KEY", "private-judge-key")
    monkeypatch.setenv("LA_EDIT", "false")
    rows = [row("a")]
    devset = tmp_path / "devset.jsonl"
    devset.write_text(json.dumps(rows[0]))
    metadata = evaluation.build_metadata(evaluation.load_settings(), rows, devset,
                                         workers=2, repeats=1, baseline="golden")
    serialized = json.dumps(metadata)
    assert "private-key-do-not-save" not in serialized
    assert "private-api.example.invalid" not in serialized
    assert "private-judge-key" not in serialized
    assert "api_base" not in metadata["settings"] and "llm_key" not in metadata["settings"]
    assert metadata["settings"]["edit"] is False
    assert len(metadata["inputs"]["kb_sha256"]) == 64
    assert len(metadata["inputs"]["devset_sha256"]) == 64
    assert len(metadata["inputs"]["question_sha256"]["a"]) == 64
    assert "sha" in metadata["git"] and "dirty" in metadata["git"]
    evaluation.report(rows, {"a": answer()}, {}, {}, tmp_path / "run", "test", metadata)
    saved = json.loads((tmp_path / "run" / "answers.json").read_text())
    assert saved["metadata"] == metadata
    assert json.loads((tmp_path / "run" / "metadata.json").read_text()) == metadata
    assert "Оценено 0 / 1" in (tmp_path / "run" / "report.md").read_text()


def test_compare_cli_requires_no_api(monkeypatch, tmp_path, capsys):
    module_dir = tmp_path / "eval"
    runs = module_dir / "runs"
    for label in ("before", "after"):
        run = runs / label
        run.mkdir(parents=True)
        (run / "answers.json").write_text(json.dumps(artifact(["a"], [{"a": verdict(0.5)}])))
    monkeypatch.setattr(evaluation, "__file__", str(module_dir / "run_eval.py"))
    monkeypatch.setattr(evaluation.sys, "argv", ["run_eval.py", "--compare", "before", "after"])
    monkeypatch.setattr(evaluation, "load_settings", lambda: pytest.fail("comparison must not need API settings"))
    evaluation.main()
    assert "+0.0000" in capsys.readouterr().out
    assert (runs / "compare_before_vs_after.md").is_file()


@pytest.fixture
def saved_run_cli(monkeypatch, tmp_path):
    module_dir = tmp_path / "eval"
    runs = module_dir / "runs"
    source = runs / "v7"
    source.mkdir(parents=True)
    original = json.dumps(artifact(["a"], [{"a": verdict(0.5)}]))
    (source / "answers.json").write_text(original)
    (source / "report.md").write_text("original report")
    devset = tmp_path / "devset.jsonl"
    devset.write_text(json.dumps(row("a")))
    settings = evaluation.load_settings()
    settings.api_base, settings.llm_key = "https://unused.example.invalid/v1", "unused-test-key"
    monkeypatch.setattr(evaluation, "__file__", str(module_dir / "run_eval.py"))
    monkeypatch.setattr(evaluation, "load_settings", lambda: settings)
    monkeypatch.setattr(evaluation, "run_ours", lambda *_: pytest.fail("saved answers must not be generated again"))
    return runs, devset, original


def test_rejudge_default_preserves_source_and_uses_unique_labels(monkeypatch, saved_run_cli):
    runs, devset, original = saved_run_cli
    monkeypatch.setattr(evaluation.sys, "argv", ["run_eval.py", "--answers", "v7", "--no-judge",
                                                "--devset", str(devset)])
    evaluation.main()
    evaluation.main()
    assert (runs / "v7" / "answers.json").read_text() == original
    assert (runs / "v7" / "report.md").read_text() == "original report"
    rejudged = list(runs.glob("v7-rejudge-*"))
    assert len(rejudged) == 2
    assert all((path / "answers.json").is_file() for path in rejudged)


def test_existing_label_is_rejected_before_api_work(monkeypatch, saved_run_cli, capsys):
    runs, devset, original = saved_run_cli
    monkeypatch.setattr(evaluation.sys, "argv", ["run_eval.py", "--answers", "v7", "--label", "v7",
                                                "--no-judge", "--devset", str(devset)])
    monkeypatch.setattr(evaluation, "load_settings", lambda: pytest.fail("must reject before any API work"))
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
    assert "--overwrite" in capsys.readouterr().err
    assert (runs / "v7" / "answers.json").read_text() == original


def test_explicit_overwrite_can_replace_existing_run(monkeypatch, saved_run_cli):
    runs, devset, original = saved_run_cli
    monkeypatch.setattr(evaluation.sys, "argv", ["run_eval.py", "--answers", "v7", "--label", "v7",
                                                "--overwrite", "--no-judge", "--devset", str(devset)])
    evaluation.main()
    assert (runs / "v7" / "answers.json").read_text() != original
    saved = json.loads((runs / "v7" / "answers.json").read_text())
    assert saved["metadata"]["evaluation"]["answer_source_run"] == "v7"


@pytest.mark.parametrize("arguments", [
    ["--label", "../escape"],
    ["--answers", "../escape"],
    ["--compare", "../escape", "v7"],
    ["--label", "/tmp/escape"],
    ["--label", "bad\\path"],
    ["--compare", "", "v7"],
])
def test_cli_labels_reject_paths_before_api(monkeypatch, arguments):
    monkeypatch.setattr(evaluation.sys, "argv", ["run_eval.py", *arguments])
    monkeypatch.setattr(evaluation, "load_settings", lambda: pytest.fail("invalid labels must fail before API settings"))
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
