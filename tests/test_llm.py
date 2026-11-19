"""Tests for kairos/llm.py and its two wiring points:
- kairos/pipeline/budget.py's compress_content (compression stage)
- kairos/knowledge.py's propose_llm_commit_summary (auto-proposal)

No real network calls anywhere here — get_llm_provider() itself is tested
against monkeypatched env vars (asserting it returns None absent a key,
the state every other test in this suite runs under), and the wiring
tests use a small in-process FakeProvider instead of AnthropicProvider/
OpenAiProvider so they don't depend on a real vendor key or network access.
"""

from kairos.knowledge import propose_llm_commit_summary
from kairos.llm import AnthropicProvider, OpenAiProvider, get_llm_provider
from kairos.pipeline.budget import GreedyBudgeter, compress_content, estimate_tokens
from kairos.pipeline.types import Candidate, ScoredCandidate


class FakeProvider:
    name = "fake"

    def __init__(self, response: str | None = None, raises: bool = False):
        self.response = response
        self.raises = raises
        self.calls: list[tuple[str, int]] = []

    def summarize(self, text: str, *, max_tokens: int) -> str:
        self.calls.append((text, max_tokens))
        if self.raises:
            raise RuntimeError("simulated provider failure")
        return self.response


class TestGetLlmProvider:
    def test_off_by_default_even_with_a_vendor_key(self, monkeypatch):
        # A key in the environment alone must not start sending knowledge
        # content to a third party.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-fake")
        assert get_llm_provider() is None

    def test_anthropic_selected_explicitly(self, monkeypatch):
        monkeypatch.setenv("KAIROS_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        assert isinstance(get_llm_provider(), AnthropicProvider)

    def test_openai_selected_explicitly(self, monkeypatch):
        monkeypatch.setenv("KAIROS_LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-fake")
        assert isinstance(get_llm_provider(), OpenAiProvider)

    def test_selected_provider_without_key_is_none(self, monkeypatch):
        monkeypatch.setenv("KAIROS_LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert get_llm_provider() is None


class TestCompressContent:
    def test_no_provider_falls_back_to_truncation(self):
        long_text = "x" * 200
        result = compress_content(long_text, max_tokens=10, llm_provider=None)
        assert result.endswith("…")
        assert len(result) <= 41  # 10 tokens * 4 chars + ellipsis, truncate_to_tokens' own contract

    def test_provider_summary_used_when_it_fits_the_budget(self):
        provider = FakeProvider(response="short summary")
        result = compress_content("x" * 200, max_tokens=10, llm_provider=provider)
        assert result == "short summary"
        assert provider.calls == [("x" * 200, 10)]

    def test_provider_failure_falls_back_to_truncation(self):
        provider = FakeProvider(raises=True)
        result = compress_content("x" * 200, max_tokens=10, llm_provider=provider)
        assert result.endswith("…")

    def test_oversized_provider_response_is_truncated_not_discarded(self):
        # A provider that ignores max_tokens and returns something too long
        # must not blow the budget just because it "succeeded" — but the
        # oversized SUMMARY gets truncated, not thrown away in favor of
        # truncating the original (uncompressed, usually much longer) text.
        provider = FakeProvider(response="y" * 500)
        result = compress_content("x" * 200, max_tokens=10, llm_provider=provider)
        assert result.endswith("…")
        assert result.startswith("y")  # truncated the summary, not the original "x"*200 text
        assert estimate_tokens(result) <= 10

    def test_empty_provider_response_falls_back_to_truncation(self):
        provider = FakeProvider(response="")
        result = compress_content("x" * 200, max_tokens=10, llm_provider=provider)
        assert result.endswith("…")


def _ranked(content: str) -> list[ScoredCandidate]:
    c = Candidate(
        id="c1", layer="team", scope_id="s1", content=content, confidence=1.0, staleness_hint="low", similarity=1.0
    )
    return [ScoredCandidate(c, 1.0)]


class TestBudgeterWithLlmProvider:
    def test_no_provider_truncates(self):
        result = GreedyBudgeter(lambda: None).fit(_ranked("x" * 200), budget_tokens=25)
        assert result.bundle[0].content.endswith("…")
        assert result.tokens_used <= 25

    def test_configured_provider_is_used_for_compression(self):
        # ~50 tokens of content, budget 25: above MIN_TOKENS_TO_COMPRESS, so
        # the compress branch runs.
        provider = FakeProvider(response="concise")
        result = GreedyBudgeter(lambda: provider).fit(_ranked("x" * 200), budget_tokens=25)
        assert result.bundle[0].content == "concise"

    def test_provider_not_built_when_nothing_needs_compressing(self):
        built = []
        GreedyBudgeter(lambda: built.append(1)).fit(_ranked("short"), budget_tokens=100)
        assert built == []


class TestProposeLlmCommitSummary:
    def test_no_provider_returns_none(self, db_session):
        result = propose_llm_commit_summary(
            db_session,
            None,
            scope_id="team-platform",
            layer="team",
            commit_subjects=["fix bug", "add feature"],
        )
        assert result is None

    def test_no_commits_returns_none(self, db_session):
        provider = FakeProvider(response="summary")
        result = propose_llm_commit_summary(
            db_session,
            provider,
            scope_id="team-platform",
            layer="team",
            commit_subjects=[],
        )
        assert result is None
        assert provider.calls == []

    def test_provider_failure_returns_none_not_raise(self, db_session):
        provider = FakeProvider(raises=True)
        result = propose_llm_commit_summary(
            db_session,
            provider,
            scope_id="team-platform",
            layer="team",
            commit_subjects=["fix bug"],
        )
        assert result is None

    def test_successful_summary_creates_an_llm_summary_knowledge_item(self, db_session):
        provider = FakeProvider(response="Recent work focused on bug fixes and a new feature.")
        item = propose_llm_commit_summary(
            db_session,
            provider,
            scope_id="team-platform",
            layer="team",
            commit_subjects=["fix bug", "add feature"],
        )
        assert item is not None
        assert item.origin == "llm_summary"
        assert item.content == "Recent work focused on bug fixes and a new feature."
        assert item.scope_id == "team-platform"
