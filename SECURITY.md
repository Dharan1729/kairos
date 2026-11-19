# Security

## Reporting a vulnerability

Please report security issues privately through GitHub's "Report a vulnerability" on this repository's Security tab, not in a public issue. Include the affected endpoint or module, steps to reproduce and the impact. Expect a first reply within a few days. This is a small project, so there is no formal SLA.

Issues that let one caller read another person's, team's or tenant's knowledge, or that make Kairos serve stale or superseded knowledge from its cache, are the ones we most want to hear about.

## Threat model

Kairos holds an organization's internal knowledge and hands parts of it to coding agents. The agent, and whatever the agent is told by the content it reads, is not trusted. The credential is.

### What Kairos protects

- **Tenant isolation.** A credential belongs to one tenant (one org). Every read and write is limited to that org's scopes. Ids of other tenants' scopes, items, proposals and keys are refused or return the same 404 as missing ones. Tested against every endpoint and MCP tool ([tests/test_tenant_isolation.py](tests/test_tenant_isolation.py)).
- **Person and team boundaries inside a tenant.** A key or SSO token is bound to one person. Personal and session knowledge is readable only by its owner. Team and project knowledge is readable by the owning team. The request cannot widen this: naming another user or another user's session is refused; another team's repo is left out ([docs/guide/concepts.md](docs/guide/concepts.md)).
- **The cache cannot cross those boundaries or serve stale content.** Bundles are partitioned by the caller's scope chain, identity and the versions of every readable item, and re-validated item by item on every hit ([docs/guide/caching.md](docs/guide/caching.md), [tests/test_cache_safety.py](tests/test_cache_safety.py)).
- **Write integrity.** Viewers cannot write. Members can write only to their own team's scopes and their own personal and session scopes; org-wide facts need an admin. An item's layer must match its scope's kind. Agent proposals (`remember`) are never served until an admin approves them.
- **Credentials.** Only SHA-256 hashes of API keys are stored. SSO tokens are verified against the tenant's own JWKS, with a fixed algorithm allow-list and a required expiry. Dev keys written by `kairos seed` are created with mode 600.
- **Server-side paths.** Git extraction over the API reads a path on the server, so it is admin-only and limited to directories listed in `KAIROS_GIT_EXTRACT_ROOTS` (off when unset).

### What Kairos does not protect

- **What the agent does with the context.** Kairos decides what an agent receives. A compromised or prompt-injected agent can still leak what it was legitimately given.
- **Prompt injection inside knowledge.** Stored facts are returned as text. A malicious fact written by someone with write access to a scope reaches every agent that reads that scope. Review proposals before approving them.
- **The directory itself.** Who is on which team comes from your system of record. If that data is wrong, scope decisions are wrong.
- **Admin service keys.** An unbound admin key may act as any person in its tenant, by design (for gateways that authenticate users themselves). Treat it like a root credential; give agents person-bound member keys.
- **Transport and storage encryption.** Kairos speaks plain HTTP and `kairos serve` binds to 127.0.0.1 by default; put TLS in front of it. Knowledge, audit data and cached bundles are plaintext in the database.
- **Availability under abuse.** Rate limits are per tenant and in memory per process: a fair-use control, not DDoS protection.
- **Third parties you enable.** With `KAIROS_LLM_PROVIDER` set, knowledge content is sent to that provider for compression and summaries.
- **The dashboard URL.** `GET /dashboard` accepts `?api_key=`, which ends up in browser history and access logs. Use a viewer key there.

## Known limitations

- No per-project memberships: project access follows team ownership.
- SSO audience checking is skipped for a tenant with no `sso_audience` when the token has no `aud` claim. Set `sso_audience`.
- The ANN top-k (500) is a hard cutoff; items beyond it are reported in `dropped` with reason `retrieval_limit`.
- Audit rows are not tamper-evident.
- No third-party security audit has been done.

## Dependencies

`pip-audit` runs in CI on every push and pull request.
