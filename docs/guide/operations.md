# Operations

## Keys

```bash
kairos keys create --role member --person alice --label "alice laptop"   # for agents
kairos keys create --role viewer --label "dashboard"                     # org-only reads
kairos keys list
kairos keys revoke 7
```

Give agents person-bound member keys. Keep unbound admin keys for gateways and administration.

## Loading your directory

Kairos reads `orgs`, `teams`, `people`, `projects` (with `repo_name`) and `sessions`. There is no API for them: write them from your HR or source-control system of record with a small job, or adapt `kairos/seed.py`, which loads `kairos/demo_data/*.json`. Then create a `tenant_configs` row mapping a tenant id to the org. Moving a person to another team takes effect on their next request; their cached bundles become unreachable because the scope chain changes.

## Adding knowledge

- Directly: `kairos add-knowledge --layer team --scope team-platform --content "..."` (member of that team, or admin for org).
- From git: `kairos watch /path/to/repo proj-x` re-extracts commit subjects into a project when new commits land (runs locally, against the database).
- From agents: `remember` proposals, reviewed below.
- Corrections: `kairos revise <item id> --content "..."` supersedes the old fact.

## Reviewing proposals

```bash
kairos candidates get 12
kairos candidates approve 12 --by alice        # admin
kairos candidates reject 12 --by alice
```

Approve only what you would want every reader of that scope to receive: approved text reaches their agents verbatim.

## Monitoring

- `/healthz` (liveness) and `/readyz` (database) for probes.
- `kairos dashboard` or `/dashboard`: outcomes, hit rate, tokens, latency, knowledge freshness.
- `audit_ledger` has one row per request: user, agent, repo, session, outcome, tokens, latency.
- Tracing: set `OTEL_EXPORTER_OTLP_ENDPOINT`; each request is a span with the cache outcome and tokens.

## Backups

```bash
KAIROS_DATABASE_URL=... scripts/backup.sh            # SQLite .backup or pg_dump -Fc into ./backups
KAIROS_DATABASE_URL=... scripts/restore.sh backups/kairos-....dump
```

The cache table can be emptied at any time (`DELETE FROM cache_entries`) without losing anything.

## Changing the embedding model

Set `KAIROS_EMBEDDING_MODEL` (or `KAIROS_EMBEDDER`), then `kairos reembed`: it recomputes every item's vector and clears the cache. The cache key includes the embedder, so old bundles are never mixed with new ones.
