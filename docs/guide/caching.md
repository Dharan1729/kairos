# Caching

Kairos caches assembled bundles so a repeated request returns the same bundle without ranking again. A cache that serves the wrong bundle is worse than none, so reuse is allowed only when it can be shown to be equivalent to assembling fresh.

## How a lookup works

1. The scope is resolved (see [concepts](concepts.md)). The cache never sees a request before that.
2. Kairos reads the id and version of every item valid now in the caller's scopes (one light query) and hashes them: the *readable fingerprint*.
3. It computes the *partition key*: a SHA-256 over
   - the tenant,
   - the scope chain (every layer and scope id),
   - the caller's person id (or, with cross-user reuse on, nothing: see below),
   - the readable fingerprint,
   - the exact budget,
   - the embedder's name.
4. Only entries in that partition are compared, by cosine similarity of the task embedding. The best one is a candidate hit if it reaches `KAIROS_CACHE_SIMILARITY` (default 0.90).
5. Before serving, every item in the bundle is re-read. It must still exist, be valid now, live in a scope in this caller's chain, and have the same content hash as when the bundle was built. If any check fails, the entry is deleted, the response is assembled fresh, and the outcome is `stale`.
6. On a miss the fresh bundle is stored with its contributing item ids and content hashes. Entries expire after `KAIROS_CACHE_TTL_SECONDS` (default one day); at most 32 are kept per partition.

## Safety rules

| Rule | Enforced by | Tested in |
|---|---|---|
| A bundle is only reused by a caller with the identical scope chain and readable knowledge | partition key | `test_same_task_same_project_never_serves_another_users_private_items` |
| Personal and session knowledge is never shared across users | person id in the key; with cross-user reuse, the fingerprint covers private items | `test_cross_user_reuse_shares_only_between_users_without_private_items` |
| Another team's knowledge never reaches you through the cache | team and project in the chain come from the directory | `test_other_teams_repo_is_not_readable` |
| Edits, supersessions, new items and expiry invalidate | fingerprint changes; ORM edits bump `knowledge_version` | `test_revised_item_...`, `test_orm_in_place_edit_...`, `test_expired_item_...` |
| Raw SQL edits are not served | content hash re-check on hit | `test_raw_sql_edit_is_caught_by_content_check_on_hit` |
| A bundle holding an unreadable item is never served, however it got there | scope re-check on hit | `test_tampered_cache_entry_with_unreadable_item_is_not_served` |
| A bundle built for a larger budget is never served to a smaller one | exact budget in the key | `test_budget_is_part_of_the_cache_key` |

All in [tests/test_cache_safety.py](../../tests/test_cache_safety.py). The leak probe ([benchmarks](../benchmarks.md#bench-001--leak-and-staleness-probe)) runs the same attacks over HTTP.

## Cross-user reuse

`KAIROS_CACHE_CROSS_USER_REUSE=true` leaves the person and session scope ids out of the partition key. The readable fingerprint still includes every personal and session item the caller can read, so:

- two users with no private items in play, on the same team and project, share bundles;
- a user with any personal or session item never shares with anyone.

It is off by default. On the benchmark org it raised the hit rate from 0.21 to 0.29, all from users without personal items.

## The similarity threshold

`KAIROS_CACHE_SIMILARITY` decides how different two task descriptions may be and still share a bundle. Measured on the benchmark (BENCH-003): at 0.75 every one of 8 paraphrase hits served a bundle that differed from a fresh assembly; at 0.90 there were no paraphrase hits and no wrong hits. The default is 0.90, which in practice means near-exact repeats. Lower it only after measuring on your own requests.

## What it buys

Little latency at this scale: on the benchmark a hit took 6.8 ms against 8.0 ms for a miss (median), because the task is embedded before the lookup. The cache is about returning the same bundle for the same request and saving database work, not speed. Turn it off with `KAIROS_CACHE_ENABLED=false`; outcomes are then `disabled`.

## Outcomes

`cache.outcome` in a response: `hit`, `miss`, `stale` (a hit failed validation and was rebuilt), `disabled`; `search_knowledge` is never cached (`bypass` in the audit ledger).
