"""Hybrid retrieval over knowledge-base chunks and budgeted context assembly.

Lexical BM25 (own implementation, light Russian stemming) is always available.
Dense vectors (bge-m3 through the proxy) and the reranker are optional layers:
if the API is unavailable the search silently stays lexical.

Context policy: small documents are given to the model in full, large ones as
the best fragments in document order with their structural context.
"""

from __future__ import annotations

import hashlib
import math
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from la_kb import render_company_doc, render_law_fragments, render_law_full
from la_text import tokens

try:  # numpy is optional: without it the search is lexical only
    import numpy as np
except Exception:  # pragma: no cover
    np = None

# Common legal abbreviations: the query is expanded with the full wording used in the documents.
ABBREVIATIONS = {
    "мрот": "минимальный размер оплаты труда",
    "ндс": "налог на добавленную стоимость налоговая ставка",
    "ндфл": "налог на доходы физических лиц налоговая ставка",
    "пдн": "персональные данные",
    "тк": "трудовой кодекс",
    "нк": "налоговый кодекс",
    "коап": "кодекс об административных правонарушениях административный штраф",
    "гк": "гражданский кодекс",
    "сэд": "система электронного документооборота",
    "упд": "универсальный передаточный документ",
    "допсоглашение": "дополнительное соглашение",
    "допник": "дополнительное соглашение",
    "главбух": "главный бухгалтер",
    "гендир": "генеральный директор",
    "гендиректор": "генеральный директор",
    "финдир": "финансовый директор",
    "финдиректор": "финансовый директор",
    "юротдел": "юридический отдел",
    "испытательный": "испытание при приеме на работу срок испытания",
    "переработка": "сверхурочная работа",
    "переработки": "сверхурочная работа",
    "овертайм": "сверхурочная работа",
    "аванс": "предоплата",
    "авансом": "предоплата",
    "пеня": "пени неустойка",
    "штраф": "штраф неустойка пени",
}


def expand_query(query: str) -> str:
    from la_text import words
    extra = [ABBREVIATIONS[w] for w in set(words(query)) if w in ABBREVIATIONS]
    return query + (" " + " ".join(extra) if extra else "")


FULL_LAW_CHARS = 9000          # a law document up to this size is shown in full
PIN_COMPANY_CHARS = 45000      # all company documents are pinned if together they fit this budget


class Bm25:
    def __init__(self, corpus: list, k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(corpus)
        self.lengths = [len(d) for d in corpus]
        self.avg = (sum(self.lengths) / self.n) if self.n else 1.0
        self.postings: dict = defaultdict(list)
        for i, doc in enumerate(corpus):
            for term, tf in Counter(doc).items():
                self.postings[term].append((i, tf))
        self.idf = {t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def scores(self, query: list) -> dict:
        out: dict = defaultdict(float)
        for term, qtf in Counter(query).items():
            idf = self.idf.get(term)
            if idf is None:
                continue
            weight = idf * min(qtf, 2)
            for i, tf in self.postings[term]:
                out[i] += weight * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg))
        return out


@dataclass
class Context:
    """What is shown to the model for one question."""
    company_text: str = ""
    law_text: str = ""
    doc_ids: list = field(default_factory=list)          # every document visible to the model
    full_docs: list = field(default_factory=list)        # documents shown in full
    chunk_ids: list = field(default_factory=list)        # fragments of large documents (indices in kb.chunks)
    top_hits: list = field(default_factory=list)         # [(chunk_index, score)] for diagnostics / UI
    wide: bool = False

    @property
    def text(self) -> str:
        return self.company_text + ("\n\n" + self.law_text if self.law_text else "")


