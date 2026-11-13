"""Reproducible ref builds must not disturb a teammate's working branch."""

import hashlib
import importlib.util
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def build_repo(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_submission.py"
    spec = importlib.util.spec_from_file_location("submission_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    root = tmp_path / "repo"
    (root / "submission").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "submission" / "solution.py").write_text('print("baseline")\n', encoding="utf-8")
    (root / "submission" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "submission" / "requirements.txt").write_text("httpx==0.28.1\n", encoding="utf-8")
    (root / "data" / "allowed_requirements.txt").write_text("httpx==0.28.1\n", encoding="utf-8")
    (root / "data" / "knowledge-base.zip").write_bytes(b"public fixture KB")
    (root / "data" / "demo_questions.csv").write_text("id,question\n1,fixture\n", encoding="utf-8")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    git("init", "-q", "-b", "main")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "baseline")
    sha = git("rev-parse", "HEAD").decode().strip()
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "SRC", root / "submission")
    monkeypatch.setattr(builder, "DIST", root / "dist")
    monkeypatch.setattr(builder, "ALLOWED", root / "data" / "allowed_requirements.txt")
    return builder, root, sha, git


def test_ref_uses_its_sources_and_allowed_list_without_checkout(build_repo, tmp_path):
    builder, root, sha, git = build_repo
    git("switch", "-qc", "teammate")
    (root / "submission" / "solution.py").write_text('print("candidate")\n', encoding="utf-8")
    (root / "submission" / "requirements.txt").write_text("numpy==2.2.6\n", encoding="utf-8")
    (root / "data" / "allowed_requirements.txt").write_text("numpy==2.2.6\n", encoding="utf-8")
    before = git("status", "--porcelain=v1", "-z")

    target = builder.build(ref=sha, output=tmp_path / "baseline.zip")

    with zipfile.ZipFile(target) as archive:
        assert archive.read("solution.py") == b'print("baseline")\n'
        assert archive.read("requirements.txt") == b"httpx==0.28.1\n"
    assert git("branch", "--show-current").strip() == b"teammate"
    assert git("rev-parse", "HEAD").decode().strip() == sha
    assert git("status", "--porcelain=v1", "-z") == before
    assert (root / "submission" / "solution.py").read_text() == 'print("candidate")\n'


def test_ref_zip_is_repeatable_and_manifest_hashes_match(build_repo, tmp_path):
    builder, root, sha, _ = build_repo
    first = builder.build(ref=sha, output=tmp_path / "first.zip")
    second = builder.build(ref="HEAD", output=tmp_path / "second.zip")
    assert first.read_bytes() == second.read_bytes()

    manifest = json.loads(Path(str(first) + ".manifest.json").read_text())
    assert manifest["source"]["git_sha"] == sha
    assert manifest["source"]["kind"] == "git_ref"
    assert manifest["archive"]["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert manifest["archive"]["size_bytes"] == first.stat().st_size
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {"solution.py", "helper.py", "requirements.txt"}
        for name in archive.namelist():
            content = archive.read(name)
            assert manifest["files"][name] == {"sha256": hashlib.sha256(content).hexdigest(),
                                                 "size_bytes": len(content)}
    assert manifest["allowed_requirements_sha256"] == hashlib.sha256(
        (root / "data" / "allowed_requirements.txt").read_bytes()).hexdigest()
    assert manifest["local_smoke_inputs"]["source"] == "current_working_tree"
    assert manifest["local_smoke_inputs"]["knowledge_base"]["sha256"] == hashlib.sha256(
        (root / "data" / "knowledge-base.zip").read_bytes()).hexdigest()
    assert manifest["python"]["version"]


def test_working_build_records_dirty_files_without_env_values(build_repo, monkeypatch):
    builder, root, sha, _ = build_repo
    (root / "submission" / "solution.py").write_text('print("candidate")\n', encoding="utf-8")
    secret = "environment-value-must-never-be-recorded"
    monkeypatch.setenv("LLM_API_KEY", secret)

    target = builder.build()

    assert target == root / "dist" / "submission.zip"
    raw_manifest = Path(str(target) + ".manifest.json").read_text()
    manifest = json.loads(raw_manifest)
    assert secret not in raw_manifest
    assert manifest["source"]["kind"] == "working_tree"
    assert manifest["source"]["git_sha"] == sha
    assert manifest["source"]["dirty"] is True
    assert {change["path"] for change in manifest["source"]["changes"]} == {"submission/solution.py"}


def test_ref_does_not_borrow_newer_allowlist(build_repo, tmp_path):
    builder, root, _, git = build_repo
    (root / "submission" / "requirements.txt").write_text("numpy==2.2.6\n", encoding="utf-8")
    git("add", "submission/requirements.txt")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "invalid deps")
    # The current allowlist accepts this pin, but the ref's own allowlist does not.
    (root / "data" / "allowed_requirements.txt").write_text("numpy==2.2.6\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="1"):
        builder.build(ref="HEAD", output=tmp_path / "invalid.zip")


def test_default_build_still_works_for_downloaded_source_without_git(build_repo):
    builder, root, _, _ = build_repo
    shutil.rmtree(root / ".git")
    target = builder.build()
    manifest = json.loads(Path(str(target) + ".manifest.json").read_text())
    assert manifest["source"]["git_sha"] is None
    assert manifest["source"]["dirty"] is None
    assert target.is_file()
