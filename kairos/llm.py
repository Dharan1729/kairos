"""Optional LLM summarization, used in two places only:

- compressing the last item that does not fit the budget (instead of
  truncating it), in kairos/pipeline/budget.py;
- summarizing a batch of new commits into one item, in kairos/watch.py.

Off by default. Turning it on sends knowledge content to a third-party API,
so it needs an explicit ``KAIROS_LLM_PROVIDER=anthropic`` or ``=openai`` as
well as that vendor's key. An API key in the environment alone does nothing.
Every caller falls back to plain truncation on any failure.

No vendor SDK is a dependency: each provider is a thin wrapper over the
vendor's HTTP API via httpx.
"""

from __future__ import annotations

import os
from typing import Protocol

import httpx

from kairos.config import load_settings

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-4o-mini"}


class LlmProvider(Protocol):
    name: str

    def summarize(self, text: str, *, max_tokens: int) -> str:
        """A summary of roughly max_tokens (4 characters per token). Raises
        on any failure; callers decide how to fall back."""
        ...


def _summarize_prompt(text: str, max_tokens: int) -> str:
    return (
        f"Summarize the following in under {max_tokens * 4} characters, keeping specific, "
        f"actionable facts. Reply with only the summary.\n\n{text}"
    )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = DEFAULT_MODELS["anthropic"]):
        self._api_key = api_key
        self._model = model

    def summarize(self, text: str, *, max_tokens: int) -> str:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self._api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": self._model,
                "max_tokens": max(256, max_tokens * 2),
                "thinking": {"type": "disabled"},
                "messages": [{"role": "user", "content": _summarize_prompt(text, max_tokens)}],
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json()
        texts = [b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"]
        return "".join(texts).strip()


class OpenAiProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str = DEFAULT_MODELS["openai"]):
        self._api_key = api_key
        self._model = model

    def summarize(self, text: str, *, max_tokens: int) -> str:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}", "content-type": "application/json"},
            json={
                "model": self._model,
                "max_completion_tokens": max(64, max_tokens),
                "messages": [{"role": "user", "content": _summarize_prompt(text, max_tokens)}],
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def get_llm_provider() -> LlmProvider | None:
    """None unless KAIROS_LLM_PROVIDER names a vendor whose key is set."""
    provider = load_settings().llm_provider
    model = os.environ.get("KAIROS_LLM_MODEL")
    if provider == "anthropic" and (key := os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicProvider(key, model=model or DEFAULT_MODELS["anthropic"])
    if provider == "openai" and (key := os.environ.get("OPENAI_API_KEY")):
        return OpenAiProvider(key, model=model or DEFAULT_MODELS["openai"])
    return None
