"""Local evaluation that mirrors the organisers' LLM-as-judge protocol.

    python eval/run_eval.py                 # our answers + baseline + judge + report
    python eval/run_eval.py --no-judge      # quick checks only (no judge calls)
    python eval/run_eval.py --limit 10 --only temporal,twins

Needs the same environment as the solution (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL ...).
The judge can use another model: JUDGE_MODEL, JUDGE_BASE_URL, JUDGE_API_KEY.

What is reproduced from the technical regulation:
  * our answer is compared with a baseline answer, the reference answer is the yardstick;
  * two rounds with swapped positions (A = baseline, B = ours; then A = ours, B = baseline);
  * scale 1 / 0.75 / 0.5 / 0.25 / 0 relative to the baseline, question score = mean of two rounds;
  * an invented fact zeroes the question;
  * MatchRate = mean score, WinRate = W / (W + L), Score = 0.7 * MatchRate + 0.3 * WinRate.
The organisers' judge prompt, baseline and questions are not public, so absolute numbers
will differ from the leaderboard; the tool is for comparing our own versions.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "submission"))

from la_config import load_settings  # noqa: E402
from la_kb import load_kb  # noqa: E402
from la_llm import LLMClient, LLMError  # noqa: E402
from la_pipeline import Assistant, parse_dialog, retrieval_query  # noqa: E402
from la_retrieval import Retriever  # noqa: E402

JUDGE_SYSTEM = """Ты — строгий судья качества ответов юридического ассистента. Ассистент обязан отвечать только по базе нормативных документов; эталонный ответ утверждён экспертами и считается верным.

Тебе даны вопрос (возможно, с историей диалога), эталонный ответ и два ответа: A и B. Оцени ответ B относительно ответа A, ориентируясь на эталон:
1.0 — B значительно лучше A;
0.75 — B немного лучше A;
0.5 — ответы сопоставимы по качеству;
0.25 — B немного хуже A;
0.0 — B значительно хуже A.

Что считается качеством: фактическое совпадение с эталоном по существу (суммы, сроки, номера пунктов, применимая редакция, контрагент); указание на ошибку в вопросе, если она есть; обоснованный отказ, если по эталону сведений в базе нет, и содержательный ответ, если по эталону они есть; ссылки на документ и пункт; отсутствие лишнего и неподтверждённого.

Выдуманный факт — утверждение о норме, сумме, сроке, документе или пункте, которое противоречит эталону или которого по эталону в базе нет. Отметь его наличие отдельно для A и для B.

