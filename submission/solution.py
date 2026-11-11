"""Entry point of the automatic submission: questions.csv + knowledge base -> submission.csv.

The run is built to always leave a valid submission.csv behind:
  * a complete file with safe placeholder answers is written before the first API call;
  * answers are flushed as they arrive;
  * a time budget degrades the pipeline (full -> lite -> placeholder) instead of timing out;
  * a watchdog writes the file and exits if something hangs.
"""

from __future__ import annotations

import csv
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from la_config import load_settings  # noqa: E402
from la_kb import find_kb_source, load_kb  # noqa: E402
from la_llm import LLMClient  # noqa: E402
from la_pipeline import FALLBACK_ANSWER, Assistant, parse_dialog, retrieval_query  # noqa: E402
from la_retrieval import Retriever  # noqa: E402
from la_text import squash  # noqa: E402

OUTPUT = Path("submission.csv")
MAX_ANSWER_CHARS = 9500
T_START = time.monotonic()


def log(message: str) -> None:
    print(f"[{time.monotonic() - T_START:7.1f}s] {message}", file=sys.stderr, flush=True)


def find_questions(data_dir: Path) -> Path:
    for root in (data_dir, HERE / data_dir, Path.cwd(), HERE):
        for name in ("questions.csv", "demo_questions.csv"):
            p = root / name
            if p.is_file():
                return p
    raise FileNotFoundError("questions.csv not found")


def load_questions(path: Path) -> list:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = [r for r in reader if r]
    if not rows:
        raise ValueError("questions.csv is empty")
    header = [h.strip().lower() for h in rows[0]]
    if "id" in header and "question" in header:
        i_id, i_q = header.index("id"), header.index("question")
        body = rows[1:]
    else:                                   # no header: assume id, question
        i_id, i_q, body = 0, 1, rows
    out, seen = [], set()
    for r in body:
        if len(r) <= max(i_id, i_q):
            continue
        qid = str(r[i_id]).strip()
        if not qid or qid in seen:
            continue
        seen.add(qid)
        out.append((qid, str(r[i_q]).strip()))
    if not out:
        raise ValueError("no questions with ids")
    return out


def write_submission(questions: list, answers: dict) -> None:
    tmp = OUTPUT.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writerow(["id", "answer"])
        for qid, _ in questions:
            answer = squash(answers.get(qid) or FALLBACK_ANSWER)[:MAX_ANSWER_CHARS] or FALLBACK_ANSWER
            writer.writerow([qid, answer])
    os.replace(tmp, OUTPUT)


def diagnostics(s, questions: list) -> None:
    """Facts about the environment for the logs. Names and aggregates only: no keys, no question texts."""
    secret_names = ("LLM_BASE_URL", "OPENAI_BASE_URL", "BASE_URL", "LLM_API_KEY", "OPENAI_API_KEY",
                    "EMBEDDER_API_KEY", "RERANKER_API_KEY")
    plain_names = ("LLM_MODEL", "GEN_MODEL", "OPENAI_MODEL", "EMB_MODEL", "RERANKER_MODEL", "TOP_K", "RERANKER_TOP_N",
                   "CHUNKER", "TEMPERATURE", "MAX_TOKENS", "MAX_WORKERS", "DATA_DIR")
    log("env set: " + ", ".join(n for n in secret_names if os.getenv(n)) + " | " +
        ", ".join(f"{n}={os.getenv(n)}" for n in plain_names if os.getenv(n)))
    try:
        data_files = [f"{p.name}({p.stat().st_size})" for p in sorted(s.data_dir.iterdir())][:20]
        root_files = [p.name for p in sorted(HERE.iterdir()) if p.is_file()][:40]
        log(f"data dir: {data_files}; solution dir: {root_files}")
    except OSError as exc:
        log(f"data dir listing failed: {type(exc).__name__}")
    lengths = sorted(len(q) for _, q in questions)
    dialogs = sum(1 for _, q in questions if len(parse_dialog(q).history) > 0)
    log(f"questions: {len(questions)}; with dialogue history: {dialogs}; length median/max: "
        f"{lengths[len(lengths) // 2]}/{lengths[-1]}; workers: {s.max_workers}; budget: {int(s.time_budget)}s; "
        f"python {sys.version.split()[0]}; cpu {os.cpu_count()}")


