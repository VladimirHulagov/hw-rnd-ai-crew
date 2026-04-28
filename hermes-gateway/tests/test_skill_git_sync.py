import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from skill_git_sync import SkillGitSync


@pytest.fixture
def sync(tmp_path):
    return SkillGitSync(
        repo_url="https://github.com/example/skills.git",
        branch="main",
        path="skills",
        token="ghp_testtoken",
        author="Agent <agent@example.com>",
    )


@pytest.fixture
def sync_repo(tmp_path, sync):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    sync._repo_dir = repo_dir
    return sync, repo_dir


class TestSkillGitSyncInit:
    def test_init_sets_properties(self, tmp_path):
        s = SkillGitSync(
            repo_url="https://github.com/x/y.git",
            branch="develop",
            path="agent-skills",
            token="tok123",
            author="Bot <bot@test.com>",
        )
        assert s.repo_url == "https://github.com/x/y.git"
        assert s.branch == "develop"
        assert s.path == "agent-skills"
        assert s.token == "tok123"
        assert s.author == "Bot <bot@test.com>"


class TestSkillGitSyncPush:
    def test_push_writes_skill_files(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker Management\nContent here"),
            ("coding", "python-dev", "# Python Dev\nCode here"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="M skills/devops/docker-mgmt/SKILL.md", returncode=0)
            result = sync.push_skills(conn, "company-123")

        skill_dir = repo_dir / "skills"
        assert (skill_dir / "devops" / "docker-mgmt" / "SKILL.md").exists()
        assert (skill_dir / "coding" / "python-dev" / "SKILL.md").exists()
        assert (skill_dir / "devops" / "docker-mgmt" / "SKILL.md").read_text() == "# Docker Management\nContent here"
        assert result["pushed"] == 2

    def test_push_removes_deleted_skills(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        existing = repo_dir / "skills" / "old-category" / "old-skill"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("old content")

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="D skills/old-category/old-skill/SKILL.md", returncode=0)
            result = sync.push_skills(conn, "company-123")

        assert not (repo_dir / "skills" / "old-category").exists()
        assert result["removed"] >= 1

    def test_push_skips_when_no_changes(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="", returncode=0)
            result = sync.push_skills(conn, "company-123")

            commit_calls = [c for c in mock_git.call_args_list if c[0][0] and "commit" in str(c[0][0])]
            assert len(commit_calls) == 0
        assert result["skipped"] is True

    def test_push_commits_with_correct_author(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            sync.push_skills(conn, "company-123")

            commit_calls = [c for c in mock_git.call_args_list if c[0][0] and "commit" in str(c[0][0])]
            assert len(commit_calls) >= 1

        git_env = sync._git_env()
        assert git_env["GIT_AUTHOR_NAME"] == "Agent"
        assert git_env["GIT_AUTHOR_EMAIL"] == "agent@example.com"


class TestSkillGitSyncPull:
    def test_pull_imports_new_skills(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        skill_dir = repo_dir / "skills" / "devops" / "docker-mgmt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: Docker\ndescription: Manage Docker\n---\n# Docker content"
        )

        with patch.object(sync, "_ensure_repo", return_value=True):
            result = sync.pull_skills(conn, "company-123")

        assert cur.execute.call_count >= 1
        assert result["imported"] + result["updated"] >= 1

    def test_pull_correct_key_format(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        skill_dir = repo_dir / "skills" / "devops" / "docker-mgmt"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: Docker\n---\n# Docker")

        with patch.object(sync, "_ensure_repo", return_value=True):
            sync.pull_skills(conn, "company-123")

        upsert_calls = [
            c for c in cur.execute.call_args_list
            if "INSERT INTO company_skills" in str(c)
        ]
        assert len(upsert_calls) >= 1
        args = upsert_calls[0][0][1]
        assert args[1] == "git/devops/docker-mgmt"

    def test_pull_metadata_sourceKind(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        skill_dir = repo_dir / "skills" / "coding" / "python"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: Python\n---\n# Python")

        with patch.object(sync, "_ensure_repo", return_value=True):
            sync.pull_skills(conn, "company-123")

        upsert_calls = [
            c for c in cur.execute.call_args_list
            if "INSERT INTO company_skills" in str(c)
        ]
        assert len(upsert_calls) >= 1
        metadata_arg = upsert_calls[0][0][1][-1]
        metadata = json.loads(metadata_arg)
        assert metadata["sourceKind"] == "git_sync"


class TestSkillGitSyncErrors:
    def test_invalid_repo_url(self, tmp_path, mock_db):
        sync = SkillGitSync(
            repo_url="",
            branch="main",
            path="skills",
            token="",
            author="Agent <a@b.com>",
        )
        sync._repo_dir = tmp_path / "repo"
        _, conn, _ = mock_db

        result = sync.push_skills(conn, "company-123")
        assert result["skipped"] is True

    def test_git_clone_failure(self, tmp_path, mock_db):
        sync = SkillGitSync(
            repo_url="https://github.com/nonexistent/repo.git",
            branch="main",
            path="skills",
            token="tok",
            author="Agent <a@b.com>",
        )
        sync._repo_dir = tmp_path / "repo"
        _, conn, _ = mock_db

        with patch.object(sync, "_ensure_repo", return_value=False):
            result = sync.push_skills(conn, "company-123")
            assert result["skipped"] is True
