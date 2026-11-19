# Quickstart

Kairos keeps your organization's knowledge in layers (org, team, project, personal, session) and gives a coding agent the part that applies to its task, within a token budget, and only what the person behind the agent may read. This page gets it running locally in a few minutes with SQLite. No Docker needed.

## 1. Install

```bash
git clone https://github.com/Dharan1729/kairos && cd kairos
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

Python 3.11+. The install includes `sentence-transformers` and PyTorch (about 1 GB; on Linux, `pip install -e . --extra-index-url https://download.pytorch.org/whl/cpu` gets the smaller CPU-only build).

**First-run download.** The first time anything is embedded, the model `sentence-transformers/all-MiniLM-L6-v2` (about 90 MB) is downloaded from Hugging Face to `~/.cache/huggingface`. Later runs use the cache and work offline (`HF_HUB_OFFLINE=1`). To avoid the download entirely, `KAIROS_EMBEDDER=hashing` uses a dependency-free embedder with no semantic matching: fine for a smoke test, not for real use.

## 2. One command

```bash
kairos quickstart
```

This:

1. creates `./.kairos/kairos.db` (SQLite) and loads the synthetic demo org from `kairos/demo_data/` (one org, 4 teams, 16 people, 4 projects, 16 sessions, 32 facts);
2. writes `./.kairos/dev_keys.json` (mode 600) with an `admin` service key and a `demo_user` key bound to `person-team-platform-0`;
3. prints a sample bundle;
4. serves the API on `http://127.0.0.1:8000` (Ctrl-C to stop).

`kairos quickstart --no-serve` stops after step 3. Set `KAIROS_HOME` to keep the state somewhere other than `./.kairos`.

## 3. Ask for context without a server

```bash
kairos try "Set up Terraform for a new platform microservice"
kairos try "Add a /healthz endpoint" --session session-0 --budget 80
kairos try "Investigate the perception model latency" --user person-team-ml-2
kairos try "Anything" --session session-4          # refused: session-4 is someone else's
kairos try "Kafka replay" --repo nimbus/perception-model   # another team's repo: left out, with a note
```

Each run prints the resolved scope chain, any notes, the bundle and what was dropped.

## 4. Call the API

```bash
export KAIROS_URL=http://127.0.0.1:8000
export KAIROS_API_KEY=$(python -c "import json;print(json.load(open('.kairos/dev_keys.json'))['demo_user'])")

kairos assemble --task "Add a /healthz endpoint" --session session-0 --budget 300
kairos search --query "terraform"
kairos candidates propose --layer personal --scope person-team-platform-0 --content "Prefers table-driven tests."
```

Or with curl:

```bash
curl -s http://127.0.0.1:8000/v1/assemble_context \
  -H "Authorization: Bearer $KAIROS_API_KEY" -H "Content-Type: application/json" \
  -d '{"task": {"description": "Add a /healthz endpoint"}, "budget_tokens": 300}'
```

Approve the proposal with the admin key (`kairos candidates approve 1 --by you --key <admin key>`) and it is served from then on.

## 5. Connect an agent

See [MCP setup](mcp.md) for Claude Code and Cursor.

## 6. Use your own knowledge

The demo org is fictional. To use Kairos for real, load your directory (orgs, teams, people, projects) and facts: see [operations](operations.md#loading-your-directory). For Postgres and Docker, see [deployment](deployment.md).
