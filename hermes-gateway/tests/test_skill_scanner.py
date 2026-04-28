import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from skill_scanner import (
    _mtime_hash,
    _parse_frontmatter,
    load_scanner_state,
    save_scanner_state,
    scan_agent_profiles,
    upsert_agent_created_skills,
)


def _make_skill(profile_dir, category, slug, content, is_symlink=False, target=None):
    skill_dir = profile_dir / "skills" / category / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    if is_symlink:
        target_path = profile_dir / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content)
        md.symlink_to(target_path)
    else:
        md.write_text(content)
    return md


class TestParseFrontmatter:
    def test_extracts_name_and_description(self):
        text = "---\nname: My Skill\ndescription: A test skill\n---\n# Content"
        result = _parse_frontmatter(text)
        assert result["name"] == "My Skill"
        assert result["description"] == "A test skill"

    def test_no_frontmatter(self):
        result = _parse_frontmatter("# Just content")
        assert result == {}

    def test_partial_frontmatter(self):
        text = "---\nname: Only Name\n---\n# Content"
        result = _parse_frontmatter(text)
        assert result["name"] == "Only Name"
        assert "description" not in result


class TestScanAgentProfiles:
    def _agent(self, aid="a-1", name="Agent One", cid="c-1"):
        return {aid: {"name": name, "companyId": cid}}

    def test_empty_profiles(self, tmp_path):
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        result = scan_agent_profiles(profiles, self._agent(), set(), {})
        assert result == []

    def test_finds_regular_file_skill(self, tmp_path):
        profiles = tmp_path / "profiles"
        aid = "a-1"
        profile = profiles / aid
        _make_skill(profile, "research", "web-search", "---\nname: Web Search\ndescription: Search\n---\n# Body")

        result = scan_agent_profiles(profiles, self._agent(aid), set(), {})
        assert len(result) == 1
        s = result[0]
        assert s["category"] == "research"
        assert s["slug"] == "web-search"
        assert s["name"] == "Web Search"
        assert s["description"] == "Search"
        assert s["author_agent_id"] == aid
        assert s["author_agent_name"] == "Agent One"
        assert s["company_id"] == "c-1"
        assert s["state_key"] == f"{aid}/research/web-search"

    def test_skips_symlink_skills(self, tmp_path):
        profiles = tmp_path / "profiles"
        aid = "a-1"
        profile = profiles / aid
        _make_skill(
            profile, "devops", "docker",
            "---\nname: Docker\ndescription: test\n---\n# Docker",
            is_symlink=True, target="originals/docker/SKILL.md",
        )
        result = scan_agent_profiles(profiles, self._agent(aid), set(), {})
        assert result == []

    def test_skips_bundled_slugs(self, tmp_path):
        profiles = tmp_path / "profiles"
        aid = "a-1"
        profile = profiles / aid
        _make_skill(profile, "coding", "python-dev", "---\nname: Py\ndescription: x\n---\n# Py")

        result = scan_agent_profiles(profiles, self._agent(aid), {"python-dev"}, {})
        assert result == []

    def test_skips_unknown_agents(self, tmp_path):
        profiles = tmp_path / "profiles"
        aid = "a-1"
        profile = profiles / aid
        _make_skill(profile, "general", "my-skill", "---\nname: X\n---\n# X")

        result = scan_agent_profiles(profiles, {"other-agent": {"name": "O", "companyId": "c-2"}}, set(), {})
        assert result == []

    def test_skips_unchanged_mtime(self, tmp_path):
        profiles = tmp_path / "profiles"
        aid = "a-1"
        profile = profiles / aid
        md = _make_skill(profile, "general", "my-skill", "---\nname: X\n---\n# X")

        h = _mtime_hash(md)
        state = {f"{aid}/general/my-skill": h}

        result = scan_agent_profiles(profiles, self._agent(aid), set(), state)
        assert result == []

    def test_detects_updated_skill(self, tmp_path):
        profiles = tmp_path / "profiles"
        aid = "a-1"
        profile = profiles / aid
        md = _make_skill(profile, "general", "my-skill", "---\nname: X\n---\n# X")

        state = {f"{aid}/general/my-skill": "old_hash"}

        result = scan_agent_profiles(profiles, self._agent(aid), set(), state)
        assert len(result) == 1
        assert result[0]["mtime_hash"] == _mtime_hash(md)

    def test_multi_file_skill_only_reads_skill_md(self, tmp_path):
        profiles = tmp_path / "profiles"
        aid = "a-1"
        profile = profiles / aid
        skill_dir = profile / "skills" / "general" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: X\n---\n# X")
        refs = skill_dir / "references"
        refs.mkdir()
        (refs / "api.md").write_text("# API")

        result = scan_agent_profiles(profiles, self._agent(aid), set(), {})
        assert len(result) == 1
        assert result[0]["slug"] == "my-skill"

    def test_multiple_agents_multiple_skills(self, tmp_path):
        profiles = tmp_path / "profiles"
        agents = {
            "a-1": {"name": "Agent 1", "companyId": "c-1"},
            "a-2": {"name": "Agent 2", "companyId": "c-2"},
        }
        for aid in agents:
            _make_skill(profiles / aid, "general", "skill-x", "---\nname: X\n---\n# X")
            _make_skill(profiles / aid, "devops", "skill-y", "---\nname: Y\n---\n# Y")

        result = scan_agent_profiles(profiles, agents, set(), {})
        assert len(result) == 4


