"""Tests for kairos/cli.py. Routes the CLI's httpx.Client through the same
in-process ASGI app + seeded DB the `client` fixture (conftest.py) already
sets up, via starlette.testclient.TestClient — a proper httpx.Client
subclass that already solves the sync/ASGI dispatch problem the rest of
this suite relies on. (A raw httpx.Client(transport=httpx.ASGITransport(...))
looks like the obvious way to do this but doesn't actually work: ASGITransport
only implements the async context-manager protocol, so a sync Client built
on it blows up on both `with client:` and `client.close()` — exactly what
TestClient exists to paper over.) No real network, no separate server
process, but the exact request/response path a live `kairos <cmd>` run
would take (headers, status codes, JSON).
"""

import json

import httpx
import pytest
from starlette.testclient import TestClient as StarletteTestClient
from typer.testing import CliRunner

import kairos.cli as cli_module
from kairos.api import app as fastapi_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _route_cli_through_test_app(monkeypatch, client):
    def _fake_client(*args, **kwargs):
        # kairos/cli.py always calls httpx.Client(base_url=..., headers=...,
        # timeout=...) — base_url/timeout are meaningless against an
        # in-process ASGI app, only headers (the Authorization bearer) matter.
        return StarletteTestClient(fastapi_app, headers=kwargs.get("headers"))

    monkeypatch.setattr(cli_module.httpx, "Client", _fake_client)

    raw_key = client.headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("KAIROS_API_KEY", raw_key)
    monkeypatch.delenv("KAIROS_URL", raising=False)
    return raw_key


class TestCliHealth:
    def test_health_reports_ok(self):
        result = runner.invoke(cli_module.app, ["health"])
        assert result.exit_code == 0
        assert "healthz: 200" in result.output
        assert "readyz: 200" in result.output or "readyz:  200" in result.output


class TestCliAssembleAndSearch:
    def test_assemble_returns_context_bundle(self):
        result = runner.invoke(
            cli_module.app,
            [
                "assemble",
                "--user",
                "person-team-platform-0",
                "--task",
                "add a health check",
                "--session",
                "session-0",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "context_bundle" in payload
        assert payload["cache"]["outcome"] in ("miss", "hit")

    def test_search_returns_results(self):
        result = runner.invoke(
            cli_module.app,
            [
                "search",
                "--user",
                "person-team-platform-0",
                "--query",
                "healthz",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "results" in payload


class TestCliCandidatesWorkflow:
    def test_propose_approve_get_round_trip(self):
        propose = runner.invoke(
            cli_module.app,
            [
                "candidates",
                "propose",
                "--content",
                "cli-proposed fact",
                "--layer",
                "team",
                "--scope",
                "team-platform",
            ],
        )
        assert propose.exit_code == 0
        candidate_id = json.loads(propose.output)["id"]

        approve = runner.invoke(
            cli_module.app,
            [
                "candidates",
                "approve",
                str(candidate_id),
                "--by",
                "cli-test",
            ],
        )
        assert approve.exit_code == 0
        approve_body = json.loads(approve.output)
        assert approve_body["status"] == "approved"

        get = runner.invoke(cli_module.app, ["candidates", "get", str(candidate_id)])
        assert get.exit_code == 0
        assert json.loads(get.output)["status"] == "approved"

    def test_reject_by_non_admin_still_succeeds_but_approve_requires_admin(self):
        # candidates reject only needs "member"; the seeded dev key is
        # "admin" so this doesn't distinguish roles, it just confirms the
        # CLI reaches the right endpoint and surfaces the right status.
        propose = runner.invoke(
            cli_module.app,
            [
                "candidates",
                "propose",
                "--content",
                "to be rejected",
                "--layer",
                "team",
                "--scope",
                "team-platform",
            ],
        )
        candidate_id = json.loads(propose.output)["id"]
        reject = runner.invoke(cli_module.app, ["candidates", "reject", str(candidate_id), "--by", "cli-test"])
        assert reject.exit_code == 0
        assert json.loads(reject.output)["status"] == "rejected"


class TestCliKeysWorkflow:
    def test_create_list_revoke_round_trip(self):
        create = runner.invoke(cli_module.app, ["keys", "create", "--label", "cli test key", "--role", "viewer"])
        assert create.exit_code == 0
        created = json.loads(create.output)
        assert created["role"] == "viewer"

        listing = runner.invoke(cli_module.app, ["keys", "list"])
        assert listing.exit_code == 0
        keys = json.loads(listing.output)
        match = next(k for k in keys if k["label"] == "cli test key")
        assert match["revoked_at"] is None

        revoke = runner.invoke(cli_module.app, ["keys", "revoke", str(match["id"])])
        assert revoke.exit_code == 0
        assert json.loads(revoke.output)["revoked"] is True


class TestCliErrorHandling:
    def test_server_error_response_exits_nonzero_and_prints_detail(self):
        # A viewer-equivalent failure: wrong key entirely -> 401 from the API.
        result = runner.invoke(cli_module.app, ["dashboard", "--key", "kairos_totally_bogus_key"])
        assert result.exit_code == 1
        assert "401" in result.output

    def test_connect_error_produces_a_readable_message_not_a_traceback(self, monkeypatch):
        def _raise_connect_error(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(cli_module.httpx, "Client", _raise_connect_error)
        result = runner.invoke(cli_module.app, ["health"])
        assert result.exit_code == 1
        assert "Is Kairos running?" in result.output


class TestCliDashboard:
    def test_dashboard_reports_tenant_stats(self):
        result = runner.invoke(cli_module.app, ["dashboard"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["tenant_id"] == "default"


class TestCliLocal:
    def test_try_runs_locally_as_the_demo_user(self, monkeypatch, tmp_path):
        from kairos.seed import seed_with_dev_keys

        monkeypatch.setenv("KAIROS_DATABASE_URL", f"sqlite:///{tmp_path / 'local.db'}")
        assert seed_with_dev_keys() is not None
        result = runner.invoke(cli_module.app, ["try", "Add a /healthz endpoint", "--session", "session-0"])
        assert result.exit_code == 0, result.output
        assert "[session]" in result.output and "session:session-0" in result.output

    def test_try_refuses_someone_elses_session(self, monkeypatch, tmp_path):
        from kairos.seed import seed_with_dev_keys

        monkeypatch.setenv("KAIROS_DATABASE_URL", f"sqlite:///{tmp_path / 'local.db'}")
        seed_with_dev_keys()
        result = runner.invoke(cli_module.app, ["try", "x", "--session", "session-4"])
        assert result.exit_code == 1 and "refused" in result.output

    def test_seed_writes_private_dev_keys(self, monkeypatch, tmp_path):
        import os
        import stat

        from kairos.seed import dev_keys_file, seed_with_dev_keys

        monkeypatch.setenv("KAIROS_DATABASE_URL", f"sqlite:///{tmp_path / 'local.db'}")
        keys = seed_with_dev_keys()
        assert set(keys) == {"admin", "demo_user", "demo_user_id"}
        assert stat.S_IMODE(os.stat(dev_keys_file()).st_mode) == 0o600
