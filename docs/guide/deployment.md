# Deployment

Kairos is one stateless process plus a database. It has run in tests and benchmarks on SQLite and Postgres 16.

## Docker Compose (Postgres)

```bash
KAIROS_HTTP_PORT=8000 KAIROS_PG_PORT=55432 KAIROS_PG_PASSWORD=change-me docker compose up -d --build
docker compose exec kairos kairos seed        # demo org + dev keys inside the container, or load your own directory
docker compose exec kairos cat /home/kairos/.kairos/dev_keys.json
```

Both ports bind to 127.0.0.1 and are configurable, so the stack can run next to others; use `-p <name>` for a separate Compose project. The container runs `alembic upgrade head` before starting. The image runs as a non-root user, has a health check on `/healthz`, and uses CPU-only PyTorch. The embedding model is downloaded on first use into the `hf_cache` volume.

## Without Docker

```bash
HNSWLIB_NO_NATIVE=1 pip install ".[postgres]"   # portable build; see below
export KAIROS_DATABASE_URL=postgresql://kairos:...@db:5432/kairos
alembic upgrade head
uvicorn kairos.api:app --host 127.0.0.1 --port 8000 --workers 2
```

`hnswlib` (the vector index) is compiled from source and, by default, tuned to the CPU it is built on (`-march=native`). If you build on one machine and run on another, as with a CI-built image or a shared wheel cache, set `HNSWLIB_NO_NATIVE=1` when installing, or the process can die with "illegal instruction". The Dockerfile already does this.

## Migrations

`alembic upgrade head` before starting a new version. Upgrading from 0.1.0 runs `2b7c1e0d4a91`, which adds person binding to keys and SSO, recreates the cache table (cached bundles are discarded) and simplifies the audit ledger. Existing keys keep working as unbound keys: bind or replace agent keys, because unbound member keys now see the org layer only.

## TLS and exposure

Kairos speaks plain HTTP. Put a reverse proxy or ingress with TLS in front of it, and do not expose the raw port. Restrict `/healthz`, `/readyz`, `/docs` and `/dashboard` at the edge if you do not want them public.

## SSO

Set per tenant in `tenant_configs`:

```sql
UPDATE tenant_configs SET
  sso_issuer = 'https://idp.example.com/',
  sso_audience = 'kairos-api',
  sso_jwks_url = 'https://idp.example.com/.well-known/jwks.json',
  sso_role_claim = 'kairos_role', sso_default_role = 'viewer',
  sso_user_claim = 'sub'
WHERE tenant_id = 'default';
```

The `sso_user_claim` value must equal a `people.id` in the directory. Tokens are RS256 or ES256, must carry `exp`, and are verified against the issuing tenant's JWKS (cached for an hour; `sso_jwks_static` pins a JWKS instead).

## Scaling notes

The ANN index cache and rate-limit counters live in process memory. With several workers or replicas, each keeps its own: indexes are rebuilt per process (cheap at the target scale), and rate limits apply per process. The bundle cache is in the database and shared.