def main() -> int:
    s = load_settings()
    questions = load_questions(find_questions(s.data_dir))
    answers: dict = {}
    lock = threading.Lock()
    write_submission(questions, answers)                       # a valid file exists from the very first second
    diagnostics(s, questions)

    deadline = T_START + s.time_budget
    hard_stop = s.time_budget + 75

    def watchdog() -> None:
        log("watchdog: time is up, writing what is ready")
        with lock:
            write_submission(questions, answers)
        os._exit(0)

    timer = threading.Timer(max(5.0, hard_stop - (time.monotonic() - T_START)), watchdog)
    timer.daemon = True
    timer.start()

    kb = load_kb(find_kb_source(s.data_dir, (HERE, HERE / s.data_dir)))
    removed = sum(len(d.injections) for d in kb.docs)
    log(f"knowledge base: {len(kb.docs)} documents, {len(kb.chunks)} fragments, {sum(d.size for d in kb.docs)} chars, "
        f"fingerprint {kb.fingerprint}, embedded instructions removed: {removed}")

    llm = LLMClient(s)
    retriever = Retriever(kb, llm, s)
    if not s.has_api:
        log("WARNING: API endpoint or key is missing in the environment")
    dense = retriever.build_embeddings(deadline=T_START + min(150.0, s.time_budget * 0.2))
    queries = [retrieval_query(parse_dialog(q)) for _, q in questions]
    qvecs = retriever.embed_queries(queries, deadline=T_START + min(240.0, s.time_budget * 0.3)) if dense \
        else [None] * len(questions)
    log(f"search: lexical{' + dense' if dense else ''}; model {s.llm_model}; embeddings {s.emb_model}")

    assistant = Assistant(kb, retriever, llm, s)
    state = {"started": 0, "done": 0, "modes": {}, "status": {}, "passes": 0, "repaired": 0, "issues": 0,
             "seconds": [], "errors": 0, "words": [], "extra": 0, "trimmed": 0}
    total = len(questions)

    def work(position: int):
        qid, question = questions[position]
        with lock:
            pending = total - state["started"]
            state["started"] += 1
        remaining = deadline - time.monotonic()
        per_question = remaining * s.max_workers / max(pending, 1)
        mode = "full" if per_question >= 22 else ("lite" if per_question >= 7 else "skip")
        if mode == "skip" or remaining < 6:
            return position, None, "skip"
        q_deadline = min(deadline, time.monotonic() + max(25.0, min(150.0, per_question * 2.5)))
        try:
            result = assistant.answer(question, qvec=qvecs[position], deadline=q_deadline, mode=mode)
            return position, result, mode
        except Exception as exc:  # nothing may break the run
            log(f"question #{position + 1}: {type(exc).__name__}: {str(exc)[:120]}")
            return position, None, "error"

    last_flush = time.monotonic()
    with ThreadPoolExecutor(max_workers=s.max_workers) as pool:
        futures = [pool.submit(work, i) for i in range(total)]
        for future in as_completed(futures):
            position, result, mode = future.result()
            qid = questions[position][0]
            with lock:
                state["done"] += 1
                state["modes"][mode] = state["modes"].get(mode, 0) + 1
                if result is not None:
                    answers[qid] = result.answer
                    state["status"][result.status] = state["status"].get(result.status, 0) + 1
                    state["passes"] += result.passes
                    state["repaired"] += int(result.repaired)
                    state["issues"] += sum(1 for i in result.issues if i["hard"])
                    state["seconds"].append(result.timings.get("total", 0.0))
                    state["words"].append(len(result.answer.split()))
                    state["extra"] += int(bool(result.extra))
                    state["trimmed"] += int(result.trimmed)
                    state["errors"] += int(bool(result.error))
                    if state["done"] == 1:
                        log(f"first answer: {result.timings}; status {result.status}; passes {result.passes}; "
                            f"documents in context {len(result.context_docs)}")
                done = state["done"]
                if done == total or done % 10 == 0 or time.monotonic() - last_flush > 20:
                    write_submission(questions, answers)
                    last_flush = time.monotonic()
            if done % 10 == 0 or done == total:
                log(f"answered {done}/{total}")

    with lock:
        write_submission(questions, answers)
    timer.cancel()
    st = llm.stats
    secs = sorted(state["seconds"]) or [0.0]
    log(f"seconds per question p50/p90/max: {secs[len(secs) // 2]:.1f}/{secs[int(len(secs) * 0.9) - 1]:.1f}/"
        f"{secs[-1]:.1f}; second passes {state['passes'] - len(state['seconds'])}; answers with a model error "
        f"{state['errors']}; reranker model {llm.reranker_model or '-'}; dense search {'on' if dense else 'off'}")
    words = sorted(state["words"]) or [0]
    log(f"answer words p50/p90/max: {words[len(words) // 2]}/{words[int(len(words) * 0.9) - 1]}/{words[-1]}; "
        f"answers with side notes kept out: {state['extra']}; trimmed by the word cap: {state['trimmed']}")
    print(f"submission.csv: {len(answers)}/{total} answers", flush=True)
    log(f"done: {len(answers)}/{total} answers; modes {state['modes']}; statuses {state['status']}; "
        f"llm calls {st['chat_calls']} (errors {st['chat_errors']}, retries {st['retries']}, 429: {st['rate_limited']}); "
        f"tokens in/out {st['prompt_tokens']}/{st['completion_tokens']} (cached {st['cached_tokens']}); "
        f"repaired {state['repaired']}; unresolved check issues {state['issues']}; "
        f"reranker {'on' if llm.reranker_ok else 'off'}")
    llm.close()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:  # last resort: keep whatever file exists and report the reason
        log(f"FATAL {type(exc).__name__}: {str(exc)[:300]}")
        code = 1 if not OUTPUT.is_file() else 0
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
