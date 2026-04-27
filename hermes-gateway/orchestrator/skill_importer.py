from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("gateway-orchestrator")

HERMES_SKILL_DIRS = [
    ("/opt/skills", "Project skills"),
    ("/opt/hermes-agent/skills", "Hermes Agent"),
    ("/opt/hermes-agent/optional-skills", "Hermes Agent (optional)"),
]


def _parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    raw = m.group(1)
    out: dict = {}
    in_list = False
    list_key = ""
    list_vals: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if in_list and stripped.startswith("- "):
            list_vals.append(stripped[2:].strip("'\""))
            continue
        if in_list and list_key:
            out[list_key] = list_vals
            in_list = False
            list_key = ""
            list_vals = []
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if not val or val == "|":
            continue
        if val.startswith("[") and val.endswith("]"):
            out[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        else:
            val = val.strip("'\"")
            if val:
                out[key] = val
    if in_list and list_key:
        out[list_key] = list_vals
    return out


def scan_skill_dirs() -> list[dict]:
    skills: list[dict] = []
    seen_slugs: set[str] = set()

    for base_dir, source_label in HERMES_SKILL_DIRS:
        base = Path(base_dir)
        if not base.is_dir():
            continue
        for skill_md in sorted(base.rglob("SKILL.md")):
            rel = skill_md.parent.relative_to(base)
            parts = rel.parts
            if len(parts) < 2:
                continue
            category = parts[0]
            slug = parts[-1]

            text = skill_md.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            name = fm.get("name", slug)
            description = fm.get("description", "")

            if slug in seen_slugs:
                logger.debug("Skipping duplicate skill slug: %s", slug)
                continue
            seen_slugs.add(slug)

            skills.append({
                "category": category,
                "slug": slug,
                "name": name,
                "description": description,
                "markdown": text,
                "source_path": str(skill_md.parent),
                "source_label": source_label,
            })

    return skills


def upsert_skills_for_company(conn, company_id: str, skills: list[dict]) -> int:
    count = 0
    with conn.cursor() as cur:
        for skill in skills:
            key = f"hermes/hermes-agent/{skill['category']}/{skill['slug']}"
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
                    "sourceKind": "hermes_bundled",
                    "category": skill["category"],
                    "sourcePath": skill["source_path"],
                    "sourceLabel": skill["source_label"],
                }),
            ))
            count += 1
    conn.commit()
    return count


def import_hermes_skills(conn) -> int:
    skills = scan_skill_dirs()
    if not skills:
        logger.warning("No hermes skills found in %s", HERMES_SKILL_DIRS)
        return 0

    logger.info("Found %d hermes skills to import", len(skills))

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM companies")
        company_ids = [row[0] for row in cur.fetchall()]

    total = 0
    for cid in company_ids:
        n = upsert_skills_for_company(conn, cid, skills)
        total += n
        logger.info("Imported %d skills for company %s", n, cid[:8])

    return total


def get_skill_info(conn, company_id: str, skill_key: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT source_locator, markdown, slug, metadata
            FROM company_skills
            WHERE company_id = %s AND key = %s
        """, (company_id, skill_key))
        row = cur.fetchone()
        if not row:
            return None
        meta = row[3] if row[3] else {}
        return {
            "source_locator": meta.get("sourcePath", row[0]),
            "markdown": row[1],
            "slug": row[2],
            "category": meta.get("category", "general"),
        }
