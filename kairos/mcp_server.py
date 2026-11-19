"""Kairos MCP server (stdio): lets an MCP client such as Claude Code or
Cursor call Kairos during a session.

A thin client of the HTTP API. It sends no user id: the server takes the
user from the credential, so an agent can only ever get its own user's
context. Use a key bound to a person (``kairos keys create --person ...``).

Tools:
  get_context(task, repo?)   assemble ranked, budgeted context for a task
  search(query, repo?)       ad-hoc search when the first bundle was not enough
  remember(content, layer, scope)
                             propose a fact; it is NOT used until a human
                             approves it (kairos candidates approve <id>)

Environment:
  KAIROS_URL           server URL (default http://127.0.0.1:8000)
  KAIROS_API_KEY       bearer credential (default: demo_user key from $KAIROS_HOME/dev_keys.json)
  KAIROS_DEFAULT_REPO  repo name used when a tool call gives none
  KAIROS_BUDGET_TOKENS token budget for get_context (default 3000)
"""

from __future__ import annotations

import os

import httpx
from mcp.server.mcpserver import MCPServer

DEFAULT_URL = "http://127.0.0.1:8000"

app = MCPServer(
    name="kairos",
    instructions=(
        "Kairos assembles org/team/project/personal/session knowledge into "
        "ready-to-use context for coding tasks. Call get_context near the "
        "start of a task to pull in what's already known; call search "
        "mid-task if you need something specific the initial bundle didn't "
        "cover. Use remember to propose something worth keeping for next "
        "time; it will NOT become part of future context until a human "
        "explicitly approves it via the kairos CLI."
    ),
)


def _base_url() -> str:
    return os.environ.get("KAIROS_URL", DEFAULT_URL)


def _api_key() -> str | None:
    if key := os.environ.get("KAIROS_API_KEY"):
        return key
    from kairos.seed import load_dev_key

    return load_dev_key("demo_user")


def _default_repo() -> str | None:
    return os.environ.get("KAIROS_DEFAULT_REPO") or None


def _budget() -> int:
    return int(os.environ.get("KAIROS_BUDGET_TOKENS", "3000"))


def _post(path: str, body: dict) -> dict:
    key = _api_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        with httpx.Client(base_url=_base_url(), headers=headers, timeout=30.0) as client:
            resp = client.post(path, json=body)
    except httpx.ConnectError:
        raise RuntimeError(f"couldn't reach Kairos at {_base_url()}; is it running?") from None
    if resp.status_code >= 400:
        raise RuntimeError(f"Kairos returned {resp.status_code}: {resp.text}")
    return resp.json()


def _format(items: list[dict], empty: str) -> str:
    if not items:
        return empty
    return "\n".join(f"[{i['layer']}] {i['content']}" for i in items)


@app.tool()
def get_context(task: str, repo: str | None = None) -> str:
    """Fetch approved org/team/project/personal context relevant to a task,
    ranked and fit to a token budget. Call this near the start of a task."""
    result = _post(
        "/v1/assemble_context",
        {
            "context_principal": {"agent_type": "mcp", "repo": repo or _default_repo()},
            "task": {"description": task},
            "budget_tokens": _budget(),
        },
    )
    return _format(result.get("context_bundle", []), "No relevant context found.")


@app.tool()
def search(query: str, repo: str | None = None) -> str:
    """Search for something specific mid-task, when get_context's bundle did
    not cover it."""
    result = _post(
        "/v1/search_knowledge",
        {
            "context_principal": {"agent_type": "mcp", "repo": repo or _default_repo()},
            "query": query,
            "budget_tokens": 2000,
        },
    )
    return _format(result.get("results", []), "No matching context found.")


@app.tool()
def remember(content: str, layer: str, scope: str) -> str:
    """Propose a fact worth keeping (a decision, a convention, a preference).
    It is queued for human review and is NOT used until approved.

    layer: org|team|project|personal|session.
    scope: the scope id: a project id for a project fact, a team id for a
    team fact, your own user id for a personal one."""
    result = _post(
        "/v1/candidates",
        {
            "proposed_content": content,
            "proposed_layer": layer,
            "proposed_scope_id": scope,
        },
    )
    return (
        f"Proposed as candidate #{result['id']} (status: {result['status']}). Not part of context yet: "
        f"a reviewer runs `kairos candidates approve {result['id']} --by <name>` to keep it, "
        f"or `reject` to discard it."
    )


def main():
    app.run()


if __name__ == "__main__":
    main()
