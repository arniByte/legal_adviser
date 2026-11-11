"""Build dist/submission.zip for the platform and check it against the technical regulation.

    python scripts/build_submission.py            # build + static checks
    python scripts/build_submission.py --smoke    # also run the archive against the local mock proxy

Checks: flat archive (no folders), solution.py present, only allowed pinned requirements,
no secrets-looking strings, size limit, and (with --smoke) a full run that must produce a
valid submission.csv for the demo questions.
"""

from __future__ import annotations

import argparse
import csv
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
ALLOWED = ROOT / "materials" / "requirements.txt"
MAX_BYTES = 128 * 1024 * 1024
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9_\-]{20,}|api[_-]?key\s*=\s*['\"][^'\"]{12,})", re.I)


def fail(message: str) -> None:
    print("ОШИБКА:", message)
    sys.exit(1)


def build() -> Path:
    files = sorted(p for p in SRC.iterdir() if p.is_file() and (p.suffix == ".py" or p.name == "requirements.txt"))
    names = [p.name for p in files]
    if "solution.py" not in names:
        fail("нет solution.py")
    if "requirements.txt" not in names:
        fail("нет requirements.txt")
    allowed = {ln.strip() for ln in ALLOWED.read_text(encoding="utf-8").splitlines() if ln.strip()}
    for line in (SRC / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and line.strip() not in allowed:
            fail(f"зависимость {line.strip()!r} не из разрешённого списка materials/requirements.txt")
    for p in files:
        text = p.read_text(encoding="utf-8")
        if SECRET_RE.search(text):
            fail(f"в {p.name} найдено что-то похожее на ключ")
        if re.search(r"pip\s+install|subprocess.*pip", text):
            fail(f"в {p.name} есть вызов pip install")
    DIST.mkdir(exist_ok=True)
    target = DIST / "submission.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.name)                    # flat: files in the archive root
    with zipfile.ZipFile(target) as z:
        if any("/" in n for n in z.namelist()):
            fail("в архиве есть вложенные папки")
    size = target.stat().st_size
    if size > MAX_BYTES:
        fail(f"архив {size} байт больше лимита 128 МБ")
    print(f"Собран {target.relative_to(ROOT)}: {len(files)} файлов, {size / 1024:.1f} КБ")
    print("  " + ", ".join(names))
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
    port = 8799
    work = Path(tempfile.mkdtemp(prefix="la_smoke_"))
    (work / "data").mkdir()
    with zipfile.ZipFile(archive) as z:
        z.extractall(work)
    shutil.copy(ROOT / "materials" / "knowledge-base.zip", work / "data" / "knowledge-base.zip")
    shutil.copy(ROOT / "materials" / "demo_questions.csv", work / "data" / "questions.csv")
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
    args = ap.parse_args()
    archive = build()
    if args.smoke:
        smoke(archive)
