#!/usr/bin/env python3
"""Generate synthetic org data across Kairos's five knowledge layers.

Produces org.json, teams.json, people.json, projects.json,
context_items.json, and sessions.json under kairos/demo_data/. Pure stdlib, seeded
for reproducibility. See SCHEMA.md for the shape of each file.
"""
import json
import random
from pathlib import Path

random.seed(42)

DATA_DIR = Path(__file__).resolve().parent.parent / "kairos" / "demo_data"

ORG = {
    "id": "org-1",
    "name": "Nimbus Robotics",
    "policies": [
        "All customer data must stay in region of origin",
        "No secrets committed to version control",
        "Production deploys require two approvals",
    ],
    "standards": [
        "All services expose /healthz",
        "Internal APIs versioned as /v{n}/",
        "Structured logging (JSON) required in all services",
    ],
}

TEAMS = [
    {"id": "team-platform", "org_id": "org-1", "name": "Platform",
     "conventions": ["Terraform for all infra", "Trunk-based development"],
     "initiatives": ["Migrating from EC2 to k8s", "Cost reduction Q3"]},
    {"id": "team-ml", "org_id": "org-1", "name": "ML Perception",
     "conventions": ["PyTorch only, no TF", "Model cards required per release"],
     "initiatives": ["Reducing inference latency below 50ms", "New object-detection model"]},
    {"id": "team-firmware", "org_id": "org-1", "name": "Firmware",
     "conventions": ["C for hot path, Rust for new modules"],
     "initiatives": ["OTA update reliability push"]},
    {"id": "team-appsvc", "org_id": "org-1", "name": "App Services",
     "conventions": ["GraphQL for client-facing APIs", "Feature-flagged rollouts"],
     "initiatives": ["Mobile app offline mode"]},
]

FIRST_NAMES = ["Ana", "Ben", "Chidi", "Dana", "Elin", "Farid", "Grace", "Hana",
               "Ivo", "Jae", "Kira", "Leo", "Maya", "Noor", "Omar", "Priya"]
ROLES = ["Software Engineer", "Senior Software Engineer", "Staff Engineer",
         "Engineering Manager", "ML Engineer", "Firmware Engineer"]
EXPERTISE_POOL = ["kubernetes", "pytorch", "rust", "graphql", "terraform",
                   "distributed-systems", "embedded-c", "ci-cd", "observability",
                   "vector-search", "postgres", "kafka"]

def gen_people():
    people = []
    managers = {}
    for team in TEAMS:
        mgr_id = f"person-{team['id']}-mgr"
        people.append({
            "id": mgr_id, "name": f"{random.choice(FIRST_NAMES)} ({team['name']} Lead)",
            "role": "Engineering Manager", "team_id": team["id"], "manager_id": None,
            "expertise": random.sample(EXPERTISE_POOL, 3), "seniority": "manager",
        })
        managers[team["id"]] = mgr_id
        for i in range(3):
            pid = f"person-{team['id']}-{i}"
            people.append({
                "id": pid, "name": f"{random.choice(FIRST_NAMES)} {random.choice(FIRST_NAMES)}",
                "role": random.choice(ROLES), "team_id": team["id"],
                "manager_id": mgr_id, "expertise": random.sample(EXPERTISE_POOL, 3),
                "seniority": random.choice(["junior", "mid", "senior"]),
            })
    return people

PEOPLE = gen_people()

PROJECTS = [
    {"id": "proj-orchestrator-core", "team_id": "team-platform", "name": "Orchestrator Core",
     "repo_name": "nimbus/orchestrator-core",
     "architecture_notes": ["Event-driven, Kafka backbone", "Postgres for durable state"],
     "decisions": ["Chose Kafka over SQS for replay support", "Split scheduler out of monolith in 2024"]},
    {"id": "proj-perception-model", "team_id": "team-ml", "name": "Perception Model",
     "repo_name": "nimbus/perception-model",
     "architecture_notes": ["ONNX export for edge deploy", "Trained on internal robot fleet data"],
     "decisions": ["Moved from TF to PyTorch in 2023", "Quantized model for edge inference"]},
    {"id": "proj-ota-agent", "team_id": "team-firmware", "name": "OTA Agent",
     "architecture_notes": ["A/B partition scheme", "Rollback on 3 consecutive failed healthchecks"],
     "decisions": ["Signed firmware images required since 2022 incident"]},
    {"id": "proj-mobile-app", "team_id": "team-appsvc", "name": "Mobile App",
     "architecture_notes": ["GraphQL gateway in front of internal REST services"],
     "decisions": ["Offline-first cache using local SQLite"]},
]
for p in PROJECTS:
    p.setdefault("repo_name", "nimbus/" + p["id"])

