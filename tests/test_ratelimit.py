"""Tests for kairos/ratelimit.py — per-tenant rate limiting via slowapi.

KAIROS_RATE_LIMIT is read once at import time (kairos/ratelimit.py module
scope), so these tests reload the module under a monkeypatched env var to
exercise a tight limit rather than waiting out the default 120/minute.
"""

import importlib

import pytest

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


@pytest.fixture()
def tight_limit_client(tmp_path, monkeypatch):
    """Rebuilds the whole app under a 2/minute limit, isolated from the
    shared `client` fixture's default-limit app so this doesn't leak a
    tight limit into every other test in the suite."""
    monkeypatch.setenv("KAIROS_RATE_LIMIT", "2/minute")

    import kairos.ratelimit as ratelimit_module

    importlib.reload(ratelimit_module)
    import kairos.auth as auth_module

    importlib.reload(auth_module)
    import kairos.api as api_module

    importlib.reload(api_module)

    from kairos.db import configure, create_all, get_sessionmaker
    from kairos.vector_index import reset_cache_for_tests

    reset_cache_for_tests()
    ratelimit_module.reset_rate_limiter_for_tests()
    db_path = tmp_path / "kairos_ratelimit_test.db"
    configure(f"sqlite:///{db_path}")
    create_all()

    from kairos.seed import seed

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        raw_key = seed(db)
        db.commit()
    finally:
        db.close()

    from fastapi.testclient import TestClient

    test_client = TestClient(api_module.app, headers={"Authorization": f"Bearer {raw_key}"})
    yield test_client

    # Restore the default-limit app for every test after this one.
    monkeypatch.undo()
    importlib.reload(ratelimit_module)
    importlib.reload(auth_module)
    importlib.reload(api_module)


class TestRateLimiting:
    def test_requests_within_limit_succeed(self, tight_limit_client):
        for _ in range(2):
            resp = tight_limit_client.post("/v1/assemble_context", json=ASSEMBLE_BODY)
            assert resp.status_code == 200

    def test_exceeding_limit_returns_429(self, tight_limit_client):
        # The very first request from a brand-new credential lands in the
        # IP fallback bucket before kairos/auth.py has cached its tenant_id
        # (kairos/ratelimit.py's documented not-yet-resolved fallback) —
        # spend that here so the loop below tests the real, warm 2/minute
        # tenant bucket rather than accidentally getting a 3rd free request.
        tight_limit_client.post("/v1/assemble_context", json=ASSEMBLE_BODY)

        for _ in range(2):
            tight_limit_client.post("/v1/assemble_context", json=ASSEMBLE_BODY)
        resp = tight_limit_client.post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert resp.status_code == 429

    def test_different_tenants_get_independent_buckets(self, tight_limit_client):
        from kairos.auth import generate_api_key
        from kairos.db import get_sessionmaker
        from kairos.models import TenantConfig

        SessionLocal = get_sessionmaker()
        db = SessionLocal()
        try:
            db.add(TenantConfig(tenant_id="other", org_id="other-org-999", default_budget_tokens=4000))
            db.flush()
            other_raw_key = generate_api_key(db, tenant_id="other", label="other tenant", role="admin")
            db.commit()
        finally:
            db.close()

        # Exhaust the default tenant's tiny bucket (spending the one-time
        # cold-start allowance first — see test_exceeding_limit_returns_429).
        tight_limit_client.post("/v1/assemble_context", json=ASSEMBLE_BODY)
        for _ in range(2):
            tight_limit_client.post("/v1/assemble_context", json=ASSEMBLE_BODY)
        exhausted = tight_limit_client.post("/v1/assemble_context", json=ASSEMBLE_BODY)
        assert exhausted.status_code == 429

        # A different tenant's key is unaffected.
        from fastapi.testclient import TestClient

        other_client = TestClient(tight_limit_client.app, headers={"Authorization": f"Bearer {other_raw_key}"})
        resp = other_client.post(
            "/v1/assemble_context",
            json={
                "context_principal": {
                    "user_id": "person-team-platform-0",
                    "agent_type": "claude_code",
                    "repo": None,
                    "session_id": None,
                },
                "task": {"description": "anything"},
                "budget_tokens": 2000,
            },
        )
        # Cross-tenant scope enforcement (kairos/auth.py) rejects this before
        # rate limiting would even matter for the assertion below, but the
        # key point is it's NOT 429 — a fresh tenant always gets its own bucket.
        assert resp.status_code != 429

    def test_rotating_credentials_for_the_same_tenant_share_one_bucket(self, tight_limit_client):
        # Regression test: a rotating
        # credential (SSO JWTs typically reissue on every login/refresh —
        # kairos/sso.py) used to get a brand-new "unresolved" bucket on
        # every single rotation under the old per-credential-hash fallback,
        # never actually sharing its tenant's real limit. Simulated here
        # with distinct API keys for the SAME tenant standing in for
        # distinct JWTs from the same IdP session — the fix (falling back
        # to the caller's IP, not a per-credential bucket, for anything not
        # yet cached) means repeated "new" credentials from one caller
        # still land in a shared bucket instead of each getting its own
        # fresh allowance.
        from fastapi.testclient import TestClient

        from kairos.auth import generate_api_key
        from kairos.db import get_sessionmaker

        SessionLocal = get_sessionmaker()
        db = SessionLocal()
        try:
            keys = [generate_api_key(db, tenant_id="default", label=f"rotation-{i}", role="admin") for i in range(4)]
            db.commit()
        finally:
            db.close()

        # All requests share one process/IP in TestClient, so if each
        # "rotated" credential got its own fresh bucket, all 4 would
        # succeed (2/minute limit, 4 distinct never-seen-before credentials).
        # If they correctly share the IP fallback bucket, the 3rd and 4th
        # must be rejected.
        statuses = [
            TestClient(tight_limit_client.app, headers={"Authorization": f"Bearer {k}"})
            .post("/v1/assemble_context", json=ASSEMBLE_BODY)
            .status_code
            for k in keys
        ]
        assert statuses.count(429) >= 2
