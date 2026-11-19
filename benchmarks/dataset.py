"""The benchmark org: a hand-written synthetic company with labelled tasks.

Separate from the demo org (knowledge-layer/), which is too small to
measure selection: everything in it fits in any reasonable budget.

Shape: 1 org, 3 teams, 6 projects, 9 people, one session per task.
Knowledge: topic facts at every layer, cross-team look-alikes (unreadable
for most askers), superseded facts (not valid now), and filler that is
true but irrelevant to every task.

Labels: each task names its topic. The gold set for a task is every fact
of that topic, plus any personal or session note tagged with the task, that
lives in a scope the asker can read and is valid now. Gold is computed, not
hand-listed, from those rules (``gold_for``).

Bias warning (also in docs/benchmarks.md): the author of this dataset also
wrote the ranker. Facts and tasks were written before the benchmark was
run and not edited afterwards to change results, but the phrasing is still
one person's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ORG = "org-acme"
TEAMS = {"team-pay": "Payments", "team-search": "Search", "team-mobile": "Mobile"}
PROJECTS = {
    "proj-ledger": ("team-pay", "acme/ledger"),
    "proj-checkout": ("team-pay", "acme/checkout"),
    "proj-indexer": ("team-search", "acme/indexer"),
    "proj-query": ("team-search", "acme/query-api"),
    "proj-ios": ("team-mobile", "acme/ios-app"),
    "proj-android": ("team-mobile", "acme/android-app"),
}
PEOPLE = {f"{t.removeprefix('team-')}-{i}": t for t in TEAMS for i in (1, 2, 3)}

# (topic, layer, scope, text). Topic "filler" is never gold.
FACTS: list[tuple[str, str, str, str]] = [
    # T-health
    ("health", "org", ORG, "Every service must expose GET /healthz that returns 200 only when it can serve traffic."),
    (
        "health",
        "team",
        "team-pay",
        "Payments services include the database and card-processor connections in /healthz.",
    ),
    (
        "health",
        "project",
        "proj-ledger",
        "Ledger's /healthz also fails when read-replica replication lag exceeds 30 seconds.",
    ),
    ("health", "team", "team-search", "Search services report index freshness in their /healthz response body."),
    # T-secrets
    ("secrets", "org", ORG, "Never commit secrets to git; services load them from Vault at startup."),
    (
        "secrets",
        "team",
        "team-mobile",
        "Mobile apps fetch API keys from the remote config service and never bundle them in the binary.",
    ),
    (
        "secrets",
        "project",
        "proj-checkout",
        "Checkout's fraud-provider credentials rotate every 90 days through Vault.",
    ),
    # T-versioning
    (
        "versioning",
        "org",
        ORG,
        "Public HTTP APIs are versioned in the path as /v{n}/; a breaking change needs a new version.",
    ),
    ("versioning", "project", "proj-query", "query-api v2 is current; v1 is frozen and only receives security fixes."),
    ("versioning", "team", "team-search", "Search adds new response fields as optional so v2 clients keep working."),
    # T-logging
    ("logging", "org", ORG, "Logs are structured JSON with request_id, service and level fields."),
    ("logging", "team", "team-pay", "Never log full card numbers; log the last four digits only."),
    ("logging", "project", "proj-checkout", "Checkout tags every payment-step log line with the order_id."),
    # T-deploys
    ("deploys", "org", ORG, "Production deploys need two approvals and go out through the deploy pipeline only."),
    (
        "deploys",
        "team",
        "team-search",
        "Search deploys are canaried on 5% of traffic for 30 minutes before full rollout.",
    ),
    ("deploys", "team", "team-pay", "Payments does not deploy on Fridays or during the month-end close."),
    # T-migrations
    (
        "migrations",
        "team",
        "team-pay",
        "Schema migrations must stay backward compatible for one release: expand first, contract later.",
    ),
    (
        "migrations",
        "project",
        "proj-ledger",
        "Ledger tables are append-only; corrections are new rows, never UPDATE statements.",
    ),
    (
        "migrations",
        "team",
        "team-search",
        "Search reindexes into a new index and swaps the alias instead of migrating mappings in place.",
    ),
    # T-money
    (
        "money",
        "team",
        "team-pay",
        "Store money as integer minor units with an ISO 4217 currency code, never as floats.",
    ),
    ("money", "project", "proj-checkout", "Checkout rounds tax per line item, half-even, not on the cart total."),
    # T-idempotency
    (
        "idempotency",
        "team",
        "team-pay",
        "Every payment POST requires an Idempotency-Key header; a retry with the same key returns the first result.",
    ),
    ("idempotency", "project", "proj-checkout", "Checkout stores idempotency keys for 24 hours in Redis."),
    # T-ranking
    ("ranking", "team", "team-search", "Ranking changes need an offline NDCG evaluation before any A/B test."),
    ("ranking", "project", "proj-query", "query-api boosts exact SKU matches above full-text matches."),
    # T-indexing
    (
        "indexing",
        "project",
        "proj-indexer",
        "The indexer consumes the catalog Kafka topic in batches of 500 and commits offsets after each batch is written.",
    ),
    ("indexing", "team", "team-search", "Search runs Elasticsearch 8; do not use deprecated mapping types."),
    # T-releases
    (
        "releases",
        "team",
        "team-mobile",
        "Mobile ships a release every two weeks; unfinished features stay behind flags that are off.",
    ),
    ("releases", "project", "proj-ios", "The iOS app supports the current and the previous major iOS version only."),
    ("releases", "project", "proj-android", "The Android app's minimum SDK is 26."),
    # T-offline
    (
        "offline",
        "project",
        "proj-android",
        "The Android app caches the last 50 orders in Room so order history works offline.",
    ),
    (
        "offline",
        "team",
        "team-mobile",
        "Mobile views call the backend only through the shared API client, never by URL.",
    ),
    ("offline", "project", "proj-ios", "The iOS app caches orders in Core Data for offline viewing."),
    # T-a11y
    ("a11y", "org", ORG, "All user-facing UI must meet WCAG 2.1 AA."),
    (
        "a11y",
        "team",
        "team-mobile",
        "Every tappable element needs an accessibility label and a 44-point minimum hit area.",
    ),
    # T-testing
    ("testing", "org", ORG, "New code needs unit tests; CI blocks merges below 80% line coverage on changed files."),
    (
        "testing",
        "team",
        "team-pay",
        "Charge and refund flows are tested against the card-processor sandbox, not mocks.",
    ),
    # T-incidents
    ("incidents", "org", ORG, "Incidents of SEV2 or worse need a written postmortem within five working days."),
    ("incidents", "team", "team-search", "Search on-call rotates weekly with a handoff on Mondays at 10:00."),
    # T-flags
    ("flags", "org", ORG, "Feature flags live in the flag service and are removed within 30 days of full rollout."),
    ("flags", "team", "team-mobile", "Mobile flag removals ship in the next release train, never as a hotfix."),
    # T-ratelimit
    (
        "ratelimit",
        "project",
        "proj-query",
        "query-api limits anonymous clients to 10 requests per second per IP; partners get a key with higher limits.",
    ),
    ("ratelimit", "org", ORG, "Rate-limited responses use HTTP 429 with a Retry-After header."),
    # T-residency
    ("residency", "org", ORG, "Customer data stays in its region of origin; EU data never leaves eu-west-1."),
    (
        "residency",
        "project",
        "proj-ledger",
        "Ledger runs one database per region; cross-region reports read from the warehouse.",
    ),
    # filler: true, but irrelevant to every task
    ("filler", "org", ORG, "The engineering all-hands is on the first Tuesday of each month."),
    ("filler", "org", ORG, "Design documents live in the shared drive under /eng/designs."),
    ("filler", "org", ORG, "Laptops are refreshed every three years through the IT portal."),
    ("filler", "org", ORG, "The company holiday calendar is published each December."),
    ("filler", "org", ORG, "Office badges must be worn visibly in all buildings."),
    ("filler", "team", "team-pay", "Payments retros happen every other Thursday."),
    ("filler", "team", "team-pay", "The Payments quarterly goal is reducing checkout abandonment."),
    ("filler", "team", "team-pay", "New Payments hires shadow on-call for two weeks."),
    ("filler", "team", "team-search", "Search's planning meeting is on Wednesdays."),
    ("filler", "team", "team-search", "The Search team wiki has a glossary of ranking terms."),
    ("filler", "team", "team-search", "Search interns present their projects in August."),
    ("filler", "team", "team-mobile", "Mobile design reviews happen in the Thursday crit."),
    ("filler", "team", "team-mobile", "The Mobile team keeps a device lab with twenty test phones."),
    ("filler", "team", "team-mobile", "Mobile's quarterly goal is app-store rating above 4.6."),
    ("filler", "project", "proj-ledger", "Ledger was started in 2021 as a replacement for spreadsheets."),
    ("filler", "project", "proj-checkout", "Checkout's product manager sits with the Payments team."),
    ("filler", "project", "proj-indexer", "The indexer's dashboards are in the Search Grafana folder."),
    ("filler", "project", "proj-query", "query-api's README has a diagram of the request path."),
    ("filler", "project", "proj-ios", "The iOS app's icon was redesigned last year."),
    ("filler", "project", "proj-android", "The Android app's beta channel has about 2,000 users."),
]

# Superseded facts: valid in the past, not now. Never gold; never readable.
SUPERSEDED: list[tuple[str, str, str, str]] = [
    ("money", "team", "team-pay", "Store money as decimal strings with two places."),
    ("deploys", "team", "team-search", "Search deploys go straight to 100% after CI passes."),
    ("releases", "team", "team-mobile", "Mobile ships a release every month."),
    ("health", "org", ORG, "Services expose /status for health checks."),
]

PERSONAL: list[tuple[str, str, str | None]] = [
    # (person, text, task id it is relevant to, or None)
    ("pay-1", "Prefers small PRs with one migration per PR.", "pay-migration"),
    ("pay-1", "Likes code review comments to be phrased as questions.", None),
    ("pay-2", "Writes tests before the implementation and wants agents to do the same.", "pay-tests"),
    ("pay-2", "Prefers dark-mode screenshots in PR descriptions.", None),
    ("pay-3", "Wants every log change reviewed by the security champion.", "pay-logging"),
    ("search-1", "Explains ranking changes with before-and-after result examples.", "search-ranking"),
    ("search-1", "Prefers Python type hints everywhere.", None),
    ("search-2", "Likes performance claims backed by a benchmark run.", "search-indexing"),
    ("search-3", "Prefers runbooks as numbered checklists.", None),
    ("mobile-1", "Drafts release notes alongside the code change.", "mobile-release"),
    ("mobile-2", "Prefers Kotlin coroutines over RxJava.", "mobile-offline"),
    ("mobile-3", "Wants UI changes shown with a short screen recording.", None),
]


@dataclass
class BenchTask:
    id: str
    topic: str
    person: str
    project: str
    text: str
    paraphrase: str
    session_note: str | None = None
    gold: set[str] = field(default_factory=set)


TASKS: list[BenchTask] = [
    BenchTask(
        "pay-health",
        "health",
        "pay-1",
        "proj-ledger",
        "Add a health check endpoint to the ledger service",
        "Implement /healthz for ledger",
        "Working notes: ledger's replica is in the same region as the primary.",
    ),
    BenchTask(
        "search-health",
        "health",
        "search-2",
        "proj-indexer",
        "Add a readiness endpoint to the indexer",
        "The indexer needs a health check route",
    ),
    BenchTask(
        "mobile-secrets",
        "secrets",
        "mobile-1",
        "proj-ios",
        "Where should the iOS app keep the maps API key?",
        "Store the maps SDK key in the iOS app",
    ),
    BenchTask(
        "pay-secrets",
        "secrets",
        "pay-2",
        "proj-checkout",
        "Add a new API credential for the fraud provider",
        "Configure credentials for the fraud-check vendor",
    ),
    BenchTask(
        "search-versioning",
        "versioning",
        "search-1",
        "proj-query",
        "Add a new field to the search response",
        "Return an extra attribute in query-api results",
    ),
    BenchTask(
        "pay-logging",
        "logging",
        "pay-3",
        "proj-checkout",
        "Add logging to the checkout payment step",
        "Log what happens during payment in checkout",
    ),
    BenchTask(
        "search-deploys",
        "deploys",
        "search-3",
        "proj-indexer",
        "Ship the new indexer build to production",
        "Deploy the latest indexer release",
    ),
    BenchTask(
        "pay-migration",
        "migrations",
        "pay-1",
        "proj-ledger",
        "Add a column to the ledger entries table",
        "Write a schema migration for ledger entries",
        "Working notes: the entries table is partitioned by month.",
    ),
    BenchTask(
        "pay-money",
        "money",
        "pay-2",
        "proj-checkout",
        "Compute sales tax for a shopping cart",
        "Calculate tax amounts on checkout",
    ),
    BenchTask(
        "pay-idempotency",
        "idempotency",
        "pay-3",
        "proj-checkout",
        "Make the charge endpoint safe to retry",
        "Handle duplicate charge requests from client retries",
    ),
    BenchTask(
        "search-ranking",
        "ranking",
        "search-1",
        "proj-query",
        "Change how results are ordered for product searches",
        "Tweak product search result ordering",
    ),
    BenchTask(
        "search-indexing",
        "indexing",
        "search-2",
        "proj-indexer",
        "Speed up indexing of catalog updates",
        "Make the indexer process catalog changes faster",
        "Working notes: the current lag is about four minutes at peak.",
    ),
    BenchTask(
        "mobile-release",
        "releases",
        "mobile-1",
        "proj-ios",
        "Plan the release of the new onboarding screen",
        "Get the onboarding redesign into a release",
    ),
    BenchTask(
        "mobile-offline",
        "offline",
        "mobile-2",
        "proj-android",
        "Show order history when the phone is offline",
        "Make past orders viewable without a connection",
    ),
    BenchTask(
        "mobile-a11y",
        "a11y",
        "mobile-3",
        "proj-ios",
        "Build the new settings screen",
        "Create the redesigned settings page UI",
    ),
    BenchTask(
        "pay-tests",
        "testing",
        "pay-2",
        "proj-checkout",
        "Write tests for the refund flow",
        "Add test coverage for refunds",
    ),
    BenchTask(
        "search-incident",
        "incidents",
        "search-3",
        "proj-query",
        "Write up last night's query-api outage",
        "Document the query-api incident from yesterday",
    ),
    BenchTask(
        "mobile-flags",
        "flags",
        "mobile-2",
        "proj-android",
        "Remove the old checkout feature flag",
        "Clean up a fully rolled-out flag in the Android app",
    ),
    BenchTask(
        "search-ratelimit",
        "ratelimit",
        "search-1",
        "proj-query",
        "A partner says they are getting 429 errors",
        "Investigate too-many-requests responses for a partner",
    ),
    BenchTask(
        "pay-residency",
        "residency",
        "pay-3",
        "proj-ledger",
        "Build a report across EU and US ledger entries",
        "Aggregate ledger data from both regions into one report",
    ),
]


def session_id(task: BenchTask) -> str:
    return f"bsession-{task.id}"


def readable_scopes(person: str, project: str, session: str) -> set[str]:
    team = PEOPLE[person]
    scopes = {ORG, team, person, session}
    if PROJECTS[project][0] == team:
        scopes.add(project)
    return scopes


def item_rows() -> list[dict]:
    """Every knowledge item, with stable ids and a creation order (for the
    recency baseline)."""
    rows = []
    for i, (topic, layer, scope, text) in enumerate(FACTS):
        rows.append(
            {
                "id": f"f{i:03d}",
                "topic": topic,
                "layer": layer,
                "scope": scope,
                "text": text,
                "valid": True,
                "task": None,
            }
        )
    for i, (topic, layer, scope, text) in enumerate(SUPERSEDED):
        rows.append(
            {
                "id": f"s{i:03d}",
                "topic": topic,
                "layer": layer,
                "scope": scope,
                "text": text,
                "valid": False,
                "task": None,
            }
        )
    for i, (person, text, task_id) in enumerate(PERSONAL):
        rows.append(
            {
                "id": f"p{i:03d}",
                "topic": "personal",
                "layer": "personal",
                "scope": person,
                "text": text,
                "valid": True,
                "task": task_id,
            }
        )
    for t in TASKS:
        if t.session_note:
            rows.append(
                {
                    "id": f"n-{t.id}",
                    "topic": t.topic,
                    "layer": "session",
                    "scope": session_id(t),
                    "text": t.session_note,
                    "valid": True,
                    "task": t.id,
                }
            )
    # Interleave creation order deterministically so recency is not aligned
    # with topic or layer: a fixed permutation by a simple hash of the id.
    order = sorted(range(len(rows)), key=lambda i: (hash_id(rows[i]["id"]), rows[i]["id"]))
    for rank, i in enumerate(order):
        rows[i]["created_rank"] = rank
    return rows


def hash_id(text: str) -> int:
    h = 2166136261
    for ch in text.encode():
        h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
    return h


def gold_for(task: BenchTask, rows: list[dict]) -> set[str]:
    readable = readable_scopes(task.person, task.project, session_id(task))
    gold = set()
    for r in rows:
        if not r["valid"] or r["scope"] not in readable:
            continue
        if r["task"] == task.id or (
            r["topic"] == task.topic and r["layer"] != "personal" and r["task"] in (None, task.id)
        ):
            gold.add(r["id"])
    return gold


def forbidden_for(task: BenchTask, rows: list[dict]) -> set[str]:
    """Items the asker must never receive: unreadable scope or not valid now."""
    readable = readable_scopes(task.person, task.project, session_id(task))
    return {r["id"] for r in rows if not r["valid"] or r["scope"] not in readable}


def tasks_with_gold() -> list[BenchTask]:
    rows = item_rows()
    for t in TASKS:
        t.gold = gold_for(t, rows)
    return TASKS
