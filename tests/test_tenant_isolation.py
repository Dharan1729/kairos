"""Cross-tenant access attempts against every endpoint and MCP tool.

Two tenants: "default" (the demo org, org-1) and "other" (org-2, from the
`second_tenant` fixture, every layer holding an item whose content starts
with OTHER-TENANT SECRET). Each test uses one tenant's credential against
the other tenant's ids and checks the request is refused or returns
nothing of the other tenant's.
"""

import pytest
from starlette.testclient import TestClient as StarletteTestClient

import kairos.mcp_server as mcp_module
from kairos.api import app as fastapi_app

SECRET = "OTHER-TENANT SECRET"


@pytest.fixture()
def other(second_tenant, as_key):
    return as_key(second_tenant)


def body(user=None, session=None, repo=None):
    return {
        "context_principal": {"user_id": user, "session_id": session, "repo": repo},
        "task": {"description": "add a /healthz endpoint"},
        "budget_tokens": 4000,
    }


def search_body(user=None, session=None, repo=None):
    return {
        "context_principal": {"user_id": user, "session_id": session, "repo": repo},
        "query": "add a /healthz endpoint",
        "budget_tokens": 4000,
    }


@pytest.mark.parametrize(
    "principal",
    [
        {"user": "person-other"},
        {"session": "session-other"},
        {"user": "person-team-platform-0", "session": "session-other"},
    ],
)
@pytest.mark.parametrize("path,make", [("/v1/assemble_context", body), ("/v1/search_knowledge", search_body)])
def test_naming_another_tenants_user_or_session_is_refused(client, other, principal, path, make):
    resp = client.post(path, json=make(**principal))
    assert resp.status_code == 403, resp.text
    resp2 = other.post(path, json=make(user="person-team-platform-0"))
    assert resp2.status_code == 403


@pytest.mark.parametrize("path,make", [("/v1/assemble_context", body), ("/v1/search_knowledge", search_body)])
def test_another_tenants_repo_resolves_to_nothing(client, other, path, make):
    resp = client.post(path, json=make(user="person-team-platform-0", repo="other/repo"))
    assert resp.status_code == 200
    assert SECRET not in resp.text
    assert any("unknown repo" in n for n in resp.json()["scope"]["notes"])


def test_own_tenant_reads_never_include_other_tenant_items(client, other):
    for resp in (
        client.post("/v1/assemble_context", json=body(user="person-team-platform-0", session="session-0")),
        other.post("/v1/assemble_context", json=body(user="person-other", session="session-other")),
    ):
        assert resp.status_code == 200
    assert SECRET not in client.post("/v1/assemble_context", json=body(user="person-team-platform-0")).text
    assert SECRET in other.post("/v1/assemble_context", json=body(user="person-other")).text


@pytest.mark.parametrize(
    "layer,scope",
    [
        ("org", "org-2"),
        ("team", "team-other"),
        ("project", "proj-other"),
        ("personal", "person-other"),
        ("session", "session-other"),
        ("team", "made-up-scope"),
        ("team", ""),
    ],
)
def test_writes_into_another_tenants_or_unknown_scope_are_refused(client, other, layer, scope):
    assert (
        client.post("/v1/knowledge_items", json={"layer": layer, "scope_id": scope, "content": "x"}).status_code == 403
    )
    assert (
        client.post(
            "/v1/candidates", json={"proposed_content": "x", "proposed_layer": layer, "proposed_scope_id": scope}
        ).status_code
        == 403
    )


def test_revise_another_tenants_item_is_404(client, other):
    assert client.post("/v1/knowledge_items/other-team/revise", json={"content": "x"}).status_code == 404
    assert other.post("/v1/knowledge_items/ctx-0/revise", json={"content": "x"}).status_code == 404


def test_candidates_of_another_tenant_are_indistinguishable_from_missing(client, other):
    cid = client.post(
        "/v1/candidates",
        json={"proposed_content": "mine", "proposed_layer": "team", "proposed_scope_id": "team-platform"},
    ).json()["id"]
    foreign = other.get(f"/v1/candidates/{cid}")
    missing = other.get("/v1/candidates/999999")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    for action in ("approve", "reject"):
        assert other.post(f"/v1/candidates/{cid}/{action}", json={"reviewed_by": "x"}).status_code == 404
    assert client.get(f"/v1/candidates/{cid}").status_code == 200


def test_git_extract_into_another_tenant_is_refused(client, other, tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_GIT_EXTRACT_ROOTS", str(tmp_path))
    resp = client.post("/v1/extract/git", json={"repo_path": str(tmp_path), "scope_id": "proj-other"})
    assert resp.status_code == 403


def test_api_keys_are_tenant_scoped(client, other):
    other_ids = {k["id"] for k in other.get("/v1/api_keys").json()}
    own_ids = {k["id"] for k in client.get("/v1/api_keys").json()}
    assert other_ids and own_ids and not other_ids & own_ids
    for key_id in other_ids:
        assert client.post(f"/v1/api_keys/{key_id}/revoke").status_code == 404
    assert client.post("/v1/api_keys", json={"person_id": "person-other"}).status_code == 403


def test_dashboard_is_tenant_scoped(client, other):
    other.post("/v1/assemble_context", json=body(user="person-other"))
    other.post("/v1/assemble_context", json=body(user="person-other"))
    stats = client.get("/v1/dashboard").json()
    assert stats["tenant_id"] == "default" and stats["total_requests"] == 0
    assert other.get("/v1/dashboard").json()["total_requests"] == 2
    assert other.get("/v1/dashboard").json()["knowledge_freshness"]["total_items"] == 5


# ---- MCP tools --------------------------------------------------------------


@pytest.fixture()
def mcp_as_other(monkeypatch, client, second_tenant, make_key):
    """MCP routed to the in-process app with a person-bound key of the other tenant."""
    from kairos.auth import generate_api_key
    from kairos.db import get_sessionmaker

    db = get_sessionmaker()()
    try:
        key = generate_api_key(db, "other", label="mcp", role="member", person_id="person-other")
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(
        mcp_module.httpx, "Client", lambda *a, **kw: StarletteTestClient(fastapi_app, headers=kw.get("headers"))
    )
    monkeypatch.setenv("KAIROS_API_KEY", key)
    return mcp_module


def test_mcp_get_context_and_search_never_cross_tenants(mcp_as_other):
    for repo in (None, "nimbus/orchestrator-core"):
        text = mcp_as_other.get_context(task="Add a /healthz endpoint", repo=repo)
        assert "Hana" not in text and "Orchestrator" not in text
        assert SECRET in text
        found = mcp_as_other.search(query="Kafka event backbone replay", repo=repo)
        assert "Kafka" not in found


@pytest.mark.parametrize(
    "layer,scope",
    [("team", "team-platform"), ("org", "org-1"), ("personal", "person-team-platform-0"), ("session", "session-0")],
)
def test_mcp_remember_into_another_tenant_is_refused(mcp_as_other, layer, scope):
    with pytest.raises(RuntimeError, match="403"):
        mcp_as_other.remember(content="poison", layer=layer, scope=scope)