Ответь только JSON-объектом: {"score": число из списка, "fabricated_a": true|false, "fabricated_b": true|false, "reason": "одно предложение"}"""


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text.lower().replace("ё", "е"))


def quick_checks(row: dict, answer: str) -> dict:
    a = norm(answer)
    missing = [m for m in row.get("must", []) if norm(m) not in a]
    violated = [m for m in row.get("must_not", []) if norm(m) in a]
    return {"missing": missing, "violated": violated, "words": len(answer.split())}


def load_devset(path: Path, only, limit) -> list:
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    if only:
        rows = [r for r in rows if r["category"] in only or r["id"] in only]
    return rows[:limit] if limit else rows


def run_ours(rows: list, settings, workers: int) -> dict:
    kb = load_kb(ROOT / "data" / "knowledge-base.zip")
    llm = LLMClient(settings)
    retriever = Retriever(kb, llm, settings)
    dense = retriever.build_embeddings()
    qvecs = retriever.embed_queries([retrieval_query(parse_dialog(r["question"])) for r in rows]) if dense \
        else [None] * len(rows)
    assistant = Assistant(kb, retriever, llm, settings)

    def one(i: int) -> dict:
        r = assistant.answer(rows[i]["question"], qvec=qvecs[i], deadline=time.monotonic() + 240)
        return {"answer": r.answer, "status": r.status, "passes": r.passes, "repaired": r.repaired,
                "hard_issues": [x["detail"] for x in r.issues if x["hard"]], "seconds": r.timings.get("total", 0.0),
                "error": r.error, "sources": [f"{s['doc_id']} {s['locator']}" for s in r.sources],
                "analysis": r.analysis}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one, range(len(rows))))
    print(f"ours: {len(out)} answers; dense search {'on' if dense else 'off'}; reranker "
          f"{llm.reranker_model if llm.reranker_ok else 'off'}; llm calls {llm.stats['chat_calls']}; tokens in/out "
          f"{llm.stats['prompt_tokens']}/{llm.stats['completion_tokens']}", flush=True)
    return {rows[i]["id"]: out[i] for i in range(len(rows))}


def run_baseline(rows: list, cache: Path) -> dict:
    """Answers of the organisers' sample submission (data/sample_submission) on the same questions."""
    if cache.is_file():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if all(r["id"] in cached for r in rows):
            print(f"baseline: cached ({cache.name})", flush=True)
            return cached
    work = Path(tempfile.mkdtemp(prefix="la_baseline_"))
    (work / "data").mkdir()
    (work / "data" / "kb.zip").write_bytes((ROOT / "data" / "knowledge-base.zip").read_bytes())
    with (work / "data" / "questions.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "question"])
        w.writerows([r["id"], r["question"]] for r in rows)
    env = dict(os.environ, DATA_DIR="data")
    run = subprocess.run([sys.executable, str(ROOT / "data" / "sample_submission" / "solution.py")], cwd=work,
                         env=env, capture_output=True, text=True, timeout=1500)
    if run.returncode != 0:
        raise RuntimeError("baseline failed: " + run.stderr[-400:])
    with (work / "submission.csv").open(encoding="utf-8", newline="") as f:
        out = {r["id"]: r["answer"] for r in csv.DictReader(f)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"baseline: {len(out)} answers", flush=True)
    return out


def judge_pair(judge: LLMClient, question: str, golden: str, a: str, b: str) -> dict:
    user = f"ВОПРОС:\n{question}\n\nЭТАЛОННЫЙ ОТВЕТ:\n{golden}\n\nОТВЕТ A:\n{a}\n\nОТВЕТ B:\n{b}"
    last = ""
    for _ in range(3):
        try:
            raw = judge.chat([{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
                             max_tokens=1200, temperature=0.0)
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {}
            score = float(data.get("score"))
            if score in (0.0, 0.25, 0.5, 0.75, 1.0):
                return {"score": score, "fab_a": bool(data.get("fabricated_a")), "fab_b": bool(data.get("fabricated_b")),
                        "reason": str(data.get("reason", ""))[:300]}
            last = f"bad score {score}"
        except (LLMError, ValueError, TypeError, AttributeError) as exc:
            last = f"{type(exc).__name__}: {str(exc)[:80]}"
    return {"error": last}


def judge_all(rows: list, ours: dict, base: dict, settings, workers: int) -> dict:
    js = load_settings()
    js.llm_model = os.getenv("JUDGE_MODEL", "").strip() or settings.llm_model
    js.api_base = (os.getenv("JUDGE_BASE_URL", "").strip() or settings.api_base).rstrip("/")
    js.llm_key = os.getenv("JUDGE_API_KEY", "").strip() or settings.llm_key
    judge = LLMClient(js)

    def one(row: dict) -> dict:
        ans, bas = ours[row["id"]]["answer"], base[row["id"]]
        r1 = judge_pair(judge, row["question"], row["golden"], bas, ans)      # A = baseline, B = ours
        r2 = judge_pair(judge, row["question"], row["golden"], ans, bas)      # A = ours, B = baseline
        if "error" in r1 or "error" in r2:
            return {"error": r1.get("error") or r2.get("error")}
        s1, s2 = r1["score"], 1.0 - r2["score"]
        fabricated = r1["fab_b"] or r2["fab_a"]
        return {"score": 0.0 if fabricated else (s1 + s2) / 2, "rounds": [s1, s2], "fabricated": fabricated,
                "baseline_fabricated": r1["fab_a"] or r2["fab_b"], "reasons": [r1["reason"], r2["reason"]]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one, rows))
    print(f"judge ({js.llm_model}): {judge.stats['chat_calls']} calls", flush=True)
    return {rows[i]["id"]: out[i] for i in range(len(rows))}


def metrics(scores: list) -> dict:
    if not scores:
        return {"n": 0, "match_rate": 0.0, "win_rate": 0.5, "score": 0.0, "wins": 0, "losses": 0, "ties": 0}
    wins = sum(1 for s in scores if s > 0.5)
    losses = sum(1 for s in scores if s < 0.5)
    mr = sum(scores) / len(scores)
    wr = wins / (wins + losses) if wins + losses else 0.5
    return {"n": len(scores), "match_rate": round(mr, 4), "win_rate": round(wr, 4),
            "score": round(0.7 * mr + 0.3 * wr, 4), "wins": wins, "losses": losses, "ties": len(scores) - wins - losses}


def report(rows: list, ours: dict, base: dict, judged: dict, out_dir: Path, label: str) -> None:
    lines = [f"# Локальная оценка: {label}", ""]
    checks = {r["id"]: quick_checks(r, ours[r["id"]]["answer"]) for r in rows}
    n = len(rows)
    lines += ["## Проверки без судьи", "",
              f"- ответов: {n}; с ошибкой модели: {sum(1 for r in rows if ours[r['id']]['error'])}",
              f"- ответов с неснятыми замечаниями верификатора: {sum(1 for r in rows if ours[r['id']]['hard_issues'])}",
              f"- не хватает обязательных элементов (`must`): {sum(1 for c in checks.values() if c['missing'])}",
              f"- есть запрещённые утверждения (`must_not`): {sum(1 for c in checks.values() if c['violated'])}",
              f"- вторых проходов: {sum(1 for r in rows if ours[r['id']]['passes'] > 1)}; исправлений: "
              f"{sum(1 for r in rows if ours[r['id']]['repaired'])}",
              f"- слов в ответе, медиана: {sorted(c['words'] for c in checks.values())[n // 2]} (в эталонах: "
              f"{sorted(len(r['golden'].split()) for r in rows)[n // 2]}); секунд на вопрос, "
              f"медиана: {sorted(ours[r['id']]['seconds'] for r in rows)[n // 2]:.1f}", ""]
    if judged:
        ok = [r for r in rows if "score" in judged[r["id"]]]
        total = metrics([judged[r["id"]]["score"] for r in ok])
        lines += ["## Судья", "",
                  f"**Score {total['score']:.3f}** = 0,7 × MatchRate {total['match_rate']:.3f} + 0,3 × WinRate "
                  f"{total['win_rate']:.3f} · побед {total['wins']}, ничьих {total['ties']}, поражений {total['losses']} "
                  f"из {total['n']} (ошибок судьи: {n - len(ok)})", "",
                  "| Набор | n | MatchRate | WinRate | Score | П / Н / Пр |", "|---|---|---|---|---|---|"]
        for src, title in (("organisers", "эталоны организаторов"), ("team", "эталоны команды")):
            m = metrics([judged[r["id"]]["score"] for r in ok if r.get("source") == src])
            if m["n"]:
                lines.append(f"| {title} | {m['n']} | {m['match_rate']:.2f} | {m['win_rate']:.2f} | {m['score']:.2f} | "
                             f"{m['wins']} / {m['ties']} / {m['losses']} |")
        lines += ["", "| Категория | n | MatchRate | WinRate | Score | П / Н / Пр |", "|---|---|---|---|---|---|"]
        for cat in sorted({r["category"] for r in ok}):
            m = metrics([judged[r["id"]]["score"] for r in ok if r["category"] == cat])
            lines.append(f"| {cat} | {m['n']} | {m['match_rate']:.2f} | {m['win_rate']:.2f} | {m['score']:.2f} | "
                         f"{m['wins']} / {m['ties']} / {m['losses']} |")
        lines += ["", f"Выдуманный факт у нас: {sum(1 for r in ok if judged[r['id']]['fabricated'])}; у baseline: "
                      f"{sum(1 for r in ok if judged[r['id']]['baseline_fabricated'])}", ""]
    lines += ["## Что смотреть в первую очередь", ""]
    for r in rows:
        j = judged.get(r["id"], {}) if judged else {}
        c = checks[r["id"]]
        flagged = (j.get("score", 1.0) < 0.5) or c["missing"] or c["violated"] or ours[r["id"]]["hard_issues"] \
            or ours[r["id"]]["error"]
        if not flagged:
            continue
        lines += [f"### {r['id']} · {r['category']}" + (f" · балл {j['score']:.2f}" if "score" in j else ""), "",
                  f"**Вопрос:** {r['question']}", "", f"**Эталон:** {r['golden']}", "",
                  f"**Наш ответ ({ours[r['id']]['status']}):** {ours[r['id']]['answer']}", ""]
        if base:
            lines += [f"**Baseline:** {base[r['id']]}", ""]
        notes = []
        if c["missing"]:
            notes.append("нет: " + ", ".join(c["missing"]))
        if c["violated"]:
            notes.append("запрещено: " + ", ".join(c["violated"]))
        if ours[r["id"]]["hard_issues"]:
            notes.append("верификатор: " + "; ".join(ours[r["id"]]["hard_issues"]))
        if j.get("reasons"):
            notes.append("судья: " + " / ".join(j["reasons"]))
        if ours[r["id"]]["error"]:
            notes.append("ошибка: " + ours[r["id"]]["error"])
        lines += ["_" + " · ".join(notes) + "_", ""]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "answers.json").write_text(json.dumps(
        {"ours": ours, "baseline": base, "judge": judged}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"report: {out_dir / 'report.md'}")
    print("\n".join(lines[2:14]))
    if judged:
        print("\n".join(l for l in lines if l.startswith("**Score")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--devset", default=str(Path(__file__).resolve().parent / "devset.jsonl"))
    ap.add_argument("--only", default="", help="comma-separated categories or ids")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--baseline", choices=["sample", "golden"], default="golden",
                    help="golden: the reference answer plays the baseline (a strong baseline, as on the platform, "
                         "where every team scores below 0.5); sample: the organisers' starter submission")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--label", default=time.strftime("%Y%m%d-%H%M%S"))
    args = ap.parse_args()

    settings = load_settings()
    if not settings.has_api:
        sys.exit("Задайте LLM_BASE_URL и LLM_API_KEY (см. .env.example).")
    rows = load_devset(Path(args.devset), {x for x in args.only.split(",") if x}, args.limit)
    out_dir = Path(__file__).resolve().parent / "runs" / args.label
    ours = run_ours(rows, settings, args.workers)
    base, judged = {}, {}
    if not args.no_judge and not args.no_baseline:
        if args.baseline == "golden":
            base = {r["id"]: r["golden"] for r in rows}
        else:
            safe = re.sub(r"[^0-9A-Za-z]+", "_", settings.llm_model)
            base = run_baseline(rows, Path(__file__).resolve().parent / "runs" / f"baseline_{safe}.json")
        judged = judge_all(rows, ours, base, settings, args.workers)
    report(rows, ours, base, judged, out_dir, args.label)


if __name__ == "__main__":
    main()
