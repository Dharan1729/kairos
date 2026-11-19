"""Runtime settings, read from environment variables.

Settings are read on every call to ``load_settings()`` rather than frozen at
import time, so tests and long-running tools can change the environment
without re-importing modules. Reading a handful of environment variables is
cheap compared to anything else on the request path.

See docs/guide/configuration.md for the full reference.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_CACHE_SIMILARITY = 0.90
DEFAULT_CACHE_TTL_SECONDS = 24 * 3600
DEFAULT_MAX_BUDGET_TOKENS = 32_000


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def kairos_home() -> Path:
    """Directory for local state: the default SQLite database and the dev
    keys written by `kairos seed`. Defaults to ./.kairos in the current
    working directory so a quickstart never writes outside the folder it was
    started from."""
    return Path(os.environ.get("KAIROS_HOME", ".kairos")).expanduser()


@dataclass(frozen=True)
class Settings:
    database_url: str
    embedder: str
    embedding_model: str
    cache_enabled: bool
    cache_cross_user_reuse: bool
    cache_similarity: float
    cache_ttl_seconds: int
    max_budget_tokens: int
    llm_provider: str
    git_extract_roots: tuple[Path, ...] = field(default_factory=tuple)


def load_settings() -> Settings:
    home = kairos_home()
    roots = tuple(
        Path(p).expanduser().resolve()
        for p in os.environ.get("KAIROS_GIT_EXTRACT_ROOTS", "").split(os.pathsep)
        if p.strip()
    )
    return Settings(
        database_url=os.environ.get("KAIROS_DATABASE_URL") or f"sqlite:///{home / 'kairos.db'}",
        embedder=os.environ.get("KAIROS_EMBEDDER", "sentence-transformers"),
        embedding_model=os.environ.get("KAIROS_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        cache_enabled=_bool("KAIROS_CACHE_ENABLED", True),
        cache_cross_user_reuse=_bool("KAIROS_CACHE_CROSS_USER_REUSE", False),
        cache_similarity=_float("KAIROS_CACHE_SIMILARITY", DEFAULT_CACHE_SIMILARITY),
        cache_ttl_seconds=_int("KAIROS_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS),
        max_budget_tokens=_int("KAIROS_MAX_BUDGET_TOKENS", DEFAULT_MAX_BUDGET_TOKENS),
        llm_provider=os.environ.get("KAIROS_LLM_PROVIDER", "none").strip().lower(),
        git_extract_roots=roots,
    )
