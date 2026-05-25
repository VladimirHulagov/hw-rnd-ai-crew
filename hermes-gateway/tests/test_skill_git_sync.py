import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from skill_git_sync import SkillGitSync


@pytest.fixture
def sync(tmp_path):
    return SkillGitSync(
        source_id="test-source-001",
        repo_url="https://git.example.com/example/skills.git",
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


def _write_manifest(sync, skills_dir, slugs):
    mp = sync._manifest_path(skills_dir)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(sorted(slugs)), encoding="utf-8")


class TestSkillGitSyncInit:
    def test_init_sets_properties(self, tmp_path):
        s = SkillGitSync(
            source_id="src1",
            repo_url="https://github.com/x/y.git",
            branch="develop",
            path="agent-skills",
            token="tok123",
            author="Bot <bot@test.com>",
        )
        assert s.source_id == "src1"
        assert s.repo_url == "https://github.com/x/y.git"
        assert s.branch == "develop"
        assert s.path == "agent-skills"
        assert s.token == "tok123"
        assert s.author == "Bot <bot@test.com>"

    def test_init_defaults(self):
        s = SkillGitSync()
        assert s.source_id == ""
        assert s.repo_url == ""
        assert s.branch == "main"


class TestSourceTag:
    def test_source_tag_is_md5_prefix(self):
        s = SkillGitSync(source_id="hello")
        import hashlib
        expected = hashlib.md5(b"hello").hexdigest()[:12]
        assert s._source_tag == expected

    def test_different_source_ids_different_tags(self):
        a = SkillGitSync(source_id="aaa")
        b = SkillGitSync(source_id="bbb")
        assert a._source_tag != b._source_tag


class TestSyncBranch:
    def test_sync_branch_format(self):
        s = SkillGitSync(source_id="my-source")
        assert s._sync_branch() == f"skills-sync/{s._source_tag}"


class TestManifest:
    def test_read_manifest_empty(self, tmp_path):
        s = SkillGitSync(source_id="x")
        result = s._read_manifest(tmp_path)
        assert result == set()

    def test_write_and_read_manifest(self, tmp_path):
        s = SkillGitSync(source_id="x")
        slugs = {"devops/docker", "coding/python"}
        s._write_manifest(tmp_path, slugs)
        result = s._read_manifest(tmp_path)
        assert result == slugs

    def test_read_manifest_corrupt_json(self, tmp_path):
        s = SkillGitSync(source_id="x")
        mp = s._manifest_path(tmp_path)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text("not json", encoding="utf-8")
        assert s._read_manifest(tmp_path) == set()

    def test_manifest_path_in_skills_dir(self, tmp_path):
        s = SkillGitSync(source_id="x")
        mp = s._manifest_path(tmp_path)
        assert ".manifests" in str(mp)


