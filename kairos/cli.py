"""The ``kairos`` command.

Two kinds of commands:

- Local commands work on the database directly, with no server:
  ``quickstart``, ``serve``, ``seed``, ``try``, ``watch``, ``reembed``.
  ``try`` still goes through the same core function and access checks as
  the API, as the demo user.
- Client commands talk to a running server over HTTP with a credential, so
  they behave exactly like any other API client: ``health``, ``assemble``,
  ``search``, ``add-knowledge``, ``revise``, ``extract-git``, ``dashboard``,
  ``candidates ...``, ``keys ...``.
"""

from __future__ import annotations

import json
import os
import sys

import httpx
import typer

import kairos

app = typer.Typer(help="Kairos: context assembly for coding agents.", no_args_is_help=True)
candidates_app = typer.Typer(help="Review proposed knowledge.", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys (admin).", no_args_is_help=True)
app.add_typer(candidates_app, name="candidates")
app.add_typer(keys_app, name="keys")

DEFAULT_URL = "http://127.0.0.1:8000"

KeyOption = typer.Option(
    None,
    "--key",
    envvar="KAIROS_API_KEY",
    help="API key or SSO token. Falls back to the admin key in $KAIROS_HOME/dev_keys.json.",
)


def _base_url() -> str:
    return os.environ.get("KAIROS_URL", DEFAULT_URL)


def _resolve_key(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    from kairos.seed import load_dev_key

    return load_dev_key("admin")


def _fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _request(method: str, path: str, key: str | None = None, **kwargs) -> None:
    resolved = _resolve_key(key)
    headers = {"Authorization": f"Bearer {resolved}"} if resolved else {}
    try:
        with httpx.Client(base_url=_base_url(), headers=headers, timeout=60.0) as client:
            resp = client.request(method, path, **kwargs)
    except httpx.ConnectError:
        _fail(f"couldn't reach {_base_url()}. Is Kairos running? (kairos serve)")
    if resp.status_code >= 400:
        _fail(f"{resp.status_code}: {resp.text}")
    typer.echo(json.dumps(resp.json(), indent=2))


def _principal(user: str | None, agent_type: str, repo: str | None, session: str | None) -> dict:
    return {"user_id": user, "agent_type": agent_type, "repo": repo, "session_id": session}


# ---- local commands -------------------------------------------------------


@app.command()
def version():
    """Print the version."""
    typer.echo(kairos.__version__)


@app.command()
def seed():
    """Create tables, load the demo org, write dev keys to $KAIROS_HOME."""
    from kairos.seed import main as seed_main

    seed_main()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address. Keep 127.0.0.1 unless you put TLS in front."),
    port: int = typer.Option(8000),
):
    """Run the HTTP API (creates tables if needed)."""
    import uvicorn

    from kairos.db import configure, create_all

    configure()
    create_all()
    uvicorn.run("kairos.api:app", host=host, port=port)


@app.command()
def quickstart(port: int = typer.Option(8000), no_serve: bool = typer.Option(False, "--no-serve")):
    """One command: seed the demo org into a local SQLite database, show a
    sample bundle, then serve the API on 127.0.0.1."""
    from kairos.seed import dev_keys_file, load_dev_key, seed_with_dev_keys

    typer.echo("Loading the embedding model (downloads about 90 MB from Hugging Face on first run)...")
    keys = seed_with_dev_keys()
    if keys is None:
        typer.echo(f"Already seeded; using keys in {dev_keys_file()}.")
    else:
        typer.echo(f"Seeded the demo org. Keys written to {dev_keys_file()}.")
    _try("Add a /healthz endpoint to a new internal service", session="session-0", repo=None, budget=600)
    user_key = load_dev_key("demo_user")
    typer.echo("\nNext:")
    typer.echo(f"  export KAIROS_URL=http://127.0.0.1:{port}")
    typer.echo(f"  export KAIROS_API_KEY={user_key}   # bound to the demo user")
    typer.echo('  kairos assemble --task "Set up Terraform for a new service" --budget 500')
    typer.echo("  MCP setup for Claude Code / Cursor: docs/guide/mcp.md")
    if not no_serve:
        serve(host="127.0.0.1", port=port)


def _try(task: str, session: str | None, repo: str | None, budget: int, user: str | None = None) -> None:
    from kairos.access import AccessDenied, Caller, InvalidRequest
    from kairos.db import configure, create_all, get_sessionmaker
    from kairos.models import TenantConfig
    from kairos.pipeline import ContextPrincipal, ContextService, Task
    from kairos.seed import DEMO_PERSON_ID, DEMO_TENANT_ID

    configure()
    create_all()
    db = get_sessionmaker()()
    try:
        tenant = db.get(TenantConfig, DEMO_TENANT_ID)
        if tenant is None:
            _fail("no demo tenant; run `kairos seed` first")
        person = user or DEMO_PERSON_ID
        caller = Caller(tenant_id=tenant.tenant_id, org_id=tenant.org_id, role="member", person_id=person)
        try:
            result = ContextService.from_settings().assemble(
                db,
                caller,
                ContextPrincipal(user_id=person, agent_type="cli", repo=repo, session_id=session),
                Task(task),
                budget,
            )
        except (AccessDenied, InvalidRequest) as e:
            _fail(f"refused: {e}")
    finally:
        db.close()
    typer.echo(
        f"\nTask: {task}\nAs: {person}   scope: " + " > ".join(f"{r.layer}:{r.scope_id}" for r in result.scope.chain)
    )
    for note in result.scope.notes:
        typer.echo(f"  note: {note}")
    for item in result.bundle:
        typer.echo(f"  [{item.layer}] {item.content}")
    typer.echo(
        f"{result.tokens_used}/{budget} tokens, cache {result.cache.outcome}, "
        f"{result.latency_ms:.0f} ms, dropped {result.dropped or 'nothing'}"
    )


@app.command("try")
def try_(
    task: str = typer.Argument(..., help="Task description"),
    user: str = typer.Option(None, "--user", help="Demo person id (default: the demo user)"),
    session: str = typer.Option(None, "--session"),
    repo: str = typer.Option(None, "--repo"),
    budget: int = typer.Option(600, "--budget"),
):
    """Assemble context locally against the seeded demo org, no server."""
    _try(task, session=session, repo=repo, budget=budget, user=user)


@app.command()
def watch(
    repo_path: str,
    scope_id: str,
    layer: str = typer.Option("project", "--layer"),
    debounce_ms: int = typer.Option(2000, "--debounce-ms"),
):
    """Re-extract git commits into a scope whenever the repo changes."""
    from kairos.watch import main as watch_main

    sys.argv = ["kairos-watch", repo_path, scope_id, "--layer", layer, "--debounce-ms", str(debounce_ms)]
    watch_main()


@app.command()
def reembed():
    """Recompute every item's embedding with the current embedder (after
    changing KAIROS_EMBEDDER or KAIROS_EMBEDDING_MODEL)."""
    from kairos.db import configure, get_sessionmaker
    from kairos.embeddings import embed, to_json
    from kairos.models import CacheEntry, KnowledgeItem

    configure()
    db = get_sessionmaker()()
    try:
        items = db.query(KnowledgeItem).all()
        for item in items:
            item.embedding_ref = to_json(embed(item.content))
        db.query(CacheEntry).delete()
        db.commit()
        typer.echo(f"re-embedded {len(items)} items and cleared the bundle cache")
    finally:
        db.close()


# ---- client commands ------------------------------------------------------


@app.command()
def health():
    """GET /healthz and /readyz (no credential needed)."""
    try:
        with httpx.Client(base_url=_base_url(), timeout=10.0) as client:
            healthz, readyz = client.get("/healthz"), client.get("/readyz")
    except httpx.ConnectError:
        _fail(f"couldn't reach {_base_url()}. Is Kairos running?")
    typer.echo(f"healthz: {healthz.status_code} {healthz.json()}")
    typer.echo(f"readyz:  {readyz.status_code} {readyz.json()}")


@app.command()
def assemble(
    task: str = typer.Option(..., "--task"),
    user: str = typer.Option(None, "--user", help="Defaults to the key's own user"),
    agent_type: str = typer.Option("cli", "--agent-type"),
    session: str = typer.Option(None, "--session"),
    repo: str = typer.Option(None, "--repo"),
    budget: int = typer.Option(4000, "--budget"),
    key: str = KeyOption,
):
    """POST /v1/assemble_context."""
    _request(
        "POST",
        "/v1/assemble_context",
        key=key,
        json={
            "context_principal": _principal(user, agent_type, repo, session),
            "task": {"description": task},
            "budget_tokens": budget,
        },
    )


@app.command()
def search(
    query: str = typer.Option(..., "--query"),
    user: str = typer.Option(None, "--user"),
    agent_type: str = typer.Option("cli", "--agent-type"),
    session: str = typer.Option(None, "--session"),
    repo: str = typer.Option(None, "--repo"),
    budget: int = typer.Option(2000, "--budget"),
    key: str = KeyOption,
):
    """POST /v1/search_knowledge (not cached)."""
    _request(
        "POST",
        "/v1/search_knowledge",
        key=key,
        json={
            "context_principal": _principal(user, agent_type, repo, session),
            "query": query,
            "budget_tokens": budget,
        },
    )


@app.command("add-knowledge")
def add_knowledge(
    layer: str = typer.Option(..., help="org|team|project|personal|session"),
    scope: str = typer.Option(..., "--scope"),
    content: str = typer.Option(...),
    staleness: str = typer.Option("medium", "--staleness"),
    confidence: float = typer.Option(1.0),
    key: str = KeyOption,
):
    """POST /v1/knowledge_items."""
    _request(
        "POST",
        "/v1/knowledge_items",
        key=key,
        json={
            "layer": layer,
            "scope_id": scope,
            "content": content,
            "staleness_hint": staleness,
            "confidence": confidence,
        },
    )


@app.command()
def revise(item_id: str, content: str = typer.Option(...), key: str = KeyOption):
    """POST /v1/knowledge_items/{id}/revise: supersede a fact with new content."""
    _request("POST", f"/v1/knowledge_items/{item_id}/revise", key=key, json={"content": content})


@app.command("extract-git")
def extract_git(
    repo_path: str = typer.Option(..., "--repo-path"),
    scope: str = typer.Option(..., "--scope"),
    layer: str = typer.Option("project"),
    limit: int = typer.Option(50),
    key: str = KeyOption,
):
    """POST /v1/extract/git (admin; path must be under KAIROS_GIT_EXTRACT_ROOTS on the server)."""
    _request(
        "POST",
        "/v1/extract/git",
        key=key,
        json={"repo_path": repo_path, "scope_id": scope, "layer": layer, "limit": limit},
    )


@app.command()
def dashboard(key: str = KeyOption):
    """GET /v1/dashboard."""
    _request("GET", "/v1/dashboard", key=key)


@candidates_app.command("propose")
def candidates_propose(
    content: str = typer.Option(...),
    layer: str = typer.Option(...),
    scope: str = typer.Option(..., "--scope"),
    session: str = typer.Option(None, "--session"),
    supersedes: str = typer.Option(None, "--supersedes"),
    key: str = KeyOption,
):
    """POST /v1/candidates."""
    _request(
        "POST",
        "/v1/candidates",
        key=key,
        json={
            "proposed_content": content,
            "proposed_layer": layer,
            "proposed_scope_id": scope,
            "source_session_id": session,
            "supersedes_item_id": supersedes,
        },
    )


@candidates_app.command("get")
def candidates_get(candidate_id: int, key: str = KeyOption):
    """GET /v1/candidates/{id}."""
    _request("GET", f"/v1/candidates/{candidate_id}", key=key)


@candidates_app.command("approve")
def candidates_approve(candidate_id: int, reviewed_by: str = typer.Option(..., "--by"), key: str = KeyOption):
    """POST /v1/candidates/{id}/approve (admin)."""
    _request("POST", f"/v1/candidates/{candidate_id}/approve", key=key, json={"reviewed_by": reviewed_by})


@candidates_app.command("reject")
def candidates_reject(candidate_id: int, reviewed_by: str = typer.Option(..., "--by"), key: str = KeyOption):
    """POST /v1/candidates/{id}/reject."""
    _request("POST", f"/v1/candidates/{candidate_id}/reject", key=key, json={"reviewed_by": reviewed_by})


@keys_app.command("create")
def keys_create(
    label: str = typer.Option(None),
    role: str = typer.Option("member", help="admin|member|viewer"),
    person: str = typer.Option(None, "--person", help="Bind the key to this person (recommended)"),
    key: str = KeyOption,
):
    """POST /v1/api_keys. The raw key is shown once."""
    _request("POST", "/v1/api_keys", key=key, json={"label": label, "role": role, "person_id": person})


@keys_app.command("list")
def keys_list(key: str = KeyOption):
    """GET /v1/api_keys."""
    _request("GET", "/v1/api_keys", key=key)


@keys_app.command("revoke")
def keys_revoke(key_id: int, key: str = KeyOption):
    """POST /v1/api_keys/{id}/revoke."""
    _request("POST", f"/v1/api_keys/{key_id}/revoke", key=key)


if __name__ == "__main__":
    app()
