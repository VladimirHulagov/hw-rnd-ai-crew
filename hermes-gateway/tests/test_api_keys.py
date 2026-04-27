import json
from pathlib import Path
from unittest.mock import patch

from orchestrator import orchestrator


def _make_keys_file(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "agent_api_keys.json"
    p.write_text(json.dumps(data))
    return p


class TestLoadAgentApiKeys:
    def test_valid_json(self, tmp_path):
        keys = {"agent-1": "pcp_key_abc123", "agent-2": "pcp_key_def456"}
        path = _make_keys_file(tmp_path, keys)
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == keys

    def test_missing_file(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == {}

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{invalid json!!!")
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == {}

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("")
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == {}

    def test_empty_dict(self, tmp_path):
        path = _make_keys_file(tmp_path, {})
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == {}
