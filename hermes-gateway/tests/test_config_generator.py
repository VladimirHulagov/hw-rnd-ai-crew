from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import config_generator
from config_generator import generate_profile_config, ensure_profile_dirs


class TestGenerateProfileConfig:
    def _write_template(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "config-template.yaml"
        p.write_text(content)
        return p

    def test_basic_field_substitution(self, tmp_path):
        template = """
agent:
  name: ${agent_name}
  model: ${model}
"""
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8642,
                agent_name="TestBot",
            )
        assert "glm-5.1" in result

    def test_port_in_platforms_section(self, tmp_path):
        template = "$platforms_section\n"
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8650,
            )
        assert "8650" in result

    def test_telegram_section_included(self, tmp_path):
        template = "$platforms_section\n"
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8642,
                telegram_bot_token="123456:ABC",
                telegram_chat_id="789",
            )
        assert "123456:ABC" in result
        assert "789" in result

    def test_telegram_section_absent(self, tmp_path):
        template = "$platforms_section\n"
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8642,
            )
        assert "telegram" not in result.lower()

    def test_paperclip_api_key_substitution(self, tmp_path):
        template = """
paperclip:
  api_key: ${paperclip_api_key}
"""
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8642,
                paperclip_api_key="pcp_test_key",
            )
        assert "pcp_test_key" in result


class TestEnsureProfileDirs:
    def test_creates_subdirectories(self, tmp_path):
        profile = tmp_path / "agent-1"
        ensure_profile_dirs(profile)
        assert (profile / "memories").is_dir()
        assert (profile / "skills").is_dir()
        assert (profile / "sessions").is_dir()

    def test_idempotent(self, tmp_path):
        profile = tmp_path / "agent-1"
        ensure_profile_dirs(profile)
        ensure_profile_dirs(profile)
        assert (profile / "memories").is_dir()
