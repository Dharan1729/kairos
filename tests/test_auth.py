"""Authentication and role checks. Cross-tenant and cross-scope access
attempts are in test_tenant_isolation.py and test_cache_safety.py."""

from datetime import UTC

from fastapi.testclient import TestClient

from kairos.api import app


def _unauthed_client() -> TestClient:
    return TestClient(app)  # no default headers — reuses the DB `client` already configured


def _client_with_key(raw_key: str) -> TestClient:
    return TestClient(app, headers={"Authorization": f"Bearer {raw_key}"})


ASSEMBLE_BODY = {
    "context_principal": {
        "user_id": "person-team-platform-0",
        "agent_type": "claude_code",
        "repo": None,
        "session_id": "session-0",
    },
    "task": {"description": "Add a /healthz endpoint to a new internal service"},
    "budget_tokens": 4000,
}

# For unbound non-admin keys, which act as nobody and may not name a session.
SERVICE_BODY = {"task": {"description": "Add a /healthz endpoint to a new internal service"}}


class TestAuthentication:
    def test_missing_auth_header_returns_401(self, client):
        resp = _unauthed_client().post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 401

    def test_invalid_api_key_returns_401(self, client):
        resp = _client_with_key("not-a-real-key").post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 401

    def test_revoked_key_returns_401(self, client, db_session):
        from datetime import datetime

        from kairos.auth import generate_api_key
        from kairos.models import ApiKey

        db = db_session
        raw = generate_api_key(db, tenant_id="default", label="to be revoked")
        db.commit()
        row = db.query(ApiKey).filter(ApiKey.label == "to be revoked").one()
        row.revoked_at = datetime.now(UTC)
        db.commit()

        resp = _client_with_key(raw).post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 401

    def test_valid_key_still_works(self, client):
        # Sanity check: the fixture's own key, which every other test in the
        # suite relies on, actually authenticates successfully.
        resp = client.post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 200


def _same_tenant_key(db, role: str, tenant_id: str = "default"):
    from kairos.auth import generate_api_key

    raw = generate_api_key(db, tenant_id=tenant_id, label=f"rbac-{role}", role=role)
    db.commit()
    return raw


class TestRBAC:
    """viewer < member < admin. The `client` fixture is an admin key, so these
    tests mint narrower keys to exercise the gates."""

    def test_viewer_can_read_but_not_write(self, client, db_session):
        viewer_client = _client_with_key(_same_tenant_key(db_session, "viewer"))

        assert viewer_client.post("/v1/assemble_context", json=SERVICE_BODY).status_code == 200

        resp = viewer_client.post(
            "/v1/knowledge_items",
            json={
                "layer": "team",
                "scope_id": "team-platform",
                "content": "x",
            },
        )
        assert resp.status_code == 403

    def test_member_can_propose_and_reject_but_not_approve(self, client, db_session):
        member_client = _client_with_key(_same_tenant_key(db_session, "member"))

        propose = member_client.post(
            "/v1/candidates",
            json={
                "proposed_content": "member-proposed",
                "proposed_layer": "team",
                "proposed_scope_id": "team-platform",
            },
        )
        assert propose.status_code == 200
        candidate_id = propose.json()["id"]

        approve = member_client.post(f"/v1/candidates/{candidate_id}/approve", json={"reviewed_by": "member"})
        assert approve.status_code == 403

        reject = member_client.post(f"/v1/candidates/{candidate_id}/reject", json={"reviewed_by": "member"})
        assert reject.status_code == 200

    def test_admin_can_approve(self, client):
        propose = client.post(
            "/v1/candidates",
            json={
                "proposed_content": "admin-approved",
                "proposed_layer": "team",
                "proposed_scope_id": "team-platform",
            },
        )
        candidate_id = propose.json()["id"]
        approve = client.post(f"/v1/candidates/{candidate_id}/approve", json={"reviewed_by": "admin"})
        assert approve.status_code == 200

    def test_non_admin_cannot_manage_api_keys(self, client, db_session):
        member_client = _client_with_key(_same_tenant_key(db_session, "member"))
        resp = member_client.post("/v1/api_keys", json={"label": "x", "role": "viewer"})
        assert resp.status_code == 403
        assert member_client.get("/v1/api_keys").status_code == 403

    def test_admin_can_create_list_and_revoke_a_key(self, client):
        create = client.post("/v1/api_keys", json={"label": "ci bot", "role": "viewer"})
        assert create.status_code == 200
        new_raw_key = create.json()["api_key"]

        listed = client.get("/v1/api_keys").json()
        key_id = next(k["id"] for k in listed if k["label"] == "ci bot")

        # The newly minted key actually authenticates.
        assert _client_with_key(new_raw_key).post("/v1/assemble_context", json=SERVICE_BODY).status_code == 200

        assert client.post(f"/v1/api_keys/{key_id}/revoke").status_code == 200
        assert _client_with_key(new_raw_key).post("/v1/assemble_context", json=SERVICE_BODY).status_code == 401

    def test_admin_cannot_revoke_another_tenants_key_returns_404(self, client, db_session):
        # An admin naming another tenant's key id cannot tell whether it exists.
        from kairos.auth import hash_key
        from kairos.models import ApiKey, TenantConfig

        db_session.add(TenantConfig(tenant_id="other-rbac", org_id="other-rbac-org", default_budget_tokens=4000))
        db_session.flush()
        foreign_raw_key = _same_tenant_key(db_session, "admin", tenant_id="other-rbac")
        foreign_key_id = db_session.query(ApiKey).filter(ApiKey.key_hash == hash_key(foreign_raw_key)).one().id

        resp = client.post(f"/v1/api_keys/{foreign_key_id}/revoke")
        assert resp.status_code == 404


class TestPersonBinding:
    def test_admin_can_create_a_person_bound_key(self, client):
        resp = client.post("/v1/api_keys", json={"label": "a", "role": "member", "person_id": "person-team-platform-0"})
        assert resp.status_code == 200
        bound = _client_with_key(resp.json()["api_key"])
        body = bound.post("/v1/assemble_context", json={"task": {"description": "healthz"}}).json()
        assert body["scope"]["layers"][-1] == {"layer": "personal", "scope_id": "person-team-platform-0"}

    def test_cannot_bind_a_key_to_an_unknown_or_foreign_person(self, client, second_tenant):
        assert client.post("/v1/api_keys", json={"person_id": "nobody"}).status_code == 403
        assert client.post("/v1/api_keys", json={"person_id": "person-other"}).status_code == 403

    def test_unknown_role_rejected(self, client):
        assert client.post("/v1/api_keys", json={"role": "owner"}).status_code == 422