class TestParseRepoInfo:
    def test_parse_https_forgejo(self):
        owner, repo = SkillGitSync._parse_repo_info("https://git.example.com/owner/repo.git")
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_with_token(self):
        owner, repo = SkillGitSync._parse_repo_info("https://ghp_token@git.example.com/myorg/myrepo.git")
        assert owner == "myorg"
        assert repo == "myrepo"

    def test_parse_no_git_suffix(self):
        owner, repo = SkillGitSync._parse_repo_info("https://git.example.com/owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_github_still_works(self):
        owner, repo = SkillGitSync._parse_repo_info("https://github.com/myorg/skills-repo")
        assert owner == "myorg"
        assert repo == "skills-repo"

    def test_parse_forgejo_custom_host(self):
        owner, repo = SkillGitSync._parse_repo_info("https://git.mycompany.internal/devops/ansible-skills")
        assert owner == "devops"
        assert repo == "ansible-skills"

    def test_parse_forgejo_with_token_in_url(self):
        owner, repo = SkillGitSync._parse_repo_info("https://abc123@git.example.com/myorg/skills-repo")
        assert owner == "myorg"
        assert repo == "skills-repo"

    def test_parse_empty(self):
        owner, repo = SkillGitSync._parse_repo_info("")
        assert owner == ""
        assert repo == ""

    def test_parse_invalid(self):
        owner, repo = SkillGitSync._parse_repo_info("not-a-url")
        assert owner == ""
        assert repo == ""


class TestSkillGitSyncPush:
    def test_push_writes_skill_files(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker Management\nContent here"),
            ("coding", "python-dev", "# Python Dev\nCode here"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_prepare_sync_branch"), \
             patch.object(sync, "_finish_sync_branch"), \
             patch.object(sync, "_create_or_update_pr"), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="M skills/devops/docker-mgmt/SKILL.md", returncode=0)
            result = sync.push_skills(conn, "company-123")

        skill_dir = repo_dir / "skills"
        assert (skill_dir / "devops" / "docker-mgmt" / "SKILL.md").exists()
        assert (skill_dir / "coding" / "python-dev" / "SKILL.md").exists()
        assert (skill_dir / "devops" / "docker-mgmt" / "SKILL.md").read_text() == "# Docker Management\nContent here"
        assert result["pushed"] == 2

    def test_push_removes_manifest_tracked_skills(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        existing = repo_dir / "skills" / "old-category" / "old-skill"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("old content")

        _write_manifest(sync, repo_dir / "skills", {"old-category/old-skill"})

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_prepare_sync_branch"), \
             patch.object(sync, "_finish_sync_branch"), \
             patch.object(sync, "_create_or_update_pr"), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="D skills/old-category/old-skill/SKILL.md", returncode=0)
            result = sync.push_skills(conn, "company-123")

        assert not (repo_dir / "skills" / "old-category").exists()
        assert result["removed"] >= 1

    def test_push_does_not_delete_untracked_skills(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        other_skill = repo_dir / "skills" / "other-cat" / "other-skill"
        other_skill.mkdir(parents=True)
        (other_skill / "SKILL.md").write_text("other content")

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_prepare_sync_branch"), \
             patch.object(sync, "_finish_sync_branch"), \
             patch.object(sync, "_create_or_update_pr"), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="M skills/devops/docker-mgmt/SKILL.md", returncode=0)
            result = sync.push_skills(conn, "company-123")

        assert (repo_dir / "skills" / "other-cat" / "other-skill").exists()
        assert result["removed"] == 0

    def test_push_skips_when_no_changes(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_prepare_sync_branch"), \
             patch.object(sync, "_finish_sync_branch"), \
             patch.object(sync, "_create_or_update_pr"), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="", returncode=0)
            result = sync.push_skills(conn, "company-123")

            commit_calls = [c for c in mock_git.call_args_list if c[0][0] and "commit" in str(c[0][0])]
            assert len(commit_calls) == 0
            checkout_calls = [c for c in mock_git.call_args_list if c[0][0] and c[0][0] == "checkout"]
            assert any(c[0][1] == "main" for c in checkout_calls)
        assert result["skipped"] is True

    def test_push_commits_with_correct_author(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_prepare_sync_branch"), \
             patch.object(sync, "_finish_sync_branch"), \
             patch.object(sync, "_create_or_update_pr"), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            sync.push_skills(conn, "company-123")

            commit_calls = [c for c in mock_git.call_args_list if c[0][0] and "commit" in str(c[0][0])]
            assert len(commit_calls) >= 1

        git_env = sync._git_env()
        assert git_env["GIT_AUTHOR_NAME"] == "Agent"
        assert git_env["GIT_AUTHOR_EMAIL"] == "agent@example.com"

    def test_push_writes_manifest(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_prepare_sync_branch"), \
             patch.object(sync, "_finish_sync_branch"), \
             patch.object(sync, "_create_or_update_pr"), \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="M skills/devops/docker-mgmt/SKILL.md", returncode=0)
            sync.push_skills(conn, "company-123")

        manifest = sync._read_manifest(repo_dir / "skills")
        assert "devops/docker-mgmt" in manifest

    def test_push_calls_finish_sync_and_pr(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_prepare_sync_branch") as mock_prepare, \
             patch.object(sync, "_finish_sync_branch") as mock_finish, \
             patch.object(sync, "_create_or_update_pr") as mock_pr, \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="M skills/devops/docker-mgmt/SKILL.md", returncode=0)
            sync.push_skills(conn, "company-123")

        mock_prepare.assert_called_once()
        mock_finish.assert_called_once()
        mock_pr.assert_called_once()

    def test_push_no_pr_when_skipped(self, sync_repo, mock_db):
        sync, repo_dir = sync_repo
        _, conn, cur = mock_db

        cur.fetchall.return_value = [
            ("devops", "docker-mgmt", "# Docker"),
        ]

        with patch.object(sync, "_ensure_repo", return_value=True), \
             patch.object(sync, "_prepare_sync_branch"), \
             patch.object(sync, "_finish_sync_branch") as mock_finish, \
             patch.object(sync, "_create_or_update_pr") as mock_pr, \
             patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="", returncode=0)
            sync.push_skills(conn, "company-123")

        mock_finish.assert_not_called()
        mock_pr.assert_not_called()


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


class TestPrepareSyncBranch:
    def test_prepare_calls_correct_git_sequence(self, sync_repo):
        sync, repo_dir = sync_repo
        with patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            sync._prepare_sync_branch()

        calls = [c[0][0] for c in mock_git.call_args_list]
        assert calls[0] == "fetch"
        assert calls[1] == "checkout"
        assert calls[2] == "reset"
        assert calls[3] == "checkout"

    def test_prepare_creates_gitignore(self, sync_repo):
        sync, repo_dir = sync_repo
        with patch.object(sync, "_git"):
            sync._prepare_sync_branch()

        gitignore = repo_dir / "skills" / ".gitignore"
        assert gitignore.exists()
        assert ".manifests" in gitignore.read_text()


class TestFinishSyncBranch:
    def test_finish_force_pushes_and_checks_out_main(self, sync_repo):
        sync, repo_dir = sync_repo
        with patch.object(sync, "_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            sync._finish_sync_branch()

        calls = [c[0] for c in mock_git.call_args_list]
        assert calls[0][0] == "push"
        assert "--force" in calls[0]
        assert calls[1][0] == "checkout"
        assert calls[1][1] == "main"


class TestBuildPrBody:
    def test_lists_added_skills(self, sync):
        body = sync._build_pr_body({
            "added": [{"slug": "docker", "category": "devops", "description": "Manage Docker"}],
            "updated": [],
            "removed": [],
        })
        assert "**docker** (devops)" in body
        assert "Manage Docker" in body
        assert "### Added" in body

    def test_lists_removed_skills(self, sync):
        body = sync._build_pr_body({
            "added": [],
            "updated": [],
            "removed": [{"slug": "old-skill", "category": "general"}],
        })
        assert "**old-skill** (general)" in body
        assert "### Removed" in body

    def test_empty_changes(self, sync):
        body = sync._build_pr_body({"added": [], "updated": [], "removed": []})
        assert "No changes" in body

    def test_ollama_summary_prepended(self, sync):
        with patch.object(sync, "_generate_summary_with_ollama", return_value="Added 2 new skills."):
            body = sync._build_pr_body({
                "added": [{"slug": "a", "category": "x", "description": ""}],
                "updated": [],
                "removed": [],
            })
        assert body.startswith("Added 2 new skills.")
        assert "---" in body


class TestBuildPrTitle:
    def test_added_only(self):
        title = SkillGitSync._build_pr_title({"added": [{"slug": "a"}], "updated": [], "removed": []})
        assert "+1" in title

    def test_mixed_changes(self):
        title = SkillGitSync._build_pr_title({
            "added": [1, 2], "updated": [3], "removed": [4],
        })
        assert "+2" in title
        assert "~1" in title
        assert "-1" in title


class TestCreateOrUpdatePR:
    def test_creates_new_pr_with_changes(self, sync):
        changes = {
            "added": [{"slug": "docker", "category": "devops", "description": "Docker"}],
            "updated": [],
            "removed": [],
        }
        with patch.object(sync, "_git_api") as mock_api, \
             patch.object(sync, "_generate_summary_with_ollama", return_value=""):
            mock_api.side_effect = [
                [],
                {"number": 42},
            ]
            result = sync._create_or_update_pr({"pushed": 1, "removed": 0, "changes": changes})

        assert mock_api.call_count == 2
        post_call = mock_api.call_args_list[1]
        assert "POST" == post_call[0][0]
        assert result is not None
        json_body = post_call[0][2]
        assert "**docker**" in json_body["body"]

    def test_updates_existing_pr(self, sync):
        changes = {"added": [], "updated": [{"slug": "x", "category": "y"}], "removed": []}
        with patch.object(sync, "_git_api") as mock_api, \
             patch.object(sync, "_generate_summary_with_ollama", return_value=""):
            mock_api.side_effect = [
                [{"number": 7, "state": "open"}],
                {"number": 7, "state": "open"},
            ]
            sync._create_or_update_pr({"pushed": 1, "removed": 0, "changes": changes})

        patch_call = mock_api.call_args_list[1]
        assert "PATCH" == patch_call[0][0]
        assert "/repos/example/skills/pulls/7" in patch_call[0][1]

    def test_returns_none_when_no_repo_info(self):
        s = SkillGitSync(source_id="x", repo_url="not-a-valid-url", token="tok")
        with patch.object(s, "_git_api") as mock_api:
            result = s._create_or_update_pr({"pushed": 1, "removed": 0, "changes": {}})

        mock_api.assert_not_called()
        assert result is None


class TestSkillGitSyncErrors:
    def test_invalid_repo_url(self, tmp_path, mock_db):
        sync = SkillGitSync(
            source_id="x",
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
            source_id="x",
            repo_url="https://git.example.com/nonexistent/repo.git",
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
