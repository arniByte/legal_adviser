"""Build dist/submission.zip for the platform and check it against the technical regulation.

    python scripts/build_submission.py            # build + static checks
    python scripts/build_submission.py --smoke    # also run the archive against the local mock proxy
    python scripts/build_submission.py --ref HEAD --output dist/baseline.zip

Checks: flat archive (no folders), solution.py present, only allowed pinned requirements,
no secrets-looking strings, size limit, and (with --smoke) a full run that must produce a
valid submission.csv for the demo questions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "submission"
DIST = ROOT / "dist"
ALLOWED = ROOT / "data" / "allowed_requirements.txt"
MAX_BYTES = 128 * 1024 * 1024
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9_\-]{20,}|api[_-]?key\s*=\s*['\"][^'\"]{12,})", re.I)


def fail(message: str) -> None:
    print("ОШИБКА:", message)
    sys.exit(1)


def _git(*args: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    except FileNotFoundError:
        fail("для --ref нужен установленный git")
    if result.returncode:
        fail("git: " + result.stderr.decode("utf-8", errors="replace").strip()[:500])
    return result.stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _working_state() -> dict:
    # Porcelain includes paths/status only: never capture diffs or environment values.
    entries = iter(_git("status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0"))
    changes = []
    for entry in entries:
        if not entry:
            continue
        state = {"status": entry[:2].decode("ascii"), "path": os.fsdecode(entry[3:])}
        if "R" in state["status"] or "C" in state["status"]:
            state["original_path"] = os.fsdecode(next(entries))
        changes.append(state)
    return {"dirty": bool(changes), "changes": changes}


def _working_source() -> dict:
    # A downloaded source ZIP may have no .git: preserve the original build() use.
    try:
        probe = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"], capture_output=True)
    except FileNotFoundError:
        probe = None
    if probe is None or probe.returncode or Path(os.fsdecode(probe.stdout).strip()).resolve() != ROOT.resolve():
        return {"kind": "working_tree", "git_sha": None, "dirty": None, "changes": [],
                "git_state": "unavailable"}
    return {"kind": "working_tree", "git_sha": _git("rev-parse", "HEAD").decode().strip(), **_working_state()}


def _input_hash(path: Path) -> dict:
    return {"path": str(path.relative_to(ROOT)),
            "sha256": _sha256(path.read_bytes()) if path.is_file() else None}


def build(ref: str | None = None, output: str | Path | None = None) -> Path:
    if ref is not None:
        sha = _git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
        files = {}
        for entry in _git("ls-tree", "-z", sha + ":submission").split(b"\0"):
            if not entry:
                continue
            meta, raw_name = entry.split(b"\t", 1)
            mode, kind, _ = meta.split()
            name = raw_name.decode("utf-8")
            if Path(name).suffix != ".py" and name != "requirements.txt":
                continue
            if kind != b"blob" or mode not in (b"100644", b"100755"):
                fail(f"submission/{name} в ref должен быть обычным файлом")
            files[name] = _git("show", f"{sha}:submission/{name}")
        allowed_bytes = _git("show", sha + ":data/allowed_requirements.txt")
        source = {"kind": "git_ref", "requested_ref": ref, "git_sha": sha}
    else:
        files = {p.name: p.read_bytes() for p in sorted(SRC.iterdir())
                 if p.is_file() and (p.suffix == ".py" or p.name == "requirements.txt")}
        allowed_bytes = ALLOWED.read_bytes()
        source = _working_source()
        sha = source["git_sha"]
    names = sorted(files)
    if "solution.py" not in names:
        fail("нет solution.py")
    if "requirements.txt" not in names:
        fail("нет requirements.txt")
    allowed = {ln.strip() for ln in allowed_bytes.decode("utf-8").splitlines() if ln.strip()}
    for line in files["requirements.txt"].decode("utf-8").splitlines():
        if line.strip() and line.strip() not in allowed:
            fail(f"зависимость {line.strip()!r} не из разрешённого списка data/allowed_requirements.txt")
    for name, content in files.items():
        text = content.decode("utf-8")
        if SECRET_RE.search(text):
            fail(f"в {name} найдено что-то похожее на ключ")
        if re.search(r"pip\s+install|subprocess.*pip", text):
            fail(f"в {name} есть вызов pip install")
    target = Path(output) if output is not None else DIST / "submission.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            # Fixed order, time and permissions make repeated builds byte-identical.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(target) as z:
        if any("/" in n for n in z.namelist()):
            fail("в архиве есть вложенные папки")
    size = target.stat().st_size
    if size > MAX_BYTES:
        fail(f"архив {size} байт больше лимита 128 МБ")
    manifest = {
        "schema_version": 1,
        "source": source,
        "python": {"version": sys.version.split()[0], "implementation": sys.implementation.name},
        "files": {name: {"sha256": _sha256(files[name]), "size_bytes": len(files[name])} for name in names},
        "allowed_requirements_sha256": _sha256(allowed_bytes),
        "archive": {"name": target.name, "sha256": _sha256(target.read_bytes()), "size_bytes": size},
        "local_smoke_inputs": {
            "source": "current_working_tree",
            "knowledge_base": _input_hash(ROOT / "data" / "knowledge-base.zip"),
            "questions": _input_hash(ROOT / "data" / "demo_questions.csv"),
            "note": "--smoke uses the current public demo KB/questions, including with --ref; no real API quality score.",
        },
    }
    manifest_path = target.with_name(target.name + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Собран {target}: {len(files)} файлов, {size / 1024:.1f} КБ")
    print("  " + ", ".join(names))
    print(f"Manifest: {manifest_path}; source={sha}" + (" (dirty)" if source.get("dirty") else ""))
    return target


def validate_csv(path: Path, questions: Path) -> None:
    with questions.open(encoding="utf-8-sig", newline="") as f:
        ids = [r["id"].strip() for r in csv.DictReader(f)]
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    if header != ["id", "answer"]:
        fail(f"заголовок {header} вместо id,answer")
    got = [r[0] for r in rows]
    if got != ids:
        fail("id в submission.csv не совпадают с вопросами")
    if len(set(got)) != len(got):
        fail("повторяющиеся id")
    for r in rows:
        if len(r) != 2 or not r[1].strip():
            fail(f"пустой или некорректный ответ для id={r[0]}")
        if len(r[1]) > 10000:
            fail(f"ответ длиннее 10 000 символов для id={r[0]}")
    print(f"submission.csv корректен: {len(rows)} ответов")


def smoke(archive: Path) -> None:
    print("Smoke: текущая публичная demo-база и вопросы из working tree, в том числе для --ref; API — локальная заглушка.")
    port = 8799
    work = Path(tempfile.mkdtemp(prefix="la_smoke_"))
    (work / "data").mkdir()
    with zipfile.ZipFile(archive) as z:
        z.extractall(work)
    shutil.copy(ROOT / "data" / "knowledge-base.zip", work / "data" / "knowledge-base.zip")
    shutil.copy(ROOT / "data" / "demo_questions.csv", work / "data" / "questions.csv")
    mock = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "mock_llm.py"), "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        env = dict(os.environ, LLM_BASE_URL=f"http://127.0.0.1:{port}/v1", LLM_API_KEY="mock",
                   DATA_DIR="data", LA_CACHE_DIR=str(work / "cache"))
        env.pop("PYTHONPATH", None)
        t0 = time.time()
        run = subprocess.run([sys.executable, "solution.py"], cwd=work, env=env, capture_output=True, text=True,
                             timeout=300)
        print(run.stderr.strip().splitlines()[-1] if run.stderr.strip() else "(нет логов)")
        if run.returncode != 0:
            fail(f"solution.py завершился с кодом {run.returncode}\n{run.stderr[-800:]}")
        validate_csv(work / "submission.csv", work / "data" / "questions.csv")
        print(f"Дымовой прогон на заглушке API: {time.time() - t0:.1f} с")
    finally:
        mock.terminate()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="run the archive against the local mock proxy")
    ap.add_argument("--ref", help="build committed submission files from this git ref, without checkout")
    ap.add_argument("--output", type=Path, help="ZIP path (default: dist/submission.zip)")
    args = ap.parse_args()
    archive = build(ref=args.ref, output=args.output)
    if args.smoke:
        smoke(archive)
