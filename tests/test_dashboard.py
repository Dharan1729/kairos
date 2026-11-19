"""Tests for the observability dashboard (kairos/dashboard.py) —
the read side of data that was already being written to audit_ledger but
had nothing surfacing it."""


def _assemble(client, session_id="session-0", description="Add a /healthz endpoint"):
    return client.post(
        "/v1/assemble_context",
        json={
            "context_principal": {
                "user_id": "person-team-platform-0",
                "agent_type": "claude_code",
                "repo": None,
                "session_id": session_id,
            },
            "task": {"description": description},
            "budget_tokens": 4000,
        },
    )


class TestDashboardJson:
    def test_dashboard_reflects_requests_made(self, client):
        assert _assemble(client).status_code == 200
        assert _assemble(client).status_code == 200  # repeat -> assembly_hit

        resp = client.get("/v1/dashboard")
        assert resp.status_code == 200
        stats = resp.json()

        assert stats["total_requests"] == 2
        assert stats["outcome_counts"]["miss"] == 1
        assert stats["outcome_counts"]["hit"] == 1
        assert stats["cache_hit_rate"] == 0.5
        assert stats["tokens_used_total"] > 0

    def test_dashboard_requires_auth(self, client):
        from fastapi.testclient import TestClient

        from kairos.api import app

        resp = TestClient(app).get("/v1/dashboard")
        assert resp.status_code == 401

    def test_search_knowledge_calls_are_counted_too(self, client):
        client.post(
            "/v1/search_knowledge",
            json={
                "context_principal": {
                    "user_id": "person-team-platform-0",
                    "agent_type": "claude_code",
                    "repo": None,
                    "session_id": "session-0",
                },
                "query": "something",
                "budget_tokens": 2000,
            },
        )
        stats = client.get("/v1/dashboard").json()
        assert stats["total_requests"] == 1
        assert stats["outcome_counts"]["bypass"] == 1

    def test_search_knowledge_traffic_does_not_dilute_assembly_hit_rate(self, client):
        # Regression test: assembly_cache_hit_rate used to be computed over
        # ALL audit_ledger rows, but search_knowledge can never produce an
        # assembly_hit — mixing its rows in silently understated the rate
        # as search_knowledge volume grew. Two assemble_context calls
        # (cold_miss then assembly_hit -> 50%) plus three search_knowledge
        # calls (which must not count toward this metric's denominator).
        assert _assemble(client).status_code == 200
        assert _assemble(client).status_code == 200  # -> assembly_hit
        for _ in range(3):
            client.post(
                "/v1/search_knowledge",
                json={
                    "context_principal": {
                        "user_id": "person-team-platform-0",
                        "agent_type": "claude_code",
                        "repo": None,
                        "session_id": "session-0",
                    },
                    "query": "unrelated query",
                    "budget_tokens": 2000,
                },
            )

        stats = client.get("/v1/dashboard").json()
        assert stats["total_requests"] == 5
        assert stats["cache_hit_rate"] == 0.5  # still 1-of-2 assemble_context calls, not 1-of-5


class TestDashboardHtml:
    def test_html_view_with_query_param_key(self, client):
        # The fixture's key, from the client's default header.
        auth_header = client.headers.get("Authorization", "")
        assert auth_header.startswith("Bearer ")
        key = auth_header.removeprefix("Bearer ")

        _assemble(client)
        resp = client.get(f"/dashboard?api_key={key}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Kairos dashboard" in resp.text
        assert "requests" in resp.text

    def test_html_view_requires_some_key(self, client):
        from fastapi.testclient import TestClient

        from kairos.api import app

        resp = TestClient(app).get("/dashboard")
        assert resp.status_code == 401


class TestDashboardXssFix:
    """Regression tests for a stored-XSS finding: an earlier version of
    render_dashboard_html interpolated staleness_hint/layer values
    unescaped, and those fields accepted arbitrary strings. Two layers now
    guard this — input validation (Literal enums, tested here via the API)
    and output escaping (tested directly against render_dashboard_html, so
    the escaping itself is verified independent of whether some future
    field skips validation upstream)."""

    def test_invalid_layer_value_is_rejected_by_validation(self, client):
        resp = client.post(
            "/v1/knowledge_items",
            json={
                "layer": "<script>alert(1)</script>",
                "scope_id": "team-platform",
                "content": "x",
            },
        )
        assert resp.status_code == 422

    def test_invalid_staleness_hint_is_rejected_by_validation(self, client):
        resp = client.post(
            "/v1/knowledge_items",
            json={
                "layer": "team",
                "scope_id": "team-platform",
                "content": "x",
                "staleness_hint": "<img src=x onerror=alert(1)>",
            },
        )
        assert resp.status_code == 422

    def test_render_html_escapes_malicious_values_regardless(self):
        from kairos.dashboard import render_dashboard_html

        stats = {
            "tenant_id": "<script>alert('tenant')</script>",
            "generated_at": "2026-01-01T00:00:00Z",
            "total_requests": 1,
            "outcome_counts": {"miss": 1, "hit": 0},
            "cache_hit_rate": 0.0,
            "tokens_used_total": 10,
            "tokens_saved_total": 0,
            "avg_latency_ms": 5.0,
            "avg_context_size_tokens": 10.0,
            "most_reused_cache_entries": [],
            "knowledge_freshness": {
                "total_items": 1,
                "superseded_items": 0,
                "share_superseded": 0.0,
                "staleness_breakdown": {"<script>alert(1)</script>": 1},
            },
            "context_source_breakdown": {"<img src=x onerror=alert(2)>": 1.0},
        }
        html_out = render_dashboard_html(stats)
        assert "<script>alert('tenant')</script>" not in html_out
        assert "<script>alert(1)</script>" not in html_out
        assert "<img src=x onerror=alert(2)>" not in html_out
        assert "&lt;script&gt;" in html_out
