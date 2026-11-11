"""Offline stand-in for the organisers' OpenAI-compatible proxy. For tests only.

Implements /chat/completions, /embeddings and /rerank with deterministic toy logic so that
the whole pipeline (retrieval -> generation -> verification -> CSV) can be exercised
without keys or network. It is NOT a language model: "answers" are extractive.

    python scripts/mock_llm.py --port 8765 [--latency 0.05] [--fail-rate 0.1]

Special behaviours (to exercise pipeline branches):
    question contains "пособи"      -> status NOT_IN_KB
    question contains "стихотвор"   -> status OFF_TOPIC
    question contains "выдумай"     -> answer with a number that is absent from the documents
    question contains "без тегов"   -> plain text without the output tags
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORD = re.compile(r"[0-9a-zа-яё]{3,}", re.I)
ARGS = None
STATE = {"calls": {"chat": 0, "embeddings": 0, "rerank": 0}, "repairs": 0}


REPLAY: dict = {}


def load_replay() -> None:
    """--replay: answer the few-shot example questions with their reference outputs (for UI demos and tests)."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "submission"))
    from la_kb import load_kb
    from la_pipeline import parse_dialog
    from la_prompts import EXAMPLES, _resolve
    kb = load_kb(root / "materials" / "knowledge-base.zip")
    for ex in EXAMPLES:
        lines = []
        for key, loc, quote in ex["sources"]:
            doc_id = _resolve(kb, key)
            if doc_id:
                lines.append(f"{doc_id} | {loc} | {quote}")
        out = (f"<analysis>{ex['analysis']}</analysis>\n<status>{ex['status']}</status>\n"
               f"<answer>{ex['answer']}</answer>\n<sources>\n" + "\n".join(lines) + "\n</sources>")
        REPLAY[" ".join(parse_dialog(ex["q"]).last_user.split())] = out


def toks(text: str) -> list:
    return [w.lower().replace("ё", "е")[:6] for w in WORD.findall(text)]


def embed(text: str, dim: int = 128) -> list:
    v = [0.0] * dim
    for t in toks(text):
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16)
        v[h % dim] += 1.0 if (h >> 9) % 2 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def chat(messages: list) -> str:
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    last = messages[-1]["content"] if messages else ""
    if "Автоматическая проверка нашла" in last:
        STATE["repairs"] += 1
        return ("<analysis>Исправление: неподтверждённое число убрано.</analysis>\n<status>NOT_IN_KB</status>\n"
                "<answer>В документах базы знаний нет сведений для ответа на этот вопрос.</answer>\n<sources>\n</sources>")
    m = re.search(r"(?:Вопрос пользователя:|Последняя реплика пользователя[^\n]*:)\n(.*?)\n\nОтветь на запрос", user, re.S)
    question = (m.group(1) if m else user[-400:]).strip()
    ql = question.lower()
    if "стихотвор" in ql:
        return ("<analysis>Не юридический запрос.</analysis>\n<status>OFF_TOPIC</status>\n<answer>Я отвечаю только на "
                "юридические вопросы по документам базы знаний.</answer>\n<sources>\n</sources>")
    if "пособи" in ql:
        return ("<analysis>В каталоге нет документов о пособиях.</analysis>\n<status>NOT_IN_KB</status>\n<answer>В базе "
                "знаний нет документов о пособиях, поэтому ответить по документам нельзя.</answer>\n<sources>\n</sources>")
    if "выдумай" in ql:
        return ("<analysis>Тест проверки.</analysis>\n<status>ANSWER</status>\n<answer>Срок оплаты составляет 777 "
                "календарных дней (п. 99.9).</answer>\n<sources>\nD1 | п. 99.9 | такого текста нет\n</sources>")
    replay = REPLAY.get(" ".join(question.split()))
    if replay:
        return replay
    kb_block = user.split("КОНЕЦ БАЗЫ ЗНАНИЙ")[0]
    q = set(toks(question))
    best, best_score, doc_id = "", 0.0, ""
    current = ""
    for line in kb_block.split("\n"):
        h = re.match(r"=== \[([DL]\d+)\]", line)
        if h:
            current = h.group(1)
            continue
        if not current or len(line) < 25 or line.startswith(("(", "[D", "[L")) and "]" in line[:6]:
            continue
        lt = toks(line)
        score = len(q & set(lt)) / math.sqrt(len(lt) + 4)
        if score > best_score:
            best, best_score, doc_id = line.strip(), score, current
    if not best:
        return ("<analysis>Ничего не найдено.</analysis>\n<status>NOT_IN_KB</status>\n<answer>В базе знаний нет сведений "
                "для ответа.</answer>\n<sources>\n</sources>")
    body = re.sub(r"^\[[^\]]+\]\s*", "", best)
    loc = re.match(r"^(\d+(?:\.\d+)+)\.?\s", body)
    locator = f"п. {loc.group(1)}" if loc else ""
    quote = " ".join(body.split()[:22])
    if "без тегов" in ql:
        return f"По документам: {body}"
    return (f"<analysis>Наиболее близкое положение найдено в {doc_id}.</analysis>\n<status>ANSWER</status>\n"
            f"<answer>По документу: {body}{' (' + locator + ')' if locator else ''}</answer>\n"
            f"<sources>\n{doc_id} | {locator} | {quote}\n</sources>")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):  # silence
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, STATE)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            return self._send(401, {"error": "no key"})
        if ARGS.latency:
            time.sleep(ARGS.latency * (0.5 + random.random()))
        if ARGS.fail_rate and random.random() < ARGS.fail_rate:
            return self._send(random.choice([429, 500, 503]), {"error": "injected failure"})
        path = self.path.rstrip("/")
        if path.endswith("/chat/completions"):
            STATE["calls"]["chat"] += 1
            content = chat(payload.get("messages", []))
            ptok = sum(len(m.get("content", "")) for m in payload.get("messages", [])) // 3
            return self._send(200, {"choices": [{"message": {"role": "assistant", "content": content},
                                                 "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": ptok, "completion_tokens": len(content) // 3}})
        if path.endswith("/embeddings"):
            STATE["calls"]["embeddings"] += 1
            inputs = payload.get("input", [])
            inputs = [inputs] if isinstance(inputs, str) else inputs
            return self._send(200, {"data": [{"index": i, "embedding": embed(t)} for i, t in enumerate(inputs)]})
        if path.endswith("/rerank"):
            STATE["calls"]["rerank"] += 1
            q = set(toks(payload.get("query", "")))
            docs = payload.get("documents", [])
            scored = sorted(((len(q & set(toks(d))) / math.sqrt(len(toks(d)) + 4), i) for i, d in enumerate(docs)),
                            reverse=True)
            return self._send(200, {"results": [{"index": i, "relevance_score": round(s, 4)}
                                                for s, i in scored[: payload.get("top_n", len(docs))]]})
        return self._send(404, {"error": "unknown path"})


def main() -> None:
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--latency", type=float, default=0.0)
    ap.add_argument("--fail-rate", type=float, default=0.0)
    ap.add_argument("--replay", action="store_true", help="reference outputs for the few-shot example questions")
    ARGS = ap.parse_args()
    if ARGS.replay:
        load_replay()
    server = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    print(f"mock LLM proxy on http://127.0.0.1:{ARGS.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
