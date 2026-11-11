"""Thin client for the OpenAI-compatible proxy: chat completions, embeddings, rerank.

Bounded retries, deadline awareness and counters. Keys are never logged.
"""

from __future__ import annotations

import random
import sys
import threading
import time

import httpx

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
RERANKER_CANDIDATES = ("qwen/qwen3-reranker-8b", "cohere/rerank-5-fast", "Qwen/Qwen3-Reranker-8B")


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings):
        self.s = settings
        self.http = httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout, connect=15.0),
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )
        self.lock = threading.Lock()
        self.stats = {"chat_calls": 0, "chat_errors": 0, "retries": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "cached_tokens": 0, "chat_seconds": 0.0, "embed_calls": 0, "rerank_calls": 0,
                      "rate_limited": 0, "length_retries": 0, "truncated": 0}
        self.reranker_model = settings.reranker_model
        self.reranker_ok = None       # None = unknown, True / False after the first attempt
        self.embeddings_ok = None
        self.rerank_failures = 0

    def close(self) -> None:
        try:
            self.http.close()
        except Exception:
            pass

    def _bump(self, key: str, value=1) -> None:
        with self.lock:
            self.stats[key] = self.stats.get(key, 0) + value

    # ------------------------------------------------------------------ #
    def _post(self, path: str, key: str, payload: dict, timeout: float, deadline=None, retries: int = 2) -> dict:
        if not self.s.api_base or not key:
            raise LLMError("API endpoint or key is not configured")
        last = "unknown error"
        for attempt in range(retries + 1):
            budget = timeout
            if deadline is not None:
                budget = min(timeout, deadline - time.monotonic())
                if budget < 2.0:
                    raise LLMError("deadline reached before request")
            try:
                r = self.http.post(self.s.api_base + path, headers={"Authorization": "Bearer " + key},
                                   json=payload, timeout=httpx.Timeout(budget, connect=min(15.0, budget)))
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    self._bump("rate_limited")
                detail = ""
                try:
                    detail = str(r.json().get("error", ""))[:200]
                except Exception:
                    detail = r.text[:200]
                # some gateways answer 402 while earlier requests are still being billed: this passes on retry
                in_flight = r.status_code == 402 and "in-flight" in detail
                if r.status_code not in RETRY_STATUS and not in_flight:
                    raise LLMError(f"{path}: HTTP {r.status_code} {detail}")
                last = f"HTTP {r.status_code} {detail[:80]}"
                wait = (4.0 if in_flight else 1.5) * (2 ** attempt) + random.random()
                try:
                    wait = max(wait, min(10.0, float(r.headers.get("retry-after", "0"))))
                except ValueError:
                    pass
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = type(exc).__name__
                wait = 1.0 * (2 ** attempt) + random.random()
            except ValueError as exc:  # invalid JSON body
                last = "invalid JSON: " + str(exc)[:80]
                wait = 1.0
            if attempt >= retries:
                break
            if deadline is not None and time.monotonic() + wait > deadline - 2.0:
                break
            self._bump("retries")
            time.sleep(wait)
        raise LLMError(f"{path}: {last}")

    # ------------------------------------------------------------------ #
    def chat(self, messages: list, max_tokens=None, temperature=None, timeout=None, deadline=None) -> str:
        max_tokens = int(max_tokens or self.s.max_tokens)
        payload = {"model": self.s.llm_model, "messages": messages,
                   "temperature": self.s.temperature if temperature is None else temperature,
                   "max_tokens": max_tokens}
        t0 = time.monotonic()
        self._bump("chat_calls")
        partial = ""
        try:
            for round_no in range(2):
                data = self._post("/chat/completions", self.s.llm_key, payload,
                                  timeout or self.s.request_timeout, deadline)
                usage = data.get("usage") or {}
                self._bump("prompt_tokens", int(usage.get("prompt_tokens") or 0))
                self._bump("completion_tokens", int(usage.get("completion_tokens") or 0))
                details = usage.get("prompt_tokens_details") or {}
                self._bump("cached_tokens", int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0))
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                content = message.get("content")
                if isinstance(content, list):          # content parts
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                text = content.strip() if isinstance(content, str) else ""
                cut = choice.get("finish_reason") == "length"
                if text and not cut:
                    return text
                # a reasoning model can spend the budget on thinking and stop mid-answer: retry once with more room
                if round_no == 0 and (cut or message.get("reasoning_content") or message.get("reasoning")):
                    self._bump("length_retries")
                    payload["max_tokens"] = min(8192, max(max_tokens * 2, 4096))
                    partial = text
                    continue
                if text:
                    self._bump("truncated")
                    return text
                break
            if partial:
                self._bump("truncated")
                return partial
            raise LLMError("empty completion")
        except LLMError:
            self._bump("chat_errors")
            raise
        finally:
            self._bump("chat_seconds", time.monotonic() - t0)

    # ------------------------------------------------------------------ #
    def embed(self, texts: list, timeout: float = 60.0, deadline=None) -> list:
        if not texts:
            return []
        data = self._post("/embeddings", self.s.embed_key,
                          {"model": self.s.emb_model, "input": texts, "encoding_format": "float"},
                          timeout, deadline)
        self._bump("embed_calls")
        items = data.get("data") or []
        vectors = [None] * len(texts)
        for position, item in enumerate(items):
            index = int(item.get("index", position))
            if 0 <= index < len(texts):
                vectors[index] = item.get("embedding")
        if any(v is None for v in vectors):
            raise LLMError("embeddings: incomplete response")
        return vectors

    # ------------------------------------------------------------------ #
    def rerank(self, query: str, documents: list, top_n: int, timeout: float = 30.0, deadline=None) -> list:
        """Return [(index, score)] sorted by relevance; raises LLMError when unavailable."""
        if self.reranker_ok is False or not documents:
            raise LLMError("reranker disabled")
        models = [self.reranker_model] if self.reranker_model else list(RERANKER_CANDIDATES)
        last = None
        for model in models:
            try:
                data = self._post("/rerank", self.s.rerank_key,
                                  {"model": model, "query": query, "documents": documents,
                                   "top_n": min(top_n, len(documents))}, timeout, deadline, retries=1)
                items = data.get("results", data.get("data", [])) or []
                out = []
                for item in items:
                    idx = int(item.get("index", -1))
                    if 0 <= idx < len(documents):
                        score = item.get("relevance_score", item.get("score", 0.0))
                        out.append((idx, float(score or 0.0)))
                if not out:
                    raise LLMError("rerank: empty response")
                self.reranker_model, self.reranker_ok = model, True
                self.rerank_failures = 0
                self._bump("rerank_calls")
                return out
            except LLMError as exc:
                last = exc
        with self.lock:
            self.rerank_failures += 1
            if self.reranker_ok is None or self.rerank_failures >= 3:
                if self.reranker_ok is not False:
                    print(f"[llm] reranker switched off: {last}", file=sys.stderr)
                self.reranker_ok = False
        raise LLMError(str(last))
