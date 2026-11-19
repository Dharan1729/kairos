# HTTP API

All `/v1` endpoints need `Authorization: Bearer <API key or SSO token>`. Interactive docs are at `/docs` on a running server. Errors: 401 unauthenticated, 403 not allowed, 404 not found (also for other tenants' or other scopes' ids), 409 conflict, 422 invalid input, 429 rate limited.

## Context

### `POST /v1/assemble_context`

```json
{
  "context_principal": {"user_id": null, "agent_type": "claude_code", "repo": "nimbus/orchestrator-core", "session_id": "session-0"},
  "task": {"description": "Add a /healthz endpoint", "recent_messages": []},
  "budget_tokens": 4000
}
```

Everything but `task.description` is optional. `user_id` defaults to the credential's person. Response:

```json
{
  "context_bundle": [{"layer": "org", "content": "Every service needs ...", "source": "ctx-1"}],
  "cache": {"hit": false, "outcome": "miss", "similarity_score": 0.0, "cache_key": "bundle_..."},
  "tokens_used": 114,
  "latency_ms": 12.3,
  "dropped": [{"layer": "team", "count": 1, "reason": "budget"}],
  "scope": {"layers": [{"layer": "org", "scope_id": "org-1"}, {"layer": "team", "scope_id": "team-platform"}],
            "notes": ["unknown repo 'x' ignored"]}
}
```

`source` is the knowledge item id. `dropped.reason` is `budget`, `relevance` or `retrieval_limit`. 403 if the principal names another user or another user's session.

### `POST /v1/search_knowledge`

Same principal; `"query"` instead of `"task"`; default budget 2000. Never cached. Response: `results`, `tokens_used`, `dropped`, `scope`.

## Knowledge

| Method and path | Role | |
|---|---|---|
| `POST /v1/knowledge_items` | member+ | `{layer, scope_id, content, staleness_hint?, confidence?, origin?, source_id?}`. Write rules in [concepts](concepts.md#writing). |
| `POST /v1/knowledge_items/{id}/revise` | member+ (who may write the item's scope) | `{content}`. Supersedes the item with a new one; returns the new id. |
| `POST /v1/extract/git` | admin | `{repo_path, scope_id, layer?, limit?}`. Only under `KAIROS_GIT_EXTRACT_ROOTS`. |

## Proposals

| Method and path | Role | |
|---|---|---|
| `POST /v1/candidates` | member+ | `{proposed_content, proposed_layer, proposed_scope_id, source_session_id?, supersedes_item_id?}` |
| `GET /v1/candidates/{id}` | member+ who may write its scope | |
| `POST /v1/candidates/{id}/approve` | admin | `{reviewed_by}`. Creates the item (confidence 0.8), supersedes the target if given. |
| `POST /v1/candidates/{id}/reject` | member+ who may write its scope | `{reviewed_by}` |

## Keys

| Method and path | Role | |
|---|---|---|
| `POST /v1/api_keys` | admin | `{label?, role?, person_id?}`. Returns the raw key once. |
| `GET /v1/api_keys` | admin | This tenant's keys (never the raw key). |
| `POST /v1/api_keys/{id}/revoke` | admin | |

## Other

| | |
|---|---|
| `GET /healthz` | Liveness, no auth, not rate limited. |
| `GET /readyz` | Database reachable, no auth, not rate limited. |
| `GET /v1/dashboard` | Tenant aggregates: outcomes, hit rate, tokens, latency, freshness. |
| `GET /dashboard?api_key=...` | The same as an HTML page. |
