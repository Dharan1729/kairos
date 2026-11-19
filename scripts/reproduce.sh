#!/usr/bin/env bash
# Reproduce the committed results. Writes to out/, never to results/.
#   bash scripts/reproduce.sh quick      lint, types, tests, leak probe (no Docker)
#   bash scripts/reproduce.sh bench      selection, cache, SQLite latency
#   bash scripts/reproduce.sh postgres   Postgres latency + test suite (needs Docker)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python}
mode=${1:-quick}

case "$mode" in
  quick)
    ruff check kairos tests benchmarks alembic
    mypy kairos
    $PY -m pytest -q
    $PY -m benchmarks.leak_probe --label this-tree --out out/leak_probe.json
    ;;
  bench)
    $PY -m benchmarks.selection_quality
    $PY -m benchmarks.cache_bench
    $PY -m benchmarks.latency
    ;;
  postgres)
    port=${KAIROS_PG_PORT:-55439}
    KAIROS_PG_PORT=$port docker compose -p kairos-bench up -d postgres
    trap 'docker compose -p kairos-bench down -v' EXIT
    for _ in $(seq 1 60); do
      docker compose -p kairos-bench exec -T postgres pg_isready -U kairos >/dev/null 2>&1 && break; sleep 1
    done
    url="postgresql://kairos:kairos-local-only@127.0.0.1:${port}/kairos"
    KAIROS_DATABASE_URL=$url alembic upgrade head
    KAIROS_DATABASE_URL=$url alembic check
    KAIROS_TEST_DATABASE_URL=$url $PY -m pytest -q
    $PY -m benchmarks.latency --database-url "$url"
    ;;
  *) echo "usage: $0 quick|bench|postgres" >&2; exit 2 ;;
esac
