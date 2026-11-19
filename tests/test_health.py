"""Tests for kairos/api.py's /healthz and /readyz — k8s liveness/readiness
probes. Both must work with no Authorization header (probes don't carry
credentials) and must not be subject to rate limiting (probes fire every
few seconds).
"""

from fastapi.testclient import TestClient

from kairos.api import app


def _unauthed_client() -> TestClient:
    return TestClient(app)


class TestHealthChecks:
    def test_healthz_requires_no_auth(self, client):
        resp = _unauthed_client().get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readyz_requires_no_auth_and_checks_db(self, client):
        resp = _unauthed_client().get("/readyz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_checks_are_exempt_from_rate_limiting(self, client):
        # Far more than the default limit — none of these should ever 429.
        for _ in range(30):
            assert _unauthed_client().get("/healthz").status_code == 200
            assert _unauthed_client().get("/readyz").status_code == 200
