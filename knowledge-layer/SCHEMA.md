# Synthetic Knowledge Layer — Schema

Synthetic data simulating an org's knowledge across all five Kairos layers
(see [docs/guide/concepts.md](../docs/guide/concepts.md)). It is demo and
test fixture data: the goal is a realistic *shape* (relationships, overlap,
paraphrases), not realistic volume.

## Entities

- **`org.json`** — one org: `{id, name, policies[], standards[]}`
- **`teams.json`** — `{id, org_id, name, conventions[], initiatives[]}`
- **`people.json`** — the people graph: `{id, name, role, team_id, manager_id,
  expertise[], seniority}`. `manager_id` forms the reporting tree; `team_id`
  links into teams.
- **`projects.json`** — `{id, team_id, name, repo_name, architecture_notes[],
  decisions[]}`
- **`context_items.json`** — the actual knowledge snippets that get selected
  and cached. Every item is tagged with the layer it belongs to and the
  scope it's relevant at:
  `{id, layer: org|team|project|personal|session, scope_id, content, tags[],
  created_at, staleness_hint}`
  - `scope_id` points at the org/team/project/person/session id it belongs to.
  - `content` is a short natural-language snippet (a fact, decision, or
    preference) — the unit that would actually get embedded and cached.
- **`sessions.json`** — simulated working sessions used as *queries* against
  the dataset: `{id, person_id, project_id, task_description, timestamp,
  recent_messages[]}`. Two sessions with semantically similar
  `task_description` values are the synthetic "cache hit" test case; two
  sessions with unrelated tasks are the "cache miss" / no-reuse case.

## Hierarchy resolution

For a session, the readable scopes are (see `kairos/pipeline/scope.py`):

```
org -> the person's team -> the session's project (if the team owns it) -> the person -> the session
```

## Why this shape

- **People and sessions** give personal and session knowledge a real owner,
  so the rules "only the owner reads it" and "the cache never shares it"
  are testable.
- **Overlap is intentional.** Several items paraphrase each other and pairs
  of sessions share a task, so ranking and cache tests have real signal.
- **No embeddings are stored here.** Kairos embeds `content` when it seeds.
