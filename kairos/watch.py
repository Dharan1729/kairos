"""Auto-sync extraction (after colbymchenry/codegraph, see docs/prior-art.md).

Watches a git repo's .git directory for changes (new commits) and
re-runs the git-log extractor (kairos/knowledge.py) automatically,
instead of requiring a manual POST /v1/extract/git call every time.

Runs as a separate process from the API server, deliberately: sharing a
SQLAlchemy session across threads inside the request-serving process is a
real footgun, and CodeGraph's own architecture (a separate watcher
process feeding a shared local index) is the same shape. Like
kairos/seed.py, this is a trusted local/ops tool — it calls
extract_from_git_log directly, bypassing the HTTP API and auth, the same
way seed.py bypasses them to bootstrap the DB.

Not implemented (see docs/prior-art.md): CodeGraph's "staleness
banner" idea — surfacing "extraction in progress" in assemble_context's
response while a sync is running. That needs a signal shared across the
watcher and API processes (e.g. a row in the DB); this only watches and
re-extracts, it doesn't yet tell assemble_context anything about its own
timing.

Usage: python3 -m kairos.watch <repo_path> <scope_id> [--layer project] [--debounce-ms 2000]
"""

import argparse
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from kairos.db import configure, create_all, get_sessionmaker
from kairos.knowledge import extract_from_git_log, propose_llm_commit_summary
from kairos.llm import get_llm_provider


class DebouncedGitLogHandler(FileSystemEventHandler):
    """Watches .git specifically (HEAD, refs/heads/*, etc.) rather than the
    whole working tree, so this re-extracts on actual commits, not on every
    source file save."""

    def __init__(self, repo_path: str, scope_id: str, layer: str, debounce_seconds: float):
        self.repo_path = repo_path
        self.scope_id = scope_id
        self.layer = layer
        self.debounce_seconds = debounce_seconds
        self._last_event_time = 0.0
        self._pending = False

    def on_any_event(self, event):
        self._last_event_time = time.monotonic()
        self._pending = True

    def maybe_run(self) -> int:
        if self._pending and (time.monotonic() - self._last_event_time) >= self.debounce_seconds:
            self._pending = False
            return self.extract_now()
        return 0

    def extract_now(self) -> int:
        SessionLocal = get_sessionmaker()
        db = SessionLocal()
        try:
            items = extract_from_git_log(db, self.repo_path, self.scope_id, self.layer)
            # Optional LLM enrichment (kairos/llm.py): a no-op unless
            # KAIROS_LLM_PROVIDER and the vendor's key are set.
            summary_item = propose_llm_commit_summary(
                db,
                get_llm_provider(),
                self.scope_id,
                self.layer,
                [item.content for item in items],
            )
            db.commit()
            if items:
                print(f"[kairos.watch] extracted {len(items)} new knowledge item(s) from {self.repo_path}")
            if summary_item:
                print(f"[kairos.watch] added an LLM-synthesized summary ({summary_item.id})")
            return len(items)
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description="Auto-sync git-log extraction for a repo.")
    parser.add_argument("repo_path")
    parser.add_argument("scope_id")
    parser.add_argument("--layer", default="project")
    parser.add_argument("--debounce-ms", type=int, default=2000)
    args = parser.parse_args()

    configure()
    create_all()

    git_dir = Path(args.repo_path) / ".git"
    if not git_dir.exists():
        raise SystemExit(f"{args.repo_path} is not a git repository (no .git directory found)")

    handler = DebouncedGitLogHandler(args.repo_path, args.scope_id, args.layer, args.debounce_ms / 1000)
    handler.extract_now()  # sync once immediately on startup, don't wait for the first change

    observer = Observer()
    observer.schedule(handler, str(git_dir), recursive=True)
    observer.start()
    print(f"[kairos.watch] watching {git_dir} for changes (debounce {args.debounce_ms}ms)... Ctrl-C to stop.")
    try:
        while True:
            handler.maybe_run()
            time.sleep(0.5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
