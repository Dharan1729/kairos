"""Tests DebouncedGitLogHandler's actual extraction logic against a real
temp git repo. Doesn't drive the watchdog Observer/OS file-event machinery
directly (that's better exercised by hand against a real filesystem than
simulated in CI — flaky and not where the real logic lives); extract_now()
and the debounce gate are the parts worth a unit test.
"""

import subprocess


def _init_temp_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Initial commit"], cwd=path, check=True)


class TestDebouncedGitLogHandler:
    def test_extract_now_creates_knowledge_items(self, client, db_session, tmp_path):
        from kairos.watch import DebouncedGitLogHandler

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_temp_repo(repo)

        handler = DebouncedGitLogHandler(str(repo), "team-platform", "project", debounce_seconds=2.0)
        created = handler.extract_now()
        assert created == 1

        from kairos.models import KnowledgeItem

        items = db_session.query(KnowledgeItem).filter(KnowledgeItem.origin == "git").all()
        assert any("Initial commit" in i.content for i in items)

    def test_extract_now_is_idempotent_on_rerun(self, client, tmp_path):
        from kairos.watch import DebouncedGitLogHandler

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_temp_repo(repo)

        handler = DebouncedGitLogHandler(str(repo), "team-platform", "project", debounce_seconds=2.0)
        assert handler.extract_now() == 1
        assert handler.extract_now() == 0  # same commit, already ingested — no duplicate

    def test_maybe_run_respects_debounce_window(self, tmp_path):
        from kairos.watch import DebouncedGitLogHandler

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_temp_repo(repo)

        handler = DebouncedGitLogHandler(str(repo), "team-platform", "project", debounce_seconds=60.0)
        handler.on_any_event(event=type("E", (), {"src_path": str(repo / ".git" / "HEAD")})())
        # Debounce window (60s) hasn't elapsed yet — must not fire.
        assert handler.maybe_run() == 0
