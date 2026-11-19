"""Budgeting: walk the ranked candidates and keep what fits.

Greedy in rank order. An item that fits is kept whole. The first item that
does not fit is compressed into the remaining space if at least
``MIN_TOKENS_TO_COMPRESS`` tokens are left (an LLM summary when a provider is
configured, otherwise truncation); otherwise it is dropped. Everything left
out is reported in ``dropped`` by layer and reason: nothing is truncated
silently.

Token counts use a 4-characters-per-token estimate, not a model tokenizer.
The guarantee is ``tokens_used <= budget_tokens`` under that estimate.
"""

from __future__ import annotations

from collections.abc import Callable

from kairos.llm import LlmProvider, get_llm_provider
from kairos.pipeline.types import BudgetResult, BundleItem, ScoredCandidate

MIN_TOKENS_TO_COMPRESS = 20


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def compress_content(text: str, max_tokens: int, llm_provider: LlmProvider | None) -> str:
    """An LLM summary when a provider is configured and answers; truncation
    otherwise. Never raises, never exceeds max_tokens."""
    if llm_provider is not None:
        try:
            summary = llm_provider.summarize(text, max_tokens=max_tokens)
            if summary:
                return summary if estimate_tokens(summary) <= max_tokens else truncate_to_tokens(summary, max_tokens)
        except Exception:
            pass
    return truncate_to_tokens(text, max_tokens)


def add_dropped(dropped: dict[tuple[str, str], int], layer: str, reason: str, count: int = 1) -> None:
    dropped[(layer, reason)] = dropped.get((layer, reason), 0) + count


def dropped_list(dropped: dict[tuple[str, str], int]) -> list[dict]:
    return [{"layer": layer, "count": count, "reason": reason} for (layer, reason), count in sorted(dropped.items())]


class GreedyBudgeter:
    def __init__(self, llm_provider_factory: Callable[[], LlmProvider | None] = get_llm_provider):
        self._llm = llm_provider_factory

    def fit(self, ranked: list[ScoredCandidate], budget_tokens: int) -> BudgetResult:
        bundle: list[BundleItem] = []
        used = 0
        dropped: dict[tuple[str, str], int] = {}
        provider = None
        provider_loaded = False
        for sc in ranked:
            c = sc.candidate
            remaining = budget_tokens - used
            tokens = estimate_tokens(c.content)
            if tokens <= remaining:
                bundle.append(BundleItem(layer=c.layer, content=c.content, source=c.id))
                used += tokens
            elif remaining >= MIN_TOKENS_TO_COMPRESS:
                if not provider_loaded:
                    provider, provider_loaded = self._llm(), True
                compressed = compress_content(c.content, remaining, provider)
                bundle.append(BundleItem(layer=c.layer, content=compressed, source=c.id))
                used += estimate_tokens(compressed)
            else:
                add_dropped(dropped, c.layer, "budget")
        return BudgetResult(bundle=bundle, dropped=dropped_list(dropped), tokens_used=used)