class TestUpsertAgentCreatedSkills:
    def test_inserts_new_skill(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)

        skills = [{
            "category": "research",
            "slug": "web-search",
            "name": "Web Search",
            "description": "Search the web",
            "markdown": "---\nname: Web Search\n---\n# Content",
            "author_agent_id": "a-1",
            "author_agent_name": "Agent One",
            "company_id": "c-1",
            "mtime_hash": "12345:100",
            "state_key": "a-1/research/web-search",
        }]

        count = upsert_agent_created_skills(conn, "c-1", skills)
        assert count == 1
        cursor.execute.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert "INSERT INTO company_skills" in sql
        assert "ON CONFLICT" in sql

    def test_upsert_correct_key_format(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)

        skills = [{
            "category": "devops",
            "slug": "my-tool",
            "name": "My Tool",
            "description": "",
            "markdown": "# My Tool",
            "author_agent_id": "agent-42",
            "author_agent_name": "Bot",
            "company_id": "c-1",
            "mtime_hash": "x:y",
            "state_key": "agent-42/devops/my-tool",
        }]

        upsert_agent_created_skills(conn, "c-1", skills)
        args = cursor.execute.call_args[0][1]
        assert args[1] == "agent/agent-42/devops/my-tool"

    def test_upsert_metadata_sourceKind(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)

        skills = [{
            "category": "general",
            "slug": "s1",
            "name": "S1",
            "description": "desc",
            "markdown": "# S1",
            "author_agent_id": "a1",
            "author_agent_name": "Alpha",
            "company_id": "c1",
            "mtime_hash": "h",
            "state_key": "a1/general/s1",
        }]

        upsert_agent_created_skills(conn, "c1", skills)
        args = cursor.execute.call_args[0][1]
        metadata = json.loads(args[7])
        assert metadata["sourceKind"] == "agent_created"
        assert metadata["authorAgentId"] == "a1"
        assert metadata["authorAgentName"] == "Alpha"
        assert metadata["category"] == "general"


class TestScannerState:
    def test_load_state_missing_file(self, tmp_path):
        result = load_scanner_state(tmp_path / "nonexistent.json")
        assert result == {}

    def test_save_and_load_state(self, tmp_path):
        path = tmp_path / "state.json"
        state = {"a-1/general/skill": "12345:200", "a-2/devops/tool": "67890:300"}
        save_scanner_state(path, state)
        loaded = load_scanner_state(path)
        assert loaded == state

    def test_load_state_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json{{{")
        result = load_scanner_state(path)
        assert result == {}
