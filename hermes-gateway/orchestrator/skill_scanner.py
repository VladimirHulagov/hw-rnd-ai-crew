from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("gateway-orchestrator")


def _parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip().strip("'\"")
        if val and val != "|":
            out[key] = val
    return out


def _mtime_hash(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime}:{st.st_size}"


def scan_agent_profiles(
    profiles_root: Path,
    known_agents: dict[str, dict],
    bundled_slugs: set[str],
    state: dict,
) -> list[dict]:
    results: list[dict] = []
    if not profiles_root.is_dir():
        return results

    for agent_dir in sorted(profiles_root.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_id = agent_dir.name
        if agent_id not in known_agents:
            continue
        agent_info = known_agents[agent_id]
        skills_dir = agent_dir / "skills"
        if not skills_dir.is_dir():
            continue

        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            if skill_md.is_symlink():
                continue

            rel = skill_md.parent.relative_to(skills_dir)
            parts = rel.parts
            if len(parts) < 2:
                continue

            category = parts[0]
            slug = parts[-1]

            if slug in bundled_slugs:
                continue

            state_key = f"{agent_id}/{category}/{slug}"
            mh = _mtime_hash(skill_md)
            if state.get(state_key) == mh:
                continue

            text = skill_md.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)

            results.append({
                "category": category,
                "slug": slug,
                "name": fm.get("name", slug),
                "description": fm.get("description", ""),
                "markdown": text,
                "author_agent_id": agent_id,
                "author_agent_name": agent_info["name"],
                "company_id": agent_info["companyId"],
                "mtime_hash": mh,
                "state_key": state_key,
            })

    return results


def upsert_agent_created_skills(conn, company_id: str, skills: list[dict]) -> int:
    count = 0
    with conn.cursor() as cur:
        for skill in skills:
            key = f"agent/{skill['author_agent_id']}/{skill['category']}/{skill['slug']}"
            cur.execute("""
                INSERT INTO company_skills (
                    id, company_id, key, slug, name, description, markdown,
                    source_type, source_locator, trust_level, compatibility,
                    file_inventory, metadata, hidden
                ) VALUES (
                    gen_random_uuid(), %s, %s, %s, %s, %s, %s,
                    'catalog', NULL, 'markdown_only', 'compatible',
                    %s::jsonb, %s::jsonb, false
                )
                ON CONFLICT (company_id, key) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    markdown = EXCLUDED.markdown,
                    source_locator = EXCLUDED.source_locator,
                    file_inventory = EXCLUDED.file_inventory,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
            """, (
                company_id,
                key,
                skill["slug"],
                skill["name"],
                skill["description"],
                skill["markdown"],
                json.dumps([{"path": "SKILL.md", "kind": "markdown"}]),
                json.dumps({
                    "sourceKind": "agent_created",
                    "category": skill["category"],
                    "authorAgentId": skill["author_agent_id"],
                    "authorAgentName": skill["author_agent_name"],
                }),
            ))
            count += 1
    conn.commit()
    return count


def load_scanner_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_scanner_state(state_path: Path, state: dict):
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
