"""Parser changes must invalidate vectors even when document bytes are unchanged."""

from pathlib import Path

from la_config import load_settings
from la_kb import load_kb
from la_retrieval import Retriever


def test_cache_depends_on_exact_chunk_inputs(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BASE_URL", "http://unused.invalid")
    monkeypatch.setenv("LLM_API_KEY", "mock")
    monkeypatch.setenv("LA_CACHE_DIR", str(tmp_path))
    settings = load_settings()
    settings.use_embeddings = True

    class Embedder:
        calls = 0

        def embed(self, texts, **kwargs):
            self.calls += 1
            return [[float(len(text)), 1.0] for text in texts]

    llm = Embedder()
    path = Path(__file__).resolve().parent.parent / "data" / "knowledge-base.zip"
    kb = load_kb(path)
    assert Retriever(kb, llm, settings).build_embeddings()
    before = llm.calls
    assert before > 0
    assert Retriever(kb, llm, settings).build_embeddings() and llm.calls == before

    changed = load_kb(path)
    changed.chunks[0].search_text += " новая структурная метка"
    assert changed.fingerprint == kb.fingerprint and len(changed.chunks) == len(kb.chunks)
    assert Retriever(changed, llm, settings).build_embeddings() and llm.calls > before
    assert len(list(tmp_path.glob("emb_*.npy"))) == 2
