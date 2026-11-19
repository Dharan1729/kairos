"""Retrieval fails open on bad stored data, and only ever loads readable,
currently valid items."""

from datetime import UTC, datetime, timedelta

from kairos.embeddings import embed
from kairos.knowledge import create_knowledge_item
from kairos.models import KnowledgeItem
from kairos.pipeline.retrieval import VectorRetriever
from kairos.pipeline.types import ResolvedScope, ScopeRef

SCOPE = ResolvedScope(tenant_id="default", org_id="org-1", person_id=None, chain=(ScopeRef("team", "team-platform"),))


def test_one_corrupt_embedding_does_not_crash_retrieval(client, db_session):
    good = create_knowledge_item(db_session, layer="team", scope_id="team-platform", content="A normal fact.")
    corrupt = create_knowledge_item(
        db_session, layer="team", scope_id="team-platform", content="A fact whose embedding gets corrupted."
    )
    corrupt.embedding_ref = "{not valid json"
    db_session.commit()

    candidates = VectorRetriever().retrieve(db_session, SCOPE, embed("normal fact")).candidates
    ids = {c.id for c in candidates}
    assert good.id in ids and corrupt.id in ids
    assert next(c for c in candidates if c.id == corrupt.id).similarity == 0.0


def test_all_corrupt_embeddings_still_returns_candidates(client, db_session):
    db_session.query(KnowledgeItem).filter_by(scope_id="team-platform").update({"embedding_ref": "not json"})
    db_session.commit()
    candidates = VectorRetriever().retrieve(db_session, SCOPE, embed("anything")).candidates
    assert candidates and all(c.similarity == 0.0 for c in candidates)


def test_assemble_with_corrupt_embedding_returns_200(client, db_session):
    item = create_knowledge_item(db_session, layer="team", scope_id="team-platform", content="Corrupt one.")
    item.embedding_ref = "[1, 2"
    db_session.commit()
    resp = client.post(
        "/v1/assemble_context",
        json={"context_principal": {"user_id": "person-team-platform-0"}, "task": {"description": "terraform"}},
    )
    assert resp.status_code == 200


def test_only_resolved_scopes_are_loaded(client, db_session):
    candidates = VectorRetriever().retrieve(db_session, SCOPE, embed("pytorch")).candidates
    assert candidates and {c.scope_id for c in candidates} == {"team-platform"}


def test_expired_items_are_not_retrieved(client, db_session):
    item = create_knowledge_item(db_session, layer="team", scope_id="team-platform", content="Expires soon.")
    item.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    ids = {c.id for c in VectorRetriever().retrieve(db_session, SCOPE, embed("expires")).candidates}
    assert item.id not in ids