class Retriever:
    def __init__(self, kb, llm=None, settings=None):
        self.kb = kb
        self.llm = llm
        self.s = settings
        self.bm25 = Bm25([tokens(c.search_text) for c in kb.chunks])
        self.matrix = None
        self.pin_company = sum(d.size for d in kb.company_docs) <= PIN_COMPANY_CHARS
        self.is_law = [kb.by_id[c.doc_id].category == "law" for c in kb.chunks]

    # ------------------------------------------------------------------ #
    # Dense vectors
    # ------------------------------------------------------------------ #
    def build_embeddings(self, deadline=None) -> bool:
        """Cache by corpus, model and exact embedding inputs, including structural parser changes."""
        if np is None or self.llm is None or not self.s or not self.s.use_embeddings or not self.s.has_api:
            return False
        safe_model = "".join(ch if ch.isalnum() else "_" for ch in self.s.emb_model)
        texts = [c.search_text[:6000] for c in self.kb.chunks]
        input_hash = hashlib.sha256("\x00".join(texts).encode("utf-8")).hexdigest()[:16]
        cache = self.s.cache_dir / f"emb_{self.kb.fingerprint}_{safe_model}_{input_hash}.npy"
        try:
            if cache.is_file():
                m = np.load(cache)
                if m.shape[0] == len(self.kb.chunks):
                    self.matrix = m
                    return True
        except Exception:
            pass
        batches = [texts[i:i + 32] for i in range(0, len(texts), 32)]
        t0 = time.monotonic()
        try:
            first = self.llm.embed(batches[0], deadline=deadline)          # fail fast if the endpoint is absent
            with ThreadPoolExecutor(max_workers=4) as pool:
                rest = list(pool.map(lambda b: self.llm.embed(b, deadline=deadline), batches[1:]))
            vectors = [v for batch in [first] + rest for v in batch]
            self.matrix = _normalize(np.asarray(vectors, dtype=np.float32))
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                np.save(cache, self.matrix)
            except Exception:
                pass
            print(f"[retrieval] embedded {len(texts)} chunks in {time.monotonic() - t0:.1f}s", file=sys.stderr)
            return True
        except Exception as exc:
            print(f"[retrieval] embeddings unavailable ({type(exc).__name__}: {str(exc)[:120]}); lexical search only",
                  file=sys.stderr)
            self.matrix = None
            return False

    def embed_queries(self, queries: list, deadline=None):
        """Vectors for many queries at once (None entries when unavailable)."""
        if self.matrix is None or not queries:
            return [None] * len(queries)
        out = [None] * len(queries)
        try:
            for i in range(0, len(queries), 48):
                batch = [q[:2000] or " " for q in queries[i:i + 48]]
                vecs = _normalize(np.asarray(self.llm.embed(batch, deadline=deadline), dtype=np.float32))
                for j in range(len(batch)):
                    out[i + j] = vecs[j]
        except Exception as exc:
            print(f"[retrieval] query embeddings failed: {type(exc).__name__}", file=sys.stderr)
        return out

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #
    def search(self, query: str, qvec=None, top: int = 60) -> list:
        """Reciprocal-rank fusion of BM25 and dense rankings: [(chunk_index, score)]."""
        lex = self.bm25.scores(tokens(expand_query(query)))
        lex_rank = sorted(lex, key=lambda i: -lex[i])[:top]
        fused: dict = defaultdict(float)
        for r, i in enumerate(lex_rank):
            fused[i] += 1.0 / (60 + r)
        if qvec is not None and self.matrix is not None:
            sims = self.matrix @ qvec
            for r, i in enumerate(np.argsort(-sims)[:top].tolist()):
                fused[i] += 1.0 / (60 + r)
        return sorted(fused.items(), key=lambda kv: -kv[1])[:top]

    def _rerank(self, query: str, hits: list, deadline=None) -> list:
        if (self.llm is None or not self.s or not self.s.use_reranker or self.llm.reranker_ok is False
                or len(hits) < 3):
            return hits
        head = hits[:24]
        try:
            docs = [self.kb.chunks[i].search_text[:1800] for i, _ in head]
            order = self.llm.rerank(query[:1500], docs, top_n=len(docs), deadline=deadline)
            ranked = [(head[idx][0], 1.0 / (60 + r)) for r, (idx, _) in enumerate(order)]
            seen = {i for i, _ in ranked}
            ranked += [(i, s * 0.5) for i, s in head if i not in seen]
            return ranked + [(i, s * 0.5) for i, s in hits[24:]]
        except Exception:
            return hits

    # ------------------------------------------------------------------ #
    # Context assembly
    # ------------------------------------------------------------------ #
    def build_context(self, query: str, qvec=None, wide: bool = False, use_rerank: bool = True,
                      deadline=None, force_docs=()) -> Context:
        kb = self.kb
        ctx = Context(wide=wide)
        hits = self.search(query, qvec, top=80 if wide else 60)
        if use_rerank:
            hits = self._rerank(query, hits, deadline)
        ctx.top_hits = hits[:12]

        # ---- company documents: pinned in full when they are small
        if self.pin_company:
            company_ids = [d.doc_id for d in kb.company_docs]
        else:
            scored: dict = defaultdict(float)
            for i, s in hits:
                if not self.is_law[i]:
                    scored[kb.chunks[i].doc_id] += s
            company_ids = sorted(scored, key=lambda d: -scored[d])[: 8 if wide else 5]
            company_ids = sorted(company_ids, key=lambda d: int(d[1:]))
        company_ids += [d for d in force_docs if d in kb.by_id and kb.by_id[d].category != "law"
                        and d not in company_ids]
        ctx.company_text = "\n\n".join(render_company_doc(kb.by_id[d]) for d in company_ids)
        ctx.doc_ids.extend(company_ids)
        ctx.full_docs.extend(company_ids)

        # ---- laws: only when the question reaches them (always in the wide pass)
        forced_laws = [d for d in force_docs if d in kb.by_id and kb.by_id[d].category == "law"]
        if not kb.law_docs:
            return ctx
        # A law document is shown when one of its fragments is near the top of the overall ranking
        # (company fragments compete in the same list, so purely "company" questions bring no laws).
        window = 40 if wide else 10
        max_docs = 6 if wide else 3
        per_doc: dict = defaultdict(list)
        chosen: list = []
        for rank, (i, s) in enumerate(hits):
            if not self.is_law[i]:
                continue
            d = kb.chunks[i].doc_id
            per_doc[d].append((i, s))
            if rank < window and d not in chosen and len(chosen) < max_docs:
                chosen.append(d)
        chosen += [d for d in forced_laws if d not in chosen]
        if wide:      # the wide pass also brings in every small law: cheap and removes routing misses
            chosen += [d.doc_id for d in kb.law_docs if d.size <= FULL_LAW_CHARS and d.doc_id not in chosen]
        if not chosen:
            return ctx

        budget = 110000 if wide else 30000
        per_doc_budget = 26000 if wide else 12000
        full_limit = 22000 if wide else FULL_LAW_CHARS
        blocks = []
        for d in chosen:
            doc = kb.by_id[d]
            if doc.size <= full_limit:
                text = render_law_full(doc)
                if budget - len(text) < 0 and blocks:
                    continue
                blocks.append(text)
                budget -= len(text)
                ctx.full_docs.append(d)
            else:
                picked = self._pick_fragments(d, per_doc.get(d, []), min(per_doc_budget, budget), wide)
                if not picked:
                    continue
                text = render_law_fragments(doc, [kb.chunks[i] for i in picked])
                blocks.append(text)
                budget -= len(text)
                ctx.chunk_ids.extend(picked)
            ctx.doc_ids.append(d)
            if budget <= 1500:
                break
        ctx.law_text = "\n\n".join(blocks)
        return ctx

    def _pick_fragments(self, doc_id: str, doc_hits: list, budget: int, wide: bool) -> list:
        """Best fragments of a large document plus their structural neighbours, within a character budget."""
        kb = self.kb
        doc = kb.by_id[doc_id]
        by_unit: dict = defaultdict(list)
        first_of_doc = None
        for idx, ch in enumerate(kb.chunks):
            if ch.doc_id == doc_id:
                by_unit[ch.unit_idx].append(idx)
                if first_of_doc is None:
                    first_of_doc = idx
        picked: list = []
        used = 0

        def add(idx: int) -> bool:
            nonlocal used
            if idx in picked:
                return True
            size = len(kb.chunks[idx].text) + 1
            if used + size > budget:
                return False
            picked.append(idx)
            used += size
            return True

        if first_of_doc is not None:                      # the opening unit usually holds the general rule
            add(first_of_doc)
        limit = 22 if wide else 9
        for idx, _ in doc_hits[:limit]:
            if not add(idx):
                break
            unit = doc.units[kb.chunks[idx].unit_idx]
            siblings = by_unit[unit.idx]
            for sib in siblings[:4]:                      # other pieces of the same long unit
                add(sib)
            if unit.kind == "subpoint":                   # lead-in paragraph of the parent point
                for u in reversed(doc.units[: unit.idx]):
                    if u.kind == "point":
                        if by_unit[u.idx]:
                            add(by_unit[u.idx][0])
                        break
        if len(picked) <= 1 and first_of_doc is not None:  # forced document without hits: show its beginning
            for idx in range(first_of_doc, min(first_of_doc + 6, len(kb.chunks))):
                if kb.chunks[idx].doc_id != doc_id or not add(idx):
                    break
        return picked


def _normalize(m):
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms
