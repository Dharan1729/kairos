"""kairos/mcp_server.py. The tool functions are called directly (the MCP
decorator returns them unchanged) with httpx routed to the in-process app,
so the full HTTP and auth path runs without a network."""

import pytest
from starlette.testclient import TestClient as StarletteTestClient

import kairos.mcp_server as mcp_module
from kairos.api import app as fastapi_app

A = "person-team-platform-0"


@pytest.fixture(autouse=True)
def _route_mcp_through_test_app(monkeypatch, client, make_key):
    monkeypatch.setattr(
        mcp_module.httpx, "Client", lambda *a, **kw: StarletteTestClient(fastapi_app, headers=kw.get("headers"))
    )
    key = make_key(role="member", person_id=A)
    monkeypatch.setenv("KAIROS_API_KEY", key)
    monkeypatch.delenv("KAIROS_DEFAULT_REPO", raising=False)
    return key


def test_get_context_returns_the_key_owners_context():
    result = mcp_module.get_context(task="add a health check endpoint")
    assert result.startswith("[")
    assert "Hana:" in result  # A's own personal item


def test_get_context_with_repo_includes_project_layer():
    result = mcp_module.get_context(task="event backbone and replay", repo="nimbus/orchestrator-core")
    assert "[project]" in result


def test_unbound_service_key_gets_org_layer_only(monkeypatch, make_key):
    monkeypatch.setenv("KAIROS_API_KEY", make_key(role="member"))
    result = mcp_module.get_context(task="add a health check endpoint")
    assert all(line.startswith("[org]") for line in result.splitlines())


def test_search_returns_results():
    assert "Terraform" in mcp_module.search(query="terraform infrastructure")


def test_remember_never_writes_knowledge_directly():
    result = mcp_module.remember(content="Ship behind a feature flag xyzzy42.", layer="team", scope="team-platform")
    assert "candidate #" in result and "status: pending" in result
    assert "xyzzy42" not in mcp_module.search(query="feature flag xyzzy42")


def test_proposal_is_served_only_after_approval():
    msg = mcp_module.remember(content="A very specific unapproved fact xyzzy123.", layer="personal", scope=A)
    cid = int(msg.split("candidate #")[1].split(" ")[0])
    assert "xyzzy123" not in mcp_module.search(query="xyzzy123")
    from kairos.db import get_sessionmaker
    from kairos.knowledge import approve_candidate

    db = get_sessionmaker()()
    try:
        approve_candidate(db, cid, reviewed_by="test")
        db.commit()
    finally:
        db.close()
    assert "xyzzy123" in mcp_module.search(query="xyzzy123")


def test_remember_into_another_users_personal_scope_is_refused():
    with pytest.raises(RuntimeError, match="403"):
        mcp_module.remember(content="poison", layer="personal", scope="person-team-platform-mgr")


def test_remember_into_another_team_is_refused():
    with pytest.raises(RuntimeError, match="403"):
        mcp_module.remember(content="poison", layer="team", scope="team-ml")


def test_connect_error_is_readable(monkeypatch):
    import httpx

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(mcp_module.httpx, "Client", _raise)
    with pytest.raises(RuntimeError, match="is it running"):
        mcp_module.get_context(task="anything")
