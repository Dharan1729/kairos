"""Graph-centrality ranking, bi-temporal versioning and on-demand search
(see docs/prior-art.md for where each idea comes from)."""


class TestGraphCentrality:
    def test_populate_same_scope_edges_links_similar_items(self, client, db_session):
        from kairos.graph import compute_centrality, populate_same_scope_edges
        from kairos.knowledge import create_knowledge_item
        from kairos.models import KnowledgeGraphEdge

        db = db_session
        a = create_knowledge_item(
            db,
            layer="team",
            scope_id="team-platform",
            content="Platform team uses Terraform for all infra provisioning.",
        )
        b = create_knowledge_item(
            db,
            layer="team",
            scope_id="team-platform",
            content="Terraform is how Platform team provisions all infrastructure.",
        )
        c = create_knowledge_item(
            db,
            layer="team",
            scope_id="team-platform",
            content="Unrelated fact about the office coffee machine schedule.",
        )
        db.commit()

        # Edges are created on write (create_knowledge_item -> link_item);
        # a backfill over the scope finds nothing left to add.
        assert populate_same_scope_edges(db, "team-platform") == 0
        db.commit()

        edges = db.query(KnowledgeGraphEdge).filter(KnowledgeGraphEdge.from_item_id.in_([a.id, b.id, c.id])).all()
        edge_pairs = {(e.from_item_id, e.to_item_id) for e in edges}
        assert (a.id, b.id) in edge_pairs or (b.id, a.id) in edge_pairs
        assert (a.id, c.id) not in edge_pairs and (c.id, a.id) not in edge_pairs

        centrality = compute_centrality(db, {a.id, b.id, c.id})
        assert centrality[a.id] > centrality[c.id]
        assert centrality[b.id] > centrality[c.id]

    def test_centrality_is_neutral_with_no_edges(self, db_session):
        from kairos.graph import compute_centrality

        result = compute_centrality(db_session, {"nonexistent-1", "nonexistent-2"})
        assert result == {"nonexistent-1": 1.0, "nonexistent-2": 1.0}


class TestBiTemporalVersioning:
    def test_superseding_a_fact_hides_it_from_retrieval(self, client, db_session):
        from kairos.knowledge import create_knowledge_item

        db = db_session
        old = create_knowledge_item(
            db, layer="team", scope_id="team-platform", content="Old convention: deploys happen manually via SSH."
        )
        db.commit()
        old_version = old.knowledge_version

        propose = client.post(
            "/v1/candidates",
            json={
                "proposed_content": "New convention: deploys happen via CI/CD pipeline only, no manual SSH.",
                "proposed_layer": "team",
                "proposed_scope_id": "team-platform",
                "supersedes_item_id": old.id,
            },
        )
        candidate_id = propose.json()["id"]
        approve = client.post(f"/v1/candidates/{candidate_id}/approve", json={"reviewed_by": "eng-lead-1"})
        assert approve.status_code == 200

        db.refresh(old)
        assert old.valid_until is not None
        assert old.superseded_by_id == approve.json()["promoted_item_id"]
        assert old.knowledge_version > old_version

        result = client.post(
            "/v1/search_knowledge",
            json={
                "context_principal": {
                    "user_id": "person-team-platform-0",
                    "agent_type": "claude_code",
                    "repo": None,
                    "session_id": "session-0",
                },
                "query": "how do deploys happen",
                "budget_tokens": 4000,
            },
        ).json()
        contents = [r["content"] for r in result["results"]]
        assert "Old convention: deploys happen manually via SSH." not in contents
        assert any("CI/CD" in c for c in contents)


class TestSearchKnowledge:
    def test_search_returns_scoped_results_and_is_never_cached(self, client):
        body = {
            "context_principal": {"user_id": "person-team-platform-0", "session_id": "session-0"},
            "query": "terraform infrastructure conventions",
            "budget_tokens": 4000,
        }
        first = client.post("/v1/search_knowledge", json=body)
        assert first.status_code == 200 and first.json()["results"]
        assert "cache" not in first.json()
        assert client.post("/v1/search_knowledge", json=body).json()["results"] == first.json()["results"]


class TestSupersedeSecurity:
    """Regression tests for the security-review finding: approving a
    candidate with supersedes_item_id must not be able to invalidate a
    knowledge item outside the candidate's own proposed scope, and must
    not silently re-supersede an already-superseded item."""

    def test_cannot_supersede_an_item_in_a_different_scope(self, client, db_session):
        from kairos.knowledge import create_knowledge_item

        db = db_session
        victim = create_knowledge_item(
            db, layer="team", scope_id="team-ml", content="ML team's own internal convention."
        )
        db.commit()

        propose = client.post(
            "/v1/candidates",
            json={
                "proposed_content": "Attacker-controlled replacement fact.",
                "proposed_layer": "team",
                "proposed_scope_id": "team-platform",  # different scope from the victim item
                "supersedes_item_id": victim.id,
            },
        )
        candidate_id = propose.json()["id"]

        approve = client.post(f"/v1/candidates/{candidate_id}/approve", json={"reviewed_by": "attacker"})
        assert approve.status_code == 403

        db.refresh(victim)
        assert victim.valid_until is None  # untouched
        assert victim.superseded_by_id is None

    def test_cannot_supersede_an_already_superseded_item(self, client, db_session):
        from kairos.knowledge import create_knowledge_item

        db = db_session
        original = create_knowledge_item(db, layer="team", scope_id="team-platform", content="Original fact.")
        db.commit()

        first_propose = client.post(
            "/v1/candidates",
            json={
                "proposed_content": "First replacement.",
                "proposed_layer": "team",
                "proposed_scope_id": "team-platform",
                "supersedes_item_id": original.id,
            },
        )
        first_id = first_propose.json()["id"]
        first_approve = client.post(f"/v1/candidates/{first_id}/approve", json={"reviewed_by": "eng-lead-1"})
        assert first_approve.status_code == 200
        first_promoted_id = first_approve.json()["promoted_item_id"]

        second_propose = client.post(
            "/v1/candidates",
            json={
                "proposed_content": "Second, conflicting replacement.",
                "proposed_layer": "team",
                "proposed_scope_id": "team-platform",
                "supersedes_item_id": original.id,
            },
        )
        second_id = second_propose.json()["id"]
        second_approve = client.post(f"/v1/candidates/{second_id}/approve", json={"reviewed_by": "eng-lead-2"})
        assert second_approve.status_code == 409

        db.refresh(original)
        assert original.superseded_by_id == first_promoted_id  # not overwritten by the second attempt


class TestAdapterSearchFormatting:
    def test_format_search_results_reads_the_results_key(self):
        from kairos.adapters.claude_code import ContextProvider

        response = {"results": [{"layer": "team", "content": "Some fact.", "source": "ki-1"}], "dropped": []}
        formatted = ContextProvider.format_search_results(response)
        assert "Some fact." in formatted

        # format_bundle must NOT silently return content for a search()-shaped response
        assert ContextProvider.format_bundle(response) == ""
