# Concepts

## Knowledge layers

Every fact (a *knowledge item*) lives in exactly one scope, and the kind of scope decides its layer:

| Layer | Scope | Who can read it | Example |
|---|---|---|---|
| `org` | the org | everyone in the tenant | "Every service exposes /healthz." |
| `team` | a team | members of that team | "We use Terraform for all infra." |
| `project` | a project (a repo) | members of the team that owns the project | "Chose Kafka over SQS for replay." |
| `personal` | a person | that person | "Prefers small PRs." |
| `session` | an agent session | the person who owns the session | "Working notes: blocked on the probe library." |

`org`, `team` and `project` are *shared* layers; `personal` and `session` are *private*.

Items are bi-temporal: `valid_from` and `valid_until` record when a fact holds. Replacing a fact (`POST /v1/knowledge_items/{id}/revise`, or approving a proposal with `supersedes_item_id`) sets `valid_until` on the old item and keeps it for the audit trail. Only items valid now are ever retrieved.

## The directory

Orgs, teams, people, projects (with their repo names) and sessions form the *directory*. Kairos reads it to decide who may read what; it does not manage it through the API. Load it with `kairos seed` (the demo org) or your own sync job writing to the `orgs`, `teams`, `people`, `projects` and `sessions` tables.

## Who is asking

Every request carries a credential. It resolves to a *caller*: a tenant (one org), a role, and usually a person.

| Credential | Acts as |
|---|---|
| API key bound to a person (`kairos keys create --person p`) | that person, always |
| SSO token (JWT from the tenant's identity provider) | the person named by the tenant's `sso_user_claim` (default `sub`) |
| Unbound admin key | any person in its tenant, named in the request. For a trusted gateway that authenticates users itself. |
| Unbound member or viewer key | nobody: org layer only. For ingestion bots and dashboards. |

Roles: `viewer` reads; `member` also writes (to its own scopes, below) and proposes; `admin` also approves proposals, writes org-wide facts and manages keys.

## The context principal

A request describes who the context is for:

```json
{"user_id": "person-team-platform-0", "agent_type": "claude_code", "repo": "nimbus/orchestrator-core", "session_id": "session-0"}
```

All fields are optional. `user_id` defaults to the credential's person, and must match it if given. `repo` and `session_id` are hints: they are checked against the directory, never trusted.

## Scope resolution

`kairos/pipeline/scope.py` turns caller + principal into a *scope chain*, failing closed:

1. **Org**: the tenant's org. Always.
2. **Person**: the credential's person (or, for an unbound admin key, the named person). Not in the directory → org layer only, with a note.
3. **Team**: the person's own team.
4. **Project**: from the session if one is given, else from `repo` (looked up in the caller's org only). Included only if the person's team owns it; otherwise left out with a note.
5. **Personal**: the person.
6. **Session**: only if it belongs to the person. Someone else's session → 403. An unknown session id → ignored with a note (agents often send ids Kairos has never seen).

The response's `scope` field lists the chain and the notes, so a caller can see exactly what was searched and why something was left out.

## Writing

`kairos.access.check_can_write` governs items and proposals alike:

- the scope must belong to the tenant, and the layer must match the scope kind;
- `admin`: any scope;
- `member` bound to a person: their team's team and project scopes, their own personal scope and sessions (not org);
- `member` unbound: team and project scopes only;
- `viewer`: nothing.

Agents propose with the MCP `remember` tool (`POST /v1/candidates`). Nothing proposed is served until an admin approves it.

## Assembly

After resolution, the pipeline retrieves valid items from the chain, ranks them, and fills the budget. See [architecture](../architecture.md) for the stages and [caching](caching.md) for reuse. Every response reports `tokens_used` and `dropped`: what was left out, by layer and reason (`budget`, `relevance`, `retrieval_limit`).
