"""Runtime settings. Everything comes from the environment; nothing secret is stored in code."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    # the platform may pass a placeholder equal to the variable name
    return default if not value or value == name else value


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = env(name)
        if value:
            return value
    return default


def env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(float(env(name)))))
    except ValueError:
        return default


def env_float(name: str, default: float, low: float = 0.0, high: float = 2.0) -> float:
    try:
        return max(low, min(high, float(env(name))))
    except ValueError:
        return default


def env_flag(name: str, default: bool) -> bool:
    value = env(name).lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on", "да")


@dataclass
class Settings:
    data_dir: Path
    api_base: str
    llm_key: str
    embed_key: str
    rerank_key: str
    llm_model: str
    emb_model: str
    reranker_model: str          # empty -> probe a list of known ids
    temperature: float
    max_tokens: int
    max_workers: int
    time_budget: float           # seconds for a whole batch run
    request_timeout: float
    use_embeddings: bool
    use_reranker: bool
    second_pass: bool
    repair: bool
    cache_dir: Path

    @property
    def has_api(self) -> bool:
        return bool(self.api_base and self.llm_key)


def load_settings() -> Settings:
    llm_key = env_first("LLM_API_KEY", "OPENAI_API_KEY")
    return Settings(
        data_dir=Path(env("DATA_DIR", "data")),
        api_base=env_first("LLM_BASE_URL", "OPENAI_BASE_URL", "BASE_URL").rstrip("/"),
        llm_key=llm_key,
        embed_key=env("EMBEDDER_API_KEY") or llm_key,
        rerank_key=env("RERANKER_API_KEY") or llm_key,
        llm_model=env_first("LLM_MODEL", "GEN_MODEL", "OPENAI_MODEL", default="deepseek/deepseek-v4.1-flash"),
        emb_model=env("EMB_MODEL", "baai/bge-m3"),
        reranker_model=env("RERANKER_MODEL"),
        # answers must be reproducible and literal, so the default is 0 regardless of the task config
        temperature=env_float("LA_TEMPERATURE", 0.0),
        # the model reasons before answering and the reasoning counts towards the limit, so the task's
        # MAX_TOKENS is treated as a floor, not a ceiling
        max_tokens=max(env_int("MAX_TOKENS", 3000, 256, 8192), env_int("LA_MIN_MAX_TOKENS", 3000, 256, 8192)),
        max_workers=env_int("LA_MAX_WORKERS", env_int("MAX_WORKERS", 4, 1, 8), 1, 16),
        time_budget=env_float("LA_TIME_BUDGET", 16 * 60.0, 30.0, 24 * 3600.0),
        request_timeout=env_float("LA_REQUEST_TIMEOUT", 75.0, 5.0, 600.0),
        use_embeddings=env_flag("LA_USE_EMBEDDINGS", True),
        use_reranker=env_flag("LA_USE_RERANKER", True),
        second_pass=env_flag("LA_SECOND_PASS", True),
        repair=env_flag("LA_REPAIR", True),
        cache_dir=Path(env("LA_CACHE_DIR", "/tmp/la_cache")),
    )
