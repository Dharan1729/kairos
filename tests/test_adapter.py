"""Regression tests for the adapter's fail-open exception handling
(kairos/adapters/claude_code.py). Pins the correct, severity-based
behavior: network errors and 5xx degrade gracefully (a real Kairos
outage); ANY 4xx (401/403/400/404/422/...) propagates, since those
indicate a bug in the request itself, not an outage — an earlier version
only propagated 401/403 and silently degraded other 4xx too, which would
mask a real adapter bug (e.g. a malformed request body) as a routine
"Kairos is down" case.
"""

from unittest.mock import MagicMock

import httpx
import pytest

from kairos.adapters.claude_code import ContextProvider


def _fake_response(status_code: int, json_data: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code} error", request=MagicMock(), response=resp
        )
    return resp


class TestFetchContextFailOpen:
    def test_success_returns_response_json(self, monkeypatch):
        payload = {
            "context_bundle": [{"layer": "org", "content": "x", "source": "y"}],
            "cache": {"hit": False},
            "tokens_used": 1,
            "dropped": [],
        }
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_response(200, payload))
        result = ContextProvider(api_key="k").fetch_context(
            user_id="u", repo=None, session_id=None, task_description="x"
        )
        assert result == payload

    def test_server_error_degrades_to_empty_context(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_response(500))
        result = ContextProvider(api_key="k").fetch_context(
            user_id="u", repo=None, session_id=None, task_description="x"
        )
        assert result["context_bundle"] == []

    def test_service_unavailable_degrades(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_response(503))
        result = ContextProvider(api_key="k").fetch_context(
            user_id="u", repo=None, session_id=None, task_description="x"
        )
        assert result["context_bundle"] == []

    def test_connection_error_degrades(self, monkeypatch):
        def raise_connect_error(*a, **k):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "post", raise_connect_error)
        result = ContextProvider(api_key="k").fetch_context(
            user_id="u", repo=None, session_id=None, task_description="x"
        )
        assert result["context_bundle"] == []

    def test_unauthorized_propagates_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_response(401))
        with pytest.raises(httpx.HTTPStatusError):
            ContextProvider(api_key="bad-key").fetch_context(
                user_id="u", repo=None, session_id=None, task_description="x"
            )

    def test_forbidden_propagates_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_response(403))
        with pytest.raises(httpx.HTTPStatusError):
            ContextProvider(api_key="wrong-tenant-key").fetch_context(
                user_id="u", repo=None, session_id=None, task_description="x"
            )

    def test_bad_request_propagates_not_swallowed(self, monkeypatch):
        # A malformed request body is an adapter bug, not a Kairos outage —
        # must surface, not silently degrade to empty context.
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_response(400))
        with pytest.raises(httpx.HTTPStatusError):
            ContextProvider(api_key="k").fetch_context(user_id="u", repo=None, session_id=None, task_description="x")

    def test_validation_error_propagates_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_response(422))
        with pytest.raises(httpx.HTTPStatusError):
            ContextProvider(api_key="k").fetch_context(user_id="u", repo=None, session_id=None, task_description="x")


class TestSearchFailOpen:
    def test_server_error_degrades_to_empty_results(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_response(500))
        result = ContextProvider(api_key="k").search(user_id="u", repo=None, session_id=None, query="q")
        assert result["results"] == []

    def test_unauthorized_propagates(self, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_response(401))
        with pytest.raises(httpx.HTTPStatusError):
            ContextProvider(api_key="bad-key").search(user_id="u", repo=None, session_id=None, query="q")
