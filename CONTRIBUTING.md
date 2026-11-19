# Contributing

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q          # about 30 seconds; the first run downloads the embedding model
ruff check kairos tests benchmarks alembic && ruff format --check kairos tests benchmarks alembic
mypy kairos
```

- Run the suite against Postgres too when you touch queries or models: `bash scripts/reproduce.sh postgres` (starts a throwaway container on 127.0.0.1:55439 and removes it afterwards), or set `KAIROS_TEST_DATABASE_URL`.
- **Anything that changes what a caller can read needs an adversarial test** in `tests/test_cache_safety.py` or `tests/test_tenant_isolation.py` that fails without the change. Run `python -m benchmarks.leak_probe` as well.
- Schema changes need an Alembic migration; CI runs `alembic upgrade head` and `alembic check` on Postgres.
- Claims about quality, cache behaviour or speed go in `docs/benchmarks.md` with the command that reproduces them and a committed result in `results/`. Say how many runs and what data. Report results that go against the change too.
- Keep new heavy dependencies behind an extra.
