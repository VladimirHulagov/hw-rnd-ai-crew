import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

from orchestrator import orchestrator


class TestFetchAgentsFromDb:
    def test_returns_active_hermes_agents(self, mock_db, mock_env):
        mock_connect, conn, cursor = mock_db
        cursor.fetchall.return_value = [
            {
                "id": "a1",
                "name": "Agent1",
                "role": "worker",
                "company_id": "c1",
                "adapter_config": {},
            }
        ]
        result = orchestrator.fetch_agents_from_db()
        assert len(result) == 1
        assert result[0]["id"] == "a1"
        assert result[0]["name"] == "Agent1"

    def test_excludes_terminated(self, mock_db, mock_env):
        mock_connect, conn, cursor = mock_db
        cursor.fetchall.return_value = []
        result = orchestrator.fetch_agents_from_db()
        assert result == []

    def test_excludes_non_hermes_adapter(self, mock_db, mock_env):
        mock_connect, conn, cursor = mock_db
        cursor.fetchall.return_value = []
        result = orchestrator.fetch_agents_from_db()
        assert result == []


class TestReadPaperclipInstructions:
    def test_reads_agents_md(self, tmp_path, mock_env):
        instr_dir = (
            tmp_path
            / "paperclip"
            / "instances"
            / "default"
            / "companies"
            / "c1"
            / "agents"
            / "a1"
            / "instructions"
        )
        instr_dir.mkdir(parents=True)
        (instr_dir / "AGENTS.md").write_text("# Agent instructions")

        with patch.object(orchestrator, "PAPERCLIP_DATA_PATH", str(tmp_path / "paperclip")):
            result = orchestrator._read_paperclip_instructions("a1", "c1")
        assert result == "# Agent instructions"

    def test_fallback_to_instructions_md(self, tmp_path, mock_env):
        instr_dir = (
            tmp_path
            / "paperclip"
            / "instances"
            / "default"
            / "companies"
            / "c1"
            / "agents"
            / "a1"
            / "instructions"
        )
        instr_dir.mkdir(parents=True)
        (instr_dir / "instructions.md").write_text("Fallback instructions")

        with patch.object(orchestrator, "PAPERCLIP_DATA_PATH", str(tmp_path / "paperclip")):
            result = orchestrator._read_paperclip_instructions("a1", "c1")
        assert result == "Fallback instructions"

    def test_fallback_to_soul_md(self, tmp_path, mock_env):
        instr_dir = (
            tmp_path
            / "paperclip"
            / "instances"
            / "default"
            / "companies"
            / "c1"
            / "agents"
            / "a1"
            / "instructions"
        )
        instr_dir.mkdir(parents=True)
        (instr_dir / "SOUL.md").write_text("Soul content")

        with patch.object(orchestrator, "PAPERCLIP_DATA_PATH", str(tmp_path / "paperclip")):
            result = orchestrator._read_paperclip_instructions("a1", "c1")
        assert result == "Soul content"

    def test_returns_none_when_no_files(self, tmp_path, mock_env):
        instr_dir = (
            tmp_path
            / "paperclip"
            / "instances"
            / "default"
            / "companies"
            / "c1"
            / "agents"
            / "a1"
            / "instructions"
        )
        instr_dir.mkdir(parents=True)

        with patch.object(orchestrator, "PAPERCLIP_DATA_PATH", str(tmp_path / "paperclip")):
            result = orchestrator._read_paperclip_instructions("a1", "c1")
        assert result is None


class TestBuildSoulMd:
    def test_includes_name_and_platform(self):
        result = orchestrator._build_soul_md(role="worker", name="TestBot")
        assert "TestBot" in result
        assert "Paperclip" in result

    def test_docker_disabled_by_default(self):
        result = orchestrator._build_soul_md(role="worker", name="TestBot")
        assert "docker" not in result.lower() or "docker-guard" not in result

    def test_docker_enabled(self):
        result = orchestrator._build_soul_md(
            role="worker", name="TestBot", enable_docker=True
        )
        assert "docker" in result.lower()


class TestHotReload:
    def test_compute_fingerprint_returns_string(self):
        result = orchestrator._compute_source_fingerprint()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fingerprint_is_deterministic(self):
        result1 = orchestrator._compute_source_fingerprint()
        result2 = orchestrator._compute_source_fingerprint()
        assert result1 == result2