# Context items: deliberately include paraphrased duplicates across scopes
# so cache-hit-rate tests have real signal.
CONTEXT_TEMPLATES = [
    ("org", "org-1", "All services must expose a /healthz endpoint for readiness checks."),
    ("org", "org-1", "Every service needs a health check endpoint at /healthz."),
    ("org", "org-1", "Secrets must never be committed to git; use the vault instead."),
    ("team", "team-platform", "Platform team does trunk-based development, no long-lived feature branches."),
    ("team", "team-platform", "We migrated infra provisioning to Terraform; no manual console changes."),
    ("team", "team-ml", "ML Perception standardized on PyTorch; TensorFlow code is legacy and being phased out."),
    ("team", "team-ml", "Model releases require a model card documenting training data and eval results."),
    ("project", "proj-orchestrator-core", "Orchestrator Core uses Kafka as its event backbone because it needed replay support that SQS lacked."),
    ("project", "proj-perception-model", "The perception model was ported from TensorFlow to PyTorch in 2023 for better export tooling."),
    ("project", "proj-ota-agent", "OTA Agent requires signed firmware images since a 2022 supply-chain incident."),
    ("project", "proj-mobile-app", "Mobile app caches data locally in SQLite to support offline-first usage."),
]

def gen_context_items():
    items = []
    for i, (layer, scope_id, content) in enumerate(CONTEXT_TEMPLATES):
        items.append({
            "id": f"ctx-{i}", "layer": layer, "scope_id": scope_id, "content": content,
            "tags": [layer, scope_id], "created_at": f"2025-{(i % 12) + 1:02d}-01",
            "staleness_hint": "low" if layer in ("org", "team") else "medium",
        })
    # personal-memory items: preferences/feedback per person
    prefs = [
        "Prefers terse PR descriptions over long ones.",
        "Likes to be shown the diff before any commit.",
        "Wants test coverage called out explicitly in review comments.",
        "Prefers pairing on architecture decisions rather than async docs.",
        "Dislikes speculative abstractions; wants YAGNI enforced in review.",
    ]
    for person in PEOPLE:
        items.append({
            "id": f"ctx-personal-{person['id']}", "layer": "personal", "scope_id": person["id"],
            "content": f"{person['name'].split()[0]}: {random.choice(prefs)}",
            "tags": ["personal", person["id"]], "created_at": "2025-06-01",
            "staleness_hint": "low",
        })
    return items

CONTEXT_ITEMS = gen_context_items()

TASK_TEMPLATES = [
    "Add a /healthz endpoint to a new internal service",
    "Add a health check route to a service that's missing one",
    "Investigate why the perception model is slower after the PyTorch migration",
    "Debug latency regression in perception model post-PyTorch port",
    "Set up Terraform for a new platform microservice",
    "Review OTA rollback behavior after 3 failed healthchecks",
    "Add offline caching to a new mobile screen",
    "Write a model card for the new object-detection release",
]

def gen_sessions():
    sessions = []
    people_by_project = {
        "proj-orchestrator-core": "team-platform", "proj-perception-model": "team-ml",
        "proj-ota-agent": "team-firmware", "proj-mobile-app": "team-appsvc",
    }
    project_ids = list(people_by_project.keys())
    for i, task in enumerate(TASK_TEMPLATES * 2):
        project_id = project_ids[i % len(project_ids)]
        team_id = people_by_project[project_id]
        candidates = [p for p in PEOPLE if p["team_id"] == team_id]
        person = random.choice(candidates)
        sessions.append({
            "id": f"session-{i}", "person_id": person["id"], "project_id": project_id,
            "task_description": task, "timestamp": f"2025-11-{(i % 28) + 1:02d}T09:00:00Z",
            "recent_messages": [f"User: {task}", "Agent: Let me look into that."],
        })
    return sessions

# Session-layer working notes: private to the session's owner. Added after
# every random draw above, so the rest of the data is unchanged by them.
SESSION_NOTES = {
    "session-0": "Working notes: the new service is ledger-sync; its readiness probe must also check Kafka consumer lag.",
    "session-1": "Working notes: the perception service's health route must not load model weights.",
    "session-4": "Working notes: reuse the Terraform module in infra/modules/svc-base rather than writing a new one.",
    "session-6": "Working notes: the offline cache for the new screen should reuse the existing SQLite schema.",
    "session-12": "Working notes: this healthz work is blocked on the shared probe library release.",
}


def gen_session_items():
    return [
        {"id": f"ctx-{sid}", "layer": "session", "scope_id": sid, "content": note,
         "tags": ["session", sid], "created_at": "2025-11-01", "staleness_hint": "high"}
        for sid, note in SESSION_NOTES.items()
    ]


def main():
    DATA_DIR.mkdir(exist_ok=True)
    files = {
        "org.json": ORG, "teams.json": TEAMS, "people.json": PEOPLE,
        "projects.json": PROJECTS, "context_items.json": CONTEXT_ITEMS + gen_session_items(),
        "sessions.json": gen_sessions(),
    }
    for name, data in files.items():
        (DATA_DIR / name).write_text(json.dumps(data, indent=2))
        count = len(data) if isinstance(data, list) else 1
        print(f"wrote {name} ({count} records)")

if __name__ == "__main__":
    main()
