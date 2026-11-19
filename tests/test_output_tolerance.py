"""Regression test: BundleItemOut.layer must tolerate an off-enum stored
value rather than crash the whole response. A first pass at input
validation (Literal enums, tests/test_dashboard.py's XSS fix) mistakenly
applied the same strict Literal to this OUTPUT model too — meaning any
existing KnowledgeItem row with a bad layer value (e.g. a typo via
kairos/watch.py's --layer flag, which bypasses this API's Pydantic
validation entirely by calling create_knowledge_item directly, the same
trusted-tool pattern as kairos/seed.py) crashed assemble_context/
search_knowledge with an unhandled 500 for every request touching that
scope, instead of just serving it as-is like staleness_hint already does.
"""


def test_off_enum_layer_value_does_not_crash_assemble_context(client, db_session):
    from kairos.knowledge import create_knowledge_item

    create_knowledge_item(
        db_session,
        layer="teams",
        scope_id="team-platform",  # "teams" is not a valid Layer — a plausible typo
        content="A fact stored with a typo'd layer value.",
    )
    db_session.commit()

    resp = client.post(
        "/v1/assemble_context",
        json={
            "context_principal": {
                "user_id": "person-team-platform-0",
                "agent_type": "claude_code",
                "repo": None,
                "session_id": "session-0",
            },
            "task": {"description": "Add a /healthz endpoint to a new internal service"},
            "budget_tokens": 4000,
        },
    )
    assert resp.status_code == 200
    layers_seen = {item["layer"] for item in resp.json()["context_bundle"]}
    assert "teams" in layers_seen  # served as-is, not silently dropped or 500'd
