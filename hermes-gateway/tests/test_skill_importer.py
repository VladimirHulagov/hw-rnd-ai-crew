import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from skill_importer import scan_skill_dirs, _parse_frontmatter


class TestParseFrontmatter:
    def test_extracts_name_and_description(self):
        text = """---
name: Docker Management
description: Manage Docker containers
---
# Skill content here"""
        result = _parse_frontmatter(text)
        assert result["name"] == "Docker Management"
        assert result["description"] == "Manage Docker containers"

    def test_no_frontmatter(self):
        text = "# Just a heading\nSome content"
        result = _parse_frontmatter(text)
        assert result == {}

    def test_partial_frontmatter(self):
        text = """---
name: Only Name
---
Content"""
        result = _parse_frontmatter(text)
        assert result["name"] == "Only Name"
        assert "description" not in result


class TestScanSkillDirs:
    def test_discovers_skills_from_all_dirs(self, tmp_path):
        custom_dir = tmp_path / "custom" / "devops" / "docker-mgmt"
        custom_dir.mkdir(parents=True)
        (custom_dir / "SKILL.md").write_text(
            "---\nname: Docker\ndescription: Docker management\n---\n# Docker skill"
        )

        builtin_dir = tmp_path / "builtin" / "coding" / "python-dev"
        builtin_dir.mkdir(parents=True)
        (builtin_dir / "SKILL.md").write_text(
            "---\nname: Python Dev\ndescription: Python development\n---\n# Python skill"
        )

        dirs = [
            (str(custom_dir.parent.parent), "Project skills"),
            (str(builtin_dir.parent.parent), "Hermes Agent"),
        ]
        with patch("skill_importer.HERMES_SKILL_DIRS", dirs):
            skills = scan_skill_dirs()

        assert len(skills) == 2
        slugs = {s["slug"] for s in skills}
        assert "docker-mgmt" in slugs
        assert "python-dev" in slugs

    def test_custom_skill_priority(self, tmp_path):
        for label, subdir in [("custom", "custom"), ("builtin", "builtin")]:
            d = tmp_path / subdir / "devops" / "docker-mgmt"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                f"---\nname: Docker ({label})\ndescription: test\n---\n# {label}"
            )

        dirs = [
            (str(tmp_path / "custom"), "Project skills"),
            (str(tmp_path / "builtin"), "Hermes Agent"),
        ]
        with patch("skill_importer.HERMES_SKILL_DIRS", dirs):
            skills = scan_skill_dirs()

        docker_skill = [s for s in skills if s["slug"] == "docker-mgmt"]
        assert len(docker_skill) == 1
        assert "custom" in docker_skill[0]["name"]

    def test_empty_dirs(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch("skill_importer.HERMES_SKILL_DIRS", [(str(empty_dir), "Empty")]):
            skills = scan_skill_dirs()
        assert skills == []

    def test_skips_dirs_without_skill_md(self, tmp_path):
        d = tmp_path / "skills" / "devops" / "broken"
        d.mkdir(parents=True)
        with patch(
            "skill_importer.HERMES_SKILL_DIRS",
            [(str(tmp_path / "skills"), "Test")],
        ):
            skills = scan_skill_dirs()
        assert skills == []

    def test_extracts_category_and_source_label(self, tmp_path):
        d = tmp_path / "skills" / "devops" / "docker"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: Docker\ndescription: test\n---\n# Docker"
        )
        with patch(
            "skill_importer.HERMES_SKILL_DIRS",
            [(str(tmp_path / "skills"), "Project skills")],
        ):
            skills = scan_skill_dirs()
        assert skills[0]["category"] == "devops"
        assert skills[0]["source_label"] == "Project skills"
