"""The pipeline end to end against the seeded demo org."""

import json

from kairos.seed import DATA_DIR

SESSIONS = {s["id"]: s for s in json.loads((DATA_DIR / "sessions.json").read_text())}


def assemble(client, user_id, session_id=None, description=None, budget_tokens=4000, repo=None, status=200):
    resp = client.post(
        "/v1/assemble_context",
        json={
            "context_principal": {"user_id": user_id, "agent_type": "test", "repo": repo, "session_id": session_id},
            "task": {"description": description or SESSIONS[session_id]["task_description"]},
            "budget_tokens": budget_tokens,
        },
    )
    assert resp.status_code == status, resp.text
    return resp.json()


def test_cold_miss_returns_every_layer_of_the_scope_chain(client):
    s0 = SESSIONS["session-0"]
    result = assemble(client, s0["person_id"], "session-0")
    assert result["cache"]["outcome"] == "miss" and result["cache"]["hit"] is False
    assert {"org", "team", "project", "personal", "session"} <= {i["layer"] for i in result["context_bundle"]}
    assert [r["layer"] for r in result["scope"]["layers"]] == ["org", "team", "project", "personal", "session"]
    assert 0 < result["tokens_used"] <= 4000


def test_repeated_request_is_a_hit_with_identical_bundle(client):
    s0 = SESSIONS["session-0"]
    first = assemble(client, s0["person_id"], "session-0")
    second = assemble(client, s0["person_id"], "session-0")
    assert second["cache"]["outcome"] == "hit" and second["cache"]["hit"] is True
    assert second["context_bundle"] == first["context_bundle"]


def test_paraphrase_by_same_user_is_a_hit_only_above_threshold(client):
    s0 = SESSIONS["session-0"]
    assemble(client, s0["person_id"], "session-0", description="Add a /healthz endpoint to a new internal service")
    near = assemble(
        client, s0["person_id"], "session-0", description="Add a /healthz endpoint to the new internal service"
    )
    far = assemble(client, s0["person_id"], "session-0", description="Rotate the Kafka TLS certificates")
    assert near["cache"]["outcome"] == "hit"
    assert far["cache"]["outcome"] == "miss"


def test_different_user_same_project_does_not_reuse_by_default(client):
    s0, s8 = SESSIONS["session-0"], SESSIONS["session-8"]
    assert s0["project_id"] == s8["project_id"] and s0["person_id"] != s8["person_id"]
    assemble(client, s0["person_id"], "session-0")
    other = assemble(client, s8["person_id"], "session-8", description=s0["task_description"])
    assert other["cache"]["outcome"] == "miss"


def test_budget_is_respected_and_drops_are_reported(client):
    s0 = SESSIONS["session-0"]
    result = assemble(client, s0["person_id"], "session-0", budget_tokens=30)
    assert result["tokens_used"] <= 30
    assert result["dropped"] and all(d["count"] > 0 and d["reason"] for d in result["dropped"])


def test_budget_is_part_of_the_cache_key(client):
    s0 = SESSIONS["session-0"]
    big = assemble(client, s0["person_id"], "session-0", budget_tokens=400)
    small = assemble(client, s0["person_id"], "session-0", budget_tokens=60)
    assert small["cache"]["outcome"] == "miss"
    assert small["tokens_used"] <= 60 < big["tokens_used"]


def test_out_of_range_budget_is_rejected(client):
    assemble(client, "person-team-platform-0", description="x", budget_tokens=0, status=422)
    assemble(client, "person-team-platform-0", description="x", budget_tokens=10_000_000, status=422)


def test_approved_proposal_invalidates_cached_bundles(client):
    s0 = SESSIONS["session-0"]
    assemble(client, s0["person_id"], "session-0")
    cid = client.post(
        "/v1/candidates",
        json={
            "proposed_content": "All cron jobs must emit a heartbeat metric.",
            "proposed_layer": "team",
            "proposed_scope_id": "team-platform",
        },
    ).json()["id"]
    approved = client.post(f"/v1/candidates/{cid}/approve", json={"reviewed_by": "lead"})
    assert approved.status_code == 200 and approved.json()["promoted_item_id"]
    after = assemble(client, s0["person_id"], "session-0")
    assert after["cache"]["outcome"] == "miss"


def test_approving_twice_is_a_conflict(client):
    cid = client.post(
        "/v1/candidates",
        json={"proposed_content": "Some fact.", "proposed_layer": "team", "proposed_scope_id": "team-platform"},
    ).json()["id"]
    assert client.post(f"/v1/candidates/{cid}/approve", json={"reviewed_by": "a"}).status_code == 200
    assert client.post(f"/v1/candidates/{cid}/approve", json={"reviewed_by": "b"}).status_code == 409


def test_cache_can_be_disabled(client, monkeypatch):
    monkeypatch.setenv("KAIROS_CACHE_ENABLED", "false")
    s0 = SESSIONS["session-0"]
    assemble(client, s0["person_id"], "session-0")
    assert assemble(client, s0["person_id"], "session-0")["cache"]["outcome"] == "disabled"
