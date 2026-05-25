import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from skill_sync_mcp import SkillSyncServer


class TestSkillSyncServerInit:
    def test_init_reads_env_vars(self):
        with patch.dict("os.environ", {
            "FORGEJO_URL": "https://git.example.com",
            "FORGEJO_TOKEN": "tok123",
            "FORGEJO_OWNER": "myorg",
            "FORGEJO_REPO": "myskills",
        }):
            from skill_sync_mcp import FORGEJO_URL, FORGEJO_TOKEN, FORGEJO_OWNER, FORGEJO_REPO
            srv = SkillSyncServer()
            assert srv.forgejo_url == "https://git.example.com"
            assert srv.forgejo_token == "tok123"
            assert srv.forgejo_owner == "myorg"
            assert srv.forgejo_repo == "myskills"

    def test_init_defaults(self):
        srv = SkillSyncServer()
        assert isinstance(srv.forgejo_url, str)
        assert isinstance(srv.forgejo_token, str)
        assert isinstance(srv.profiles_dir, Path)


class TestListTools:
    def test_list_tools_returns_three(self):
        from skill_sync_mcp import list_tools
        import asyncio
        tools = asyncio.get_event_loop().run_until_complete(list_tools())
        assert len(tools) == 3
        names = [t.name for t in tools]
        assert "skill_push" in names
        assert "skill_pull" in names
        assert "skill_list_remote" in names


class TestParseConflicts:
    def test_extracts_conflict_sections(self):
        srv = SkillSyncServer()
        output = (
            "CONFLICT (content): Merge conflict in skills/devops/docker/SKILL.md\n"
            "<<<<<<< HEAD\n"
            "line from ours\n"
            "=======\n"
            "line from theirs\n"
            ">>>>>>> skills-sync/abc123\n"
        )
        conflicts = srv._parse_conflicts(output)
        assert len(conflicts) == 1
        assert conflicts[0]["path"] == "skills/devops/docker/SKILL.md"
        assert "line from ours" in conflicts[0]["yours"]
        assert "line from theirs" in conflicts[0]["theirs"]

    def test_no_conflicts(self):
        srv = SkillSyncServer()
        assert srv._parse_conflicts("Already up to date.") == []

    def test_multiple_conflicts(self):
        srv = SkillSyncServer()
        output = (
            "CONFLICT (content): Merge conflict in skills/a/b/SKILL.md\n"
            "<<<<<<< HEAD\n"
            "a1\n"
            "=======\n"
            "b1\n"
            ">>>>>>> branch\n"
            "CONFLICT (content): Merge conflict in skills/c/d/SKILL.md\n"
            "<<<<<<< HEAD\n"
            "a2\n"
            "=======\n"
            "b2\n"
            ">>>>>>> branch\n"
        )
        conflicts = srv._parse_conflicts(output)
        assert len(conflicts) == 2
        assert conflicts[0]["path"] == "skills/a/b/SKILL.md"
        assert conflicts[1]["path"] == "skills/c/d/SKILL.md"


class TestResolveRepoUrl:
    def test_override_url(self):
        srv = SkillSyncServer()
        assert srv._resolve_repo_url("https://custom.url/repo.git") == "https://custom.url/repo.git"

    def test_construct_from_env(self):
        srv = SkillSyncServer()
        srv.forgejo_url = "https://git.example.com"
        srv.forgejo_owner = "myorg"
        srv.forgejo_repo = "skills"
        assert srv._resolve_repo_url() == "https://git.example.com/myorg/skills.git"

    def test_no_config(self):
        srv = SkillSyncServer()
        srv.forgejo_url = ""
        srv.forgejo_owner = ""
        assert srv._resolve_repo_url() == ""


class TestSkillPush:
    def test_returns_error_for_missing_profile_dir(self, tmp_path):
        srv = SkillSyncServer()
        srv.profiles_dir = tmp_path
        srv.forgejo_url = "https://git.example.com"
        srv.forgejo_owner = "org"
        srv.forgejo_repo = "skills"
        with patch.object(srv, "_ensure_repo", return_value=True):
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir()
            (repo_dir / ".git").mkdir()
            srv._repo_dir = lambda aid: repo_dir
            result = srv.push("nonexistent-agent")
        assert "error" in result
        assert "No skills directory" in result["error"]

    def test_push_no_repo_url(self):
        srv = SkillSyncServer()
        srv.forgejo_url = ""
        srv.forgejo_owner = ""
        result = srv.push("some-agent")
        assert "error" in result


class TestSkillPull:
    def test_pull_no_repo_url(self):
        srv = SkillSyncServer()
        srv.forgejo_url = ""
        srv.forgejo_owner = ""
        result = srv.pull("some-agent")
        assert "error" in result

    def test_pull_returns_empty_for_no_skills_dir(self, tmp_path):
        srv = SkillSyncServer()
        srv.forgejo_url = "https://git.example.com"
        srv.forgejo_owner = "org"
        srv.forgejo_repo = "skills"
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        with patch.object(srv, "_ensure_repo", return_value=True), \
             patch.object(srv, "_repo_dir", return_value=repo_dir), \
             patch.object(srv, "_git", return_value=subprocess.CompletedProcess([], 0, "", "")):
            result = srv.pull("agent-1")
        assert result["imported"] == 0
        assert result["skills"] == []


class TestSkillListRemote:
    def test_no_repo_url(self):
        srv = SkillSyncServer()
        srv.forgejo_url = ""
        result = srv.list_remote()
        assert "error" in result

    def test_api_returns_skills(self):
        srv = SkillSyncServer()
        srv.forgejo_url = "https://git.example.com"
        srv.forgejo_owner = "org"
        srv.forgejo_repo = "skills"
        srv.forgejo_token = "tok"
        import base64
        skill_content = base64.b64encode(b"---\nname: Test Skill\ndescription: A test\n---\nBody").decode()
        with patch.object(srv, "_forgejo_api") as mock_api:
            mock_api.side_effect = [
                [{"name": "devops", "type": "dir", "path": "skills/devops"}],
                [{"name": "docker", "type": "dir", "path": "skills/devops/docker"}],
                {"content": skill_content},
            ]
            result = srv.list_remote()
        assert len(result["skills"]) == 1
        assert result["skills"][0]["slug"] == "docker"
        assert result["skills"][0]["name"] == "Test Skill"


class TestForgejoApi:
    def test_uses_token_auth_header(self):
        srv = SkillSyncServer()
        srv.forgejo_url = "https://git.example.com"
        srv.forgejo_token = "mytoken"
        with patch("skill_sync_mcp.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"id": 1}
            mock_client.request.return_value = mock_resp
            srv._forgejo_api("GET", "/api/v1/repos/org/repo/pulls")
            mock_client.request.assert_called_once()
            call_args = mock_client.request.call_args
            headers = call_args.kwargs.get("headers") or call_args[1].get("headers")
            assert headers["Authorization"] == "token mytoken"
