"""A small Python client for agent integrations (for example a session-start
hook that prepends context to a prompt):

    from kairos.adapters.claude_code import ContextProvider
    provider = ContextProvider(api_key="kairos_...", budget_tokens=4000)
    text = provider({"task_description": "Add a /healthz endpoint", "repo": "acme/api"})

An HTTP wrapper around POST /v1/assemble_context and /v1/search_knowledge
that formats the bundle as plain text. With a person-bound key, user_id can
be left out: the server takes the user from the key.
"""

import httpx


# Fail open: a Kairos outage must not block the agent's
# session — degrade to no context rather than raising. But "outage" means
# connection failures and 5xx server errors SPECIFICALLY, not any non-2xx
# response: an earlier version propagated only 401/403 and swallowed
# everything else, which also silently degraded 400/404/422 — i.e. a bug
# in this adapter's own request (wrong shape, bad field) would masquerade
# as a routine "Kairos is down" degrade instead of surfacing as the
# integration bug it actually is. The correct line is severity-based, not
# an allowlist of specific codes: 5xx (something wrong with Kairos) and
# network-level failures degrade; everything else (4xx — something wrong
# with THIS request) propagates. httpx.HTTPStatusError and
# httpx.RequestError are SIBLING exceptions under httpx.HTTPError, not
# parent/child — both must be caught explicitly, neither implies the other.
def _post_with_fail_open(url: str, json_body: dict, headers: dict, timeout: float, degraded: dict) -> dict:
    try:
        resp = httpx.post(url, json=json_body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500:
            return degraded
        raise  # 4xx: a request/auth/setup bug, not an outage — must surface
    except httpx.RequestError:
        return degraded


class ContextProvider:
    def __init__(
        self,
        api_key: str,
        budget_tokens: int = 4000,
        base_url: str = "http://127.0.0.1:8000",
        agent_type: str = "claude_code",
        timeout: float = 10.0,
    ):
        self.api_key = api_key
        self.budget_tokens = budget_tokens
        self.base_url = base_url.rstrip("/")
        self.agent_type = agent_type
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def fetch_context(
        self,
        user_id: str | None,
        repo: str | None,
        session_id: str | None,
        task_description: str,
        recent_messages: list[str] | None = None,
    ) -> dict:
        request_body = {
            "context_principal": {
                "user_id": user_id,
                "agent_type": self.agent_type,
                "repo": repo,
                "session_id": session_id,
            },
            "task": {"description": task_description, "recent_messages": recent_messages or []},
            "budget_tokens": self.budget_tokens,
        }
        return _post_with_fail_open(
            f"{self.base_url}/v1/assemble_context",
            request_body,
            self._headers(),
            self.timeout,
            degraded={"context_bundle": [], "cache": {"hit": False}, "tokens_used": 0, "dropped": []},
        )

    def search(
        self, user_id: str | None, repo: str | None, session_id: str | None, query: str, budget_tokens: int = 2000
    ) -> dict:
        """On-demand retrieval (after Letta/MemGPT, see docs/prior-art.md): lets the agent explicitly ask for more
        context mid-session — e.g. if the auto-assembled bundle from
        fetch_context() turns out insufficient for what it's actually
        working on — instead of only getting one fixed shot per session."""
        request_body = {
            "context_principal": {
                "user_id": user_id,
                "agent_type": self.agent_type,
                "repo": repo,
                "session_id": session_id,
            },
            "query": query,
            "budget_tokens": budget_tokens,
        }
        return _post_with_fail_open(
            f"{self.base_url}/v1/search_knowledge",
            request_body,
            self._headers(),
            self.timeout,
            degraded={"results": [], "cache": {"hit": False}, "dropped": []},
        )

    @staticmethod
    def _format_items(items: list[dict], dropped: list[dict]) -> str:
        lines = [f"[{item['layer']}] {item['content']}" for item in items]
        if dropped:
            lines.append(f"(context truncated: dropped {dropped})")
        return "\n".join(lines)

    @classmethod
    def format_bundle(cls, response: dict) -> str:
        """For fetch_context()'s response shape (key: context_bundle)."""
        return cls._format_items(response.get("context_bundle", []), response.get("dropped", []))

    @classmethod
    def format_search_results(cls, response: dict) -> str:
        """For search()'s response shape (key: results) — a distinct method
        rather than overloading format_bundle, since silently reading the
        wrong key for the wrong response shape returns an empty string
        with no error, hiding real results from the agent's prompt."""
        return cls._format_items(response.get("results", []), response.get("dropped", []))

    def __call__(self, session_context: dict) -> str:
        """session_context: {user_id, repo, session_id, task_description, recent_messages?}
        — matches the shape a session_start hook would supply."""
        response = self.fetch_context(
            user_id=session_context.get("user_id"),
            repo=session_context.get("repo"),
            session_id=session_context.get("session_id"),
            task_description=session_context.get("task_description", ""),
            recent_messages=session_context.get("recent_messages"),
        )
        return self.format_bundle(response)
