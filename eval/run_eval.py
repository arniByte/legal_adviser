"""Local evaluation that mirrors the organisers' LLM-as-judge protocol.

    python eval/run_eval.py --label v7                  # our answers + judge + report
    python eval/run_eval.py --no-judge                  # quick checks only (no judge calls)
    python eval/run_eval.py --answers v7 --repeats 3    # judge the saved answers of run "v7" again, three times
    python eval/run_eval.py --compare v7 v8            # paired comparison of saved runs, no API required
    python eval/run_eval.py --limit 10 --only temporal,twins

Needs the same environment as the solution (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL ...).
The judge can use another model: JUDGE_MODEL, JUDGE_BASE_URL, JUDGE_API_KEY.

What is reproduced from the technical regulation:
  * our answer is compared with a baseline answer, the reference answer is the yardstick;
  * two rounds with swapped positions (A = baseline, B = ours; then A = ours, B = baseline);
  * scale 1 / 0.75 / 0.5 / 0.25 / 0 relative to the baseline, question score = mean of two rounds;
  * an invented fact zeroes the question;
  * MatchRate = mean score, WinRate = W / (W + L), Score = 0.7 * MatchRate + 0.3 * WinRate.
The organisers' judge prompt, baseline and questions are not public, so absolute numbers differ from
the leaderboard. The starter submission and the reference answer are two proxy baselines, not proven
bounds on the unknown baseline. Their scores are local diagnostics, not calibrated leaderboard estimates.

Both the model and the judge vary between runs, so one run proves little: compare versions on several
judge repeats (--repeats) and, for the final decision, on more than one generation run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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


_REF = re.compile(r"(?:п\.|пп\.|ч\.|ст\.|подп\.)\s*(\d+(?:\.\d+)*)")


def _json_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_metadata(settings, rows: list, devset: Path, *, workers: int, repeats: int,
                   baseline: str, answer_source: str = "") -> dict:
    """An explicit allowlist keeps API addresses, keys and local paths out of provenance."""
    git = {"sha": None, "dirty": None}
    try:
        git["sha"] = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        git["dirty"] = bool(subprocess.check_output(
            ["git", "-C", str(ROOT), "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    safe_fields = ("llm_model", "emb_model", "reranker_model", "temperature", "max_tokens", "max_workers",
                   "time_budget", "request_timeout", "use_embeddings", "use_reranker", "second_pass",
                   "repair", "edit")
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git": git,
        "inputs": {
            "kb_sha256": hashlib.sha256((ROOT / "data" / "knowledge-base.zip").read_bytes()).hexdigest(),
            "devset_sha256": hashlib.sha256(devset.read_bytes()).hexdigest(),
            "selected_sha256": _json_hash(rows),
            "question_sha256": {r["id"]: _json_hash(r) for r in rows},
        },
        "settings": {name: getattr(settings, name) for name in safe_fields},
        "evaluation": {
            "workers": workers, "repeats": repeats, "baseline": baseline,
            "judge_model": os.getenv("JUDGE_MODEL", "").strip() or settings.llm_model,
            "judge_max_tokens": 1200, "judge_temperature": 0.0,
            "question_deadline_seconds": 240, "answer_source_run": answer_source or None,
        },
    }


def answer_facts(text: str, question: str) -> set:
    """Numbers, dates and clause references of a text, minus those already in the question."""
    from la_text import support_values
    facts = support_values(text) | {"ref:" + m for m in _REF.findall(text)}
    return facts - (support_values(question) | {"ref:" + m for m in _REF.findall(question)})


def fact_scores(rows: list, ours: dict) -> dict:
    """Deterministic agreement with the references: share of answer facts found in the reference (precision)
    and of reference facts found in the answer (recall). Unlike the judge, it does not vary between runs."""
    p = r = n = extra = missed = 0
    for row in rows:
        q = parse_dialog(row["question"]).full_text
        g, a = answer_facts(row["golden"], q), answer_facts(ours[row["id"]]["answer"], q)
        if not g and not a:
            continue
        hit = len(g & a)
        p += hit / len(a) if a else 1.0
        r += hit / len(g) if g else 1.0
        extra += len(a - g)
        missed += len(g - a)
        n += 1
    p, r = (p / n, r / n) if n else (0.0, 0.0)
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "extra": extra, "missed": missed}


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


def run_ours(rows: list, settings, workers: int, metadata: dict | None = None) -> dict:
    started = time.monotonic()
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
                "analysis": r.analysis, "edited": r.edited}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one, range(len(rows))))
    print(f"ours: {len(out)} answers; dense search {'on' if dense else 'off'}; reranker "
          f"{llm.reranker_model if llm.reranker_ok else 'off'}; llm calls {llm.stats['chat_calls']}; tokens in/out "
          f"{llm.stats['prompt_tokens']}/{llm.stats['completion_tokens']}", flush=True)
    if metadata is not None:
        metadata["generation"] = {
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "dense_search": bool(dense),
            "reranker_model": llm.reranker_model if llm.reranker_ok else None,
            "llm_stats": {k: v for k, v in llm.stats.items() if isinstance(v, (int, float))},
        }
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


def make_judge(settings) -> LLMClient:
    js = load_settings()
    js.llm_model = os.getenv("JUDGE_MODEL", "").strip() or settings.llm_model
    js.api_base = (os.getenv("JUDGE_BASE_URL", "").strip() or settings.api_base).rstrip("/")
    js.llm_key = os.getenv("JUDGE_API_KEY", "").strip() or settings.llm_key
    return LLMClient(js)


def judge_all(rows: list, ours: dict, base: dict, judge: LLMClient, workers: int) -> dict:
    """One pass of the judge over all questions: {id: verdict}."""

    def one(row: dict) -> dict:
        ans, bas = ours[row["id"]]["answer"], base[row["id"]]
        r1 = judge_pair(judge, row["question"], row["golden"], bas, ans)      # A = baseline, B = ours
        r2 = judge_pair(judge, row["question"], row["golden"], ans, bas)      # A = ours, B = baseline
        if "error" in r1 or "error" in r2:
            return {"error": r1.get("error") or r2.get("error")}
        s1, s2 = r1["score"], 1.0 - r2["score"]
        fabricated = r1["fab_b"] or r2["fab_a"]
        return {"score": 0.0 if fabricated else (s1 + s2) / 2, "raw": (s1 + s2) / 2, "rounds": [s1, s2],
                "fabricated": fabricated, "baseline_fabricated": r1["fab_a"] or r2["fab_b"],
                "reasons": [r1["reason"], r2["reason"]]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one, rows))
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


def pooled(repeats: list, rid: str) -> dict:
    """Verdicts of several judge repeats for one question, folded into one."""
    vs = [rep[rid] for rep in repeats if "score" in rep.get(rid, {})]
    if not vs:
        return {}
    return {"score": sum(v["score"] for v in vs) / len(vs), "fabricated": any(v["fabricated"] for v in vs),
            "reasons": vs[0]["reasons"]}


def successful_ids(repeat: dict, ids) -> set:
    return {rid for rid in ids if isinstance(repeat.get(rid, {}).get("score"), (int, float))
            and 0.0 <= repeat[rid]["score"] <= 1.0}


def common_successful_ids(repeats: list, ids) -> set:
    return set.intersection(*(successful_ids(rep, ids) for rep in repeats)) if repeats else set()


def _ids_text(ids) -> str:
    return ", ".join(f"`{rid}`" for rid in sorted(ids)) or "нет"


def judge_table(rows: list, verdicts: dict) -> list:
    """Individual coverage is visible; repeats are averaged only on the same question IDs."""
    ids = {r["id"] for r in rows}
    lines = ["| Baseline | Прогон судьи | Оценено / всего | Score | MatchRate | WinRate | П / Н / Пр | Обнулено | Score без обнуления | Исключённые ID |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, repeats in verdicts.items():
        for k, rep in enumerate(repeats, 1):
            good = successful_ids(rep, ids)
            ok = [rep[rid] for rid in sorted(good)]
            m, raw = metrics([v["score"] for v in ok]), metrics([v["raw"] for v in ok])
            z = sum(1 for v in ok if v["fabricated"])
            values = (f"{m['score']:.3f} | {m['match_rate']:.3f} | {m['win_rate']:.3f} | "
                      f"{m['wins']} / {m['ties']} / {m['losses']} | {z} | {raw['score']:.3f}") if ok \
                else "— | — | — | — | — | —"
            lines.append(f"| {name} | {k} | {len(good)} / {len(ids)} | {values} | {_ids_text(ids - good)} |")
        if len(repeats) > 1:
            common = common_successful_ids(repeats, ids)
            if common:
                scores = [metrics([rep[rid]["score"] for rid in sorted(common)])["score"] for rep in repeats]
                clean = [metrics([rep[rid]["raw"] for rid in sorted(common)])["score"] for rep in repeats]
                zeroed = [sum(bool(rep[rid]["fabricated"]) for rid in common) for rep in repeats]
                values = (f"**{sum(scores) / len(scores):.3f}** | | | | "
                          f"**{sum(zeroed) / len(zeroed):.1f}** | **{sum(clean) / len(clean):.3f}**")
            else:
                values = "— | | | | — | —"
            lines.append(f"| **{name}** | **среднее, общие ID** | **{len(common)} / {len(ids)}** | "
                         f"{values} | {_ids_text(ids - common)} |")
    return lines


def compare_artifacts(left: dict, right: dict) -> dict:
    """Pair corresponding judge repeats on identical, successful questions; keep the score formula."""
    left_ids, right_ids = set(left.get("ours", {})), set(right.get("ours", {}))
    candidate_ids = left_ids & right_ids
    left_meta, right_meta = left.get("metadata", {}), right.get("metadata", {})
    left_inputs, right_inputs = left_meta.get("inputs", {}), right_meta.get("inputs", {})
    left_hashes, right_hashes = left_inputs.get("question_sha256", {}), right_inputs.get("question_sha256", {})
    changed = {rid for rid in candidate_ids if rid in left_hashes and rid in right_hashes
               and left_hashes[rid] != right_hashes[rid]}
    warnings = []
    if any(rid not in left_hashes or rid not in right_hashes for rid in candidate_ids):
        warnings.append("Для части ID нет хеша вопроса и эталона: идентичность старых артефактов не подтверждена.")
    if not left_inputs.get("kb_sha256") or not right_inputs.get("kb_sha256") \
            or left_inputs["kb_sha256"] != right_inputs["kb_sha256"]:
        warnings.append("Хеши базы знаний различаются или отсутствуют: изменение базы смешано с изменением решения.")
    if not left_meta or not right_meta:
        warnings.append("Есть артефакты без metadata; версия кода и параметры старого прогона неизвестны.")
    baselines = {}
    left_judge, right_judge = left.get("judge", {}), right.get("judge", {})
    for name in sorted(set(left_judge) & set(right_judge)):
        reps_a, reps_b = left_judge[name], right_judge[name]
        count = min(len(reps_a), len(reps_b))
        if not count:
            continue
        bas_a, bas_b = left.get("baselines", {}).get(name, {}), right.get("baselines", {}).get(name, {})
        changed_baseline = {rid for rid in candidate_ids if rid in bas_a and rid in bas_b
                            and bas_a[rid] != bas_b[rid]}
        common = common_successful_ids(reps_a[:count] + reps_b[:count], candidate_ids - changed - changed_baseline)
        paired = []
        for i in range(count):
            ma = metrics([reps_a[i][rid]["score"] for rid in sorted(common)])
            mb = metrics([reps_b[i][rid]["score"] for rid in sorted(common)])
            paired.append({"repeat": i + 1, "left": ma, "right": mb,
                           "delta": round(mb["score"] - ma["score"], 4) if common else None,
                           "left_evaluated": len(successful_ids(reps_a[i], left_ids)),
                           "right_evaluated": len(successful_ids(reps_b[i], right_ids))})
        per_id = {}
        for rid in sorted(common):
            a = sum(rep[rid]["score"] for rep in reps_a[:count]) / count
            b = sum(rep[rid]["score"] for rep in reps_b[:count]) / count
            per_id[rid] = {"left": a, "right": b, "delta": b - a}
        baselines[name] = {
            "paired_repeats": count, "unpaired_left_repeats": len(reps_a) - count,
            "unpaired_right_repeats": len(reps_b) - count,
            "common_ids": sorted(common), "excluded_ids": sorted((left_ids | right_ids) - common),
            "changed_baseline_ids": sorted(changed_baseline), "repeats": paired, "per_id": per_id,
            "mean_delta": round(sum(p["delta"] for p in paired) / count, 4) if common else None,
            "improved_ids": [rid for rid, v in per_id.items() if v["delta"] > 0],
            "worsened_ids": [rid for rid, v in per_id.items() if v["delta"] < 0],
        }
    return {"left_total": len(left_ids), "right_total": len(right_ids), "changed_question_ids": sorted(changed),
            "warnings": warnings, "baselines": baselines}


def comparison_lines(comparison: dict, left_label: str, right_label: str) -> list:
    lines = [f"# Парное сравнение: {left_label} → {right_label}", "",
             "Разность — второй прогон минус первый. Это описательное сравнение, без вывода о статистической значимости.",
             "Для каждого baseline используются общие ID, успешно оценённые во всех парных повторах обоих прогонов.", ""]
    lines += [f"- {warning}" for warning in comparison["warnings"]]
    if comparison["changed_question_ids"]:
        lines.append("- Изменены вопрос или эталон, исключены ID: " + _ids_text(comparison["changed_question_ids"]))
    if not comparison["baselines"]:
        return lines + ["", "Нет общих прогонов судьи для сравнения."]
    for name, data in comparison["baselines"].items():
        lines += ["", f"## {name}", "",
                  f"Общие оценённые вопросы: {len(data['common_ids'])}; всего ответов — "
                  f"{left_label}: {comparison['left_total']}, {right_label}: {comparison['right_total']}.",
                  "Исключённые ID: " + _ids_text(data["excluded_ids"]),
                  "Изменился ответ baseline, исключены ID: " + _ids_text(data["changed_baseline_ids"]),
                  f"Повторов без пары: {data['unpaired_left_repeats']} / {data['unpaired_right_repeats']}.", "",
                  "| Повтор | Оценено A / всего | Оценено B / всего | Общих ID | Score A | Score B | Δ |",
                  "|---|---|---|---|---|---|---|"]
        for pair in data["repeats"]:
            values = (f"{pair['left']['score']:.4f} | {pair['right']['score']:.4f} | {pair['delta']:+.4f}") \
                if pair["delta"] is not None else "— | — | —"
            lines.append(f"| {pair['repeat']} | {pair['left_evaluated']} / {comparison['left_total']} | "
                         f"{pair['right_evaluated']} / {comparison['right_total']} | {len(data['common_ids'])} | {values} |")
        if data["mean_delta"] is not None:
            lines += ["", f"Средняя парная разность Score: {data['mean_delta']:+.4f}."]
        lines += ["", "Улучшились ID: " + _ids_text(data["improved_ids"]),
                  "Ухудшились ID: " + _ids_text(data["worsened_ids"]), "",
                  "| ID | Средний балл вопроса A | Средний балл вопроса B | Δ |", "|---|---|---|---|"]
        for rid, values in data["per_id"].items():
            if values["delta"]:
                lines.append(f"| {rid} | {values['left']:.3f} | {values['right']:.3f} | {values['delta']:+.3f} |")
    return lines


def report(rows: list, ours: dict, bases: dict, verdicts: dict, out_dir: Path, label: str,
           metadata: dict | None = None) -> None:
    """bases: {baseline name: {id: answer}}; verdicts: {baseline name: [one {id: verdict} per judge repeat]}."""
    lines = [f"# Локальная оценка: {label}", ""]
    if metadata:
        lines += ["## Воспроизводимость", "",
                  f"- git: `{metadata['git']['sha']}`; незакоммиченные изменения: {metadata['git']['dirty']}",
                  f"- SHA256 базы: `{metadata['inputs']['kb_sha256']}`",
                  f"- SHA256 devset: `{metadata['inputs']['devset_sha256']}`",
                  f"- SHA256 выбранных вопросов: `{metadata['inputs']['selected_sha256']}`",
                  "- параметры и наблюдения выполнения: `metadata.json` (без API-адресов и ключей)", ""]
    checks = {r["id"]: quick_checks(r, ours[r["id"]]["answer"]) for r in rows}
    fs = fact_scores(rows, ours)
    n = len(rows)
    lines += ["## Проверки без судьи", "",
              f"- ответов: {n}; с ошибкой модели: {sum(1 for r in rows if ours[r['id']]['error'])}",
              f"- ответов с неснятыми замечаниями верификатора: {sum(1 for r in rows if ours[r['id']]['hard_issues'])}",
              f"- не хватает обязательных элементов (`must`): {sum(1 for c in checks.values() if c['missing'])}",
              f"- есть запрещённые утверждения (`must_not`): {sum(1 for c in checks.values() if c['violated'])}",
              f"- вторых проходов: {sum(1 for r in rows if ours[r['id']]['passes'] > 1)}; исправлений: "
              f"{sum(1 for r in rows if ours[r['id']]['repaired'])}; сокращено редактором: "
              f"{sum(1 for r in rows if ours[r['id']].get('edited'))}",
              f"- факты против эталона: точность {fs['precision']:.3f}, полнота {fs['recall']:.3f}, F1 {fs['f1']:.3f}; "
              f"лишних фактов {fs['extra']}, недостающих {fs['missed']}",
              f"- слов в ответе, медиана: {sorted(c['words'] for c in checks.values())[n // 2]} (в эталонах: "
              f"{sorted(len(r['golden'].split()) for r in rows)[n // 2]}); секунд на вопрос, "
              f"медиана: {sorted(ours[r['id']]['seconds'] for r in rows)[n // 2]:.1f}", ""]
    main_name = next(iter(verdicts), "")
    main_runs = verdicts.get(main_name, [])
    common_main = common_successful_ids(main_runs, {r["id"] for r in rows})
    if verdicts:
        lines += ["## Судья", ""] + judge_table(rows, verdicts) + [
            "", "«Обнулено» — ответы, в которых локальный судья отметил выдуманный факт: такой вопрос получает 0. "
                "Судья видит эталон, но не базу знаний; это эвристическая оценка, которая может ошибаться, "
                "в том числе на верных дополнениях к эталону.", "",
            f"Разбивка ниже — против baseline «{main_name}»: только общие ID, оценённые во всех повторах "
            f"({len(common_main)} / {n} вопросов). Оценок на вопрос: {len(main_runs)}.",
            "Исключённые ID: " + _ids_text({r["id"] for r in rows} - common_main), "",
            "| Набор | Оценок / всего | MatchRate | WinRate | Score | П / Н / Пр |", "|---|---|---|---|---|---|"]

        def group(select) -> dict:
            return metrics([rep[r["id"]]["score"] for rep in main_runs for r in rows
                            if select(r) and r["id"] in common_main])

        for src, title in (("organisers", "эталоны организаторов"), ("team", "эталоны команды")):
            m = group(lambda r: r.get("source") == src)
            expected = sum(r.get("source") == src for r in rows) * len(main_runs)
            if expected:
                lines.append(f"| {title} | {m['n']} / {expected} | {m['match_rate']:.2f} | {m['win_rate']:.2f} | {m['score']:.2f} | "
                             f"{m['wins']} / {m['ties']} / {m['losses']} |")
        lines += ["", "| Категория | Оценок / всего | MatchRate | WinRate | Score | П / Н / Пр |", "|---|---|---|---|---|---|"]
        for cat in sorted({r["category"] for r in rows}):
            m = group(lambda r: r["category"] == cat)
            expected = sum(r["category"] == cat for r in rows) * len(main_runs)
            lines.append(f"| {cat} | {m['n']} / {expected} | {m['match_rate']:.2f} | {m['win_rate']:.2f} | {m['score']:.2f} | "
                         f"{m['wins']} / {m['ties']} / {m['losses']} |")
        lines.append("")
    else:
        lines += ["## Судья", "", f"Оценено 0 / {n}: судья не запускался.",
                  "Не оценены ID: " + _ids_text(r["id"] for r in rows), ""]
    lines += ["## Что смотреть в первую очередь", ""]
    for r in rows:
        j = pooled(main_runs, r["id"])
        c = checks[r["id"]]
        judge_errors = [rep.get(r["id"], {}).get("error", "нет оценки") for rep in main_runs
                        if r["id"] not in successful_ids(rep, [r["id"]])]
        flagged = (j.get("score", 1.0) < 0.5) or c["missing"] or c["violated"] or ours[r["id"]]["hard_issues"] \
            or ours[r["id"]]["error"] or judge_errors
        if not flagged:
            continue
        lines += [f"### {r['id']} · {r['category']}" + (f" · балл {j['score']:.2f}" if "score" in j else ""), "",
                  f"**Вопрос:** {r['question']}", "", f"**Эталон:** {r['golden']}", "",
                  f"**Наш ответ ({ours[r['id']]['status']}):** {ours[r['id']]['answer']}", ""]
        if main_name and main_name != "golden":
            lines += [f"**Baseline:** {bases[main_name][r['id']]}", ""]
        notes = []
        if judge_errors:
            notes.append("не оценено судьёй: " + "; ".join(judge_errors))
        if c["missing"]:
            notes.append("нет: " + ", ".join(c["missing"]))
        if c["violated"]:
            notes.append("запрещено: " + ", ".join(c["violated"]))
        if ours[r["id"]]["hard_issues"]:
            notes.append("верификатор: " + "; ".join(ours[r["id"]]["hard_issues"]))
        if j.get("fabricated"):
            notes.append("судья увидел выдуманный факт")
        if j.get("reasons"):
            notes.append("судья: " + " / ".join(j["reasons"]))
        if ours[r["id"]]["error"]:
            notes.append("ошибка: " + ours[r["id"]]["error"])
        lines += ["_" + " · ".join(notes) + "_", ""]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "answers.json").write_text(json.dumps(
        {"ours": ours, "baselines": {k: v for k, v in bases.items() if k != "golden"}, "judge": verdicts,
         "metadata": metadata or {}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "metadata.json").write_text(json.dumps(metadata or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out_dir / 'report.md'}")
    print("\n".join(lines[2:14]))
    if verdicts:
        print("\n".join(judge_table(rows, verdicts)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--devset", default=str(Path(__file__).resolve().parent / "devset.jsonl"))
    ap.add_argument("--only", default="", help="comma-separated categories or ids")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--baseline", choices=["both", "sample", "golden"], default="both",
                    help="sample: the organisers' starter submission (a weak baseline); golden: the reference answer "
                         "plays the baseline (the strongest possible one); both: judge against each")
    ap.add_argument("--repeats", type=int, default=1, help="judge passes per baseline; the judge varies between runs")
    ap.add_argument("--answers", default="", help="label of an earlier run: judge its saved answers, do not generate")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--label", default="")
    ap.add_argument("--overwrite", action="store_true", help="explicitly allow replacing an existing run directory")
    ap.add_argument("--compare", nargs=2, metavar=("LEFT", "RIGHT"),
                    help="compare saved runs on common successfully judged IDs; does not call an API")
    args = ap.parse_args()

    runs = Path(__file__).resolve().parent / "runs"
    labels = [("--label", args.label), ("--answers", args.answers)]
    labels += [("--compare", label) for label in (args.compare or [])]
    for flag, label in labels:
        if (flag == "--compare" and not label) or \
                (label and (Path(label).name != label or label in (".", "..") or "\\" in label)):
            ap.error(f"{flag} принимает метку прогона, а не путь")
    if args.compare:
        left, right = [json.loads((runs / label / "answers.json").read_text(encoding="utf-8"))
                       for label in args.compare]
        comparison = compare_artifacts(left, right)
        text = "\n".join(comparison_lines(comparison, *args.compare)) + "\n"
        path = runs / f"compare_{args.compare[0]}_vs_{args.compare[1]}.md"
        path.write_text(text, encoding="utf-8")
        print(text)
        print(f"report: {path}")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    label = args.label or (f"{args.answers}-rejudge-{timestamp}" if args.answers else timestamp)
    out_dir = runs / label
    if out_dir.exists() and not args.overwrite:
        ap.error(f"прогон {label!r} уже существует; выберите другую --label или явно передайте --overwrite")
    if out_dir.is_symlink() or (out_dir.exists() and not out_dir.is_dir()):
        ap.error("каталог прогона не должен быть файлом или символической ссылкой")
    settings = load_settings()
    if not settings.has_api:
        sys.exit("Задайте LLM_BASE_URL и LLM_API_KEY (см. .env.example).")
    rows = load_devset(Path(args.devset), {x for x in args.only.split(",") if x}, args.limit)
    saved = None
    if args.answers:
        saved = json.loads((runs / args.answers / "answers.json").read_text(encoding="utf-8"))
        ours = saved["ours"]
        rows = [r for r in rows if r["id"] in ours]
    if not rows:
        ap.error("нет вопросов для оценки")
    metadata = build_metadata(settings, rows, Path(args.devset), workers=args.workers,
                              repeats=args.repeats, baseline=args.baseline, answer_source=args.answers)
    try:
        out_dir.mkdir(parents=True, exist_ok=args.overwrite)
    except FileExistsError:
        ap.error(f"прогон {label!r} уже существует; выберите другую --label или явно передайте --overwrite")
    if saved is not None:
        original = saved.get("metadata", {})
        metadata["original_answer_metadata"] = original.get("original_answer_metadata", original)
    else:
        ours = run_ours(rows, settings, args.workers, metadata)

    bases, verdicts = {}, {}
    if not args.no_judge:
        if args.baseline in ("both", "sample"):
            safe = re.sub(r"[^0-9A-Za-z]+", "_", settings.llm_model)
            bases["sample"] = run_baseline(rows, runs / f"baseline_{safe}.json")
        if args.baseline in ("both", "golden"):
            bases["golden"] = {r["id"]: r["golden"] for r in rows}
        judge = make_judge(settings)
        for name, base in bases.items():
            verdicts[name] = [judge_all(rows, ours, base, judge, args.workers) for _ in range(args.repeats)]
        print(f"judge ({judge.s.llm_model}): {judge.stats['chat_calls']} calls", flush=True)
    report(rows, ours, bases, verdicts, out_dir, label, metadata)


if __name__ == "__main__":
    main()
