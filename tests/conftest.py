"""Shared fixtures. Every test gets its own SQLite file and $KAIROS_HOME,
seeded with the demo org, and a TestClient authenticated with the demo
tenant's admin service key (which may act as any demo user)."""

import os

import pytest
from fastapi.testclient import TestClient

from kairos.db import configure, create_all, get_sessionmaker
from kairos.ratelimit import reset_rate_limiter_for_tests
from kairos.vector_index import reset_cache_for_tests


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path / "home"))
    for name in (
        "KAIROS_DATABASE_URL",
        "KAIROS_CACHE_CROSS_USER_REUSE",
        "KAIROS_CACHE_ENABLED",
        "KAIROS_LLM_PROVIDER",
        "KAIROS_GIT_EXTRACT_ROOTS",
        "KAIROS_EMBEDDER",
        "KAIROS_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    # In-process caches keyed by values that can repeat across test databases.
    reset_cache_for_tests()
    reset_rate_limiter_for_tests()


@pytest.fixture()
def client(tmp_path):
    # KAIROS_TEST_DATABASE_URL runs the whole suite against another database
    # (CI uses Postgres); the schema is dropped and recreated per test.
    url = os.environ.get("KAIROS_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'kairos_test.db'}"
    configure(url)
    if url.startswith("postgres"):
        from kairos.db import Base, get_engine

        Base.metadata.drop_all(bind=get_engine())
    create_all()
    from kairos.seed import seed

    db = get_sessionmaker()()
    try:
        raw_key = seed(db)
        db.commit()
    finally:
        db.close()
    from kairos.api import app

    return TestClient(app, headers={"Authorization": f"Bearer {raw_key}"})


@pytest.fixture()
def db_session(client):
    db = get_sessionmaker()()
    yield db
    db.close()


@pytest.fixture()
def make_key(client):
    """make_key(role="member", person_id=None, tenant_id="default") -> raw key."""
    from kairos.auth import generate_api_key

    def _make(role: str = "member", person_id: str | None = None, tenant_id: str = "default") -> str:
        db = get_sessionmaker()()
        try:
            key = generate_api_key(db, tenant_id, label="test", role=role, person_id=person_id)
            db.commit()
            return key
        finally:
            db.close()

    return _make


@pytest.fixture()
def as_key(client):
    """as_key(raw_key) -> a TestClient using that credential."""
    from kairos.api import app

    def _client(raw_key: str) -> TestClient:
        return TestClient(app, headers={"Authorization": f"Bearer {raw_key}"})

    return _client


@pytest.fixture()
def second_tenant(client):
    """Seeds a second tenant ("other") with its own org, team, project,
    person, session and one item per layer. Returns its admin key."""
    from kairos.auth import generate_api_key
    from kairos.directory import Org, Person, Project, SessionRecord, Team
    from kairos.knowledge import create_knowledge_item
    from kairos.models import TenantConfig

    db = get_sessionmaker()()
    try:
        db.add(Org(id="org-2", name="Other Org", policies="[]", standards="[]"))
        db.add(Team(id="team-other", org_id="org-2", name="Other Team"))
        db.add(Person(id="person-other", name="Other Person", team_id="team-other"))
        db.add(Project(id="proj-other", team_id="team-other", name="Other", repo_name="other/repo"))
        db.add(
            SessionRecord(id="session-other", person_id="person-other", project_id="proj-other", recent_messages="[]")
        )
        db.add(TenantConfig(tenant_id="other", org_id="org-2"))
        db.flush()
        for layer, scope in [
            ("org", "org-2"),
            ("team", "team-other"),
            ("project", "proj-other"),
            ("personal", "person-other"),
            ("session", "session-other"),
        ]:
            create_knowledge_item(
                db,
                layer=layer,
                scope_id=scope,
                item_id=f"other-{layer}",
                content=f"OTHER-TENANT SECRET {layer}: add a /healthz endpoint",
            )
        key = generate_api_key(db, "other", label="other admin", role="admin")
        db.commit()
        return key
    finally:
        db.close()
