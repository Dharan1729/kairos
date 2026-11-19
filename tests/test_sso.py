"""Tests for kairos/sso.py — JWT/JWKS-validated SSO auth as a second path
alongside static API keys (kairos/auth.py's _resolve_bearer_credential).

Uses a locally generated RSA keypair and TenantConfig.sso_jwks_static (a
pinned JWKS blob) instead of a live IdP — that's the whole point of the
static-JWKS option: SSO is fully testable without a running identity
provider, per kairos/sso.py's docstring.
"""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from kairos.api import app
from kairos.models import TenantConfig
from kairos.sso import reset_jwks_cache_for_tests

ISSUER = "https://idp.example.com/"
AUDIENCE = "kairos-api"
KID = "test-signing-key-1"

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


@pytest.fixture(autouse=True)
def _reset():
    reset_jwks_cache_for_tests()
    yield
    reset_jwks_cache_for_tests()


@pytest.fixture()
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = KID
    jwk["alg"] = "RS256"
    jwks = {"keys": [jwk]}
    return private_key, jwks


def _configure_sso(
    db,
    tenant_id="default",
    jwks=None,
    issuer=ISSUER,
    audience=AUDIENCE,
    role_claim="kairos_role",
    default_role="viewer",
):
    tenant = db.get(TenantConfig, tenant_id)
    tenant.sso_issuer = issuer
    tenant.sso_audience = audience
    tenant.sso_jwks_static = json.dumps(jwks)
    tenant.sso_role_claim = role_claim
    tenant.sso_default_role = default_role
    db.commit()


def _sign(private_key, claims: dict, kid: str = KID) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _client_with_token(token: str) -> TestClient:
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "person-team-platform-0", "iat": now, "exp": now + 300}
    claims.update(overrides)
    return claims


class TestSsoAuthentication:
    def test_token_is_bound_to_its_subject(self, client, db_session, keypair):
        private_key, jwks = keypair
        _configure_sso(db_session, jwks=jwks)
        token = _sign(private_key, _base_claims(kairos_role="admin"))
        other_user = {**ASSEMBLE_BODY, "context_principal": {"user_id": "person-team-platform-mgr"}}
        assert _client_with_token(token).post("/v1/assemble_context", json=other_user).status_code == 403
        own = _client_with_token(token).post("/v1/assemble_context", json={"task": {"description": "healthz"}})
        assert own.json()["scope"]["layers"][-1]["scope_id"] == "person-team-platform-0"

    def test_valid_jwt_authenticates_as_configured_role(self, client, db_session, keypair):
        private_key, jwks = keypair
        _configure_sso(db_session, jwks=jwks)
        token = _sign(private_key, _base_claims(kairos_role="admin"))

        resp = _client_with_token(token).post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 200

    def test_missing_role_claim_falls_back_to_tenant_default_role(self, client, db_session, keypair):
        private_key, jwks = keypair
        _configure_sso(db_session, jwks=jwks, default_role="viewer")
        token = _sign(private_key, _base_claims())  # no kairos_role claim

        read = _client_with_token(token).post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert read.status_code == 200

        write = _client_with_token(token).post(
            "/v1/knowledge_items",
            json={
                "layer": "team",
                "scope_id": "team-platform",
                "content": "x",
            },
        )
        assert write.status_code == 403  # default role is "viewer" -> read-only

    def test_role_claim_enforces_rbac_same_as_api_keys(self, client, db_session, keypair):
        private_key, jwks = keypair
        _configure_sso(db_session, jwks=jwks)
        member_token = _sign(private_key, _base_claims(kairos_role="member"))

        propose = _client_with_token(member_token).post(
            "/v1/candidates",
            json={
                "proposed_content": "sso-proposed",
                "proposed_layer": "team",
                "proposed_scope_id": "team-platform",
            },
        )
        assert propose.status_code == 200
        candidate_id = propose.json()["id"]

        approve = _client_with_token(member_token).post(
            f"/v1/candidates/{candidate_id}/approve", json={"reviewed_by": "sso-member"}
        )
        assert approve.status_code == 403  # member, not admin

    def test_expired_token_rejected(self, client, db_session, keypair):
        private_key, jwks = keypair
        _configure_sso(db_session, jwks=jwks)
        now = int(time.time())
        token = _sign(private_key, _base_claims(iat=now - 1000, exp=now - 100))

        resp = _client_with_token(token).post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 401

    def test_wrong_audience_rejected(self, client, db_session, keypair):
        private_key, jwks = keypair
        _configure_sso(db_session, jwks=jwks)
        token = _sign(private_key, _base_claims(aud="some-other-service"))

        resp = _client_with_token(token).post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 401

    def test_unrecognized_issuer_rejected(self, client, db_session, keypair):
        private_key, jwks = keypair
        _configure_sso(db_session, jwks=jwks)
        token = _sign(private_key, _base_claims(iss="https://not-configured.example.com/"))

        resp = _client_with_token(token).post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 401

    def test_signature_from_wrong_key_rejected(self, client, db_session, keypair):
        _, jwks = keypair  # tenant trusts this JWKS...
        _configure_sso(db_session, jwks=jwks)
        forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        # ...but the token is signed with an unrelated key claiming the same kid.
        token = _sign(forged_key, _base_claims())

        resp = _client_with_token(token).post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 401

    def test_jwt_cannot_cross_tenant_boundary(self, client, db_session, keypair):
        # An SSO token authenticates to whichever tenant configured that
        # issuer — org/scope enforcement downstream (check_scope_belongs_to_tenant)
        # applies identically to JWT and API-key auth, since both produce
        # the same AuthContext.
        private_key, jwks = keypair
        db_session.add(TenantConfig(tenant_id="other", org_id="other-org-999", default_budget_tokens=4000))
        db_session.flush()
        _configure_sso(db_session, tenant_id="other", jwks=jwks, default_role="admin")

        token = _sign(private_key, _base_claims())
        resp = _client_with_token(token).post("/v1/assemble_context", json=ASSEMBLE_BODY)
        # Authenticates fine (as tenant "other"), but org-1 (seeded under
        # "default") isn't that tenant's org -> tenant-boundary 403, same as
        # the existing API-key cross-tenant tests.
        assert resp.status_code == 403

    def test_non_jwt_non_kairos_bearer_token_rejected(self, client):
        resp = _client_with_token("not-a-jwt-and-not-a-kairos-key").post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 401

    def test_api_key_auth_still_works_alongside_sso(self, client):
        # Sanity check: adding the JWT path didn't break the existing,
        # far more common API-key path.
        resp = client.post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 200
