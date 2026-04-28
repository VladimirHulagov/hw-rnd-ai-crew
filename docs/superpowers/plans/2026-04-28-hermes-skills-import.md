# Hermes Skills Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import hermes-agent skills into Paperclip `company_skills` DB so the CEO can manage skills per agent through the UI.

**Architecture:** Orchestrator scans hermes-agent skill directories, upserts each SKILL.md into `company_skills` table via direct SQL. Then reads agent's `paperclipSkillSync.desiredSkills` from `adapter_config` and creates symlinks in the agent's profile `skills/` dir. Remove `external_dirs` so agents only see Paperclip-managed skills.

**Tech Stack:** Python (orchestrator), PostgreSQL (company_skills), YAML frontmatter parsing

---

## Files

| Action | File | Purpose |
|--------|------|---------|
| Create | `hermes-gateway/orchestrator/skill_importer.py` | Scan dirs, parse SKILL.md, upsert into DB |
| Modify | `hermes-gateway/orchestrator/orchestrator.py` | Call importer + sync agent skills on provisioning |
| Modify | `hermes-gateway/config-template.yaml` | Remove `skills.external_dirs` |
| Update | `AGENTS.md` | Document new flow |

---

### Task 1: Create skill importer module

**Files:**
- Create: `hermes-gateway/orchestrator/skill_importer.py`

- [ ] **Step 1: Create skill_importer.py with scan and upsert logic**

```python
import json
import logging
import re
from pathlib import Path
from typing import Optional

import psycopg2

logger = logging.getLogger("gateway-orchestrator")

HERMES_SKILL_DIRS = [
    "/opt/hermes-agent/skills",
    "/opt/hermes-agent/optional-skills",
    "/opt/skills",
]


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from SKILL.md. Returns dict of fields."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    raw = m.group(1)
    out: dict = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val:
            out[key] = val
    return out


def scan_skill_dirs() -> list[dict]:
    """Scan all hermes skill dirs, return list of skill entries."""
    skills: list[dict] = []
    seen_slugs: set[str] = set()

    for base_dir in HERMES_SKILL_DIRS:
        base = Path(base_dir)
        if not base.is_dir():
            continue
        for skill_md in sorted(base.rglob("SKILL.md")):
            category_parts = skill_md.parent.relative_to(base).parts
            if len(category_parts) < 2:
                continue
            category = category_parts[0]
            slug = skill_md.parent.name

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
            })

    return skills


def upsert_skills_for_company(conn, company_id: str, skills: list[dict]) -> int:
    """Upsert skills into company_skills table. Returns count of upserted skills."""
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
                    'local_path', %s, 'markdown_only', 'compatible',
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
                skill["source_path"],
                json.dumps([{"path": "SKILL.md", "kind": "markdown"}]),
                json.dumps({"sourceKind": "hermes_bundled", "category": skill["category"]}),
            ))
            count += 1
    conn.commit()
    return count


def import_hermes_skills(conn) -> int:
    """Import hermes skills for all active companies. Returns total upserted count."""
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


def get_skill_source_path(conn, company_id: str, skill_key: str) -> Optional[str]:
    """Get source_path for a skill from company_skills metadata."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT source_locator FROM company_skills
            WHERE company_id = %s AND key = %s
        """, (company_id, skill_key))
        row = cur.fetchone()
        return row[0] if row else None
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/orchestrator/skill_importer.py
git commit -m "feat: add skill_importer module for hermes-agent skills"
```

---

### Task 2: Integrate skill import into orchestrator startup

**Files:**
- Modify: `hermes-gateway/orchestrator/orchestrator.py`

- [ ] **Step 1: Add import call in orchestrator `_run_cycle()`**

Find the `_run_cycle` method. After the agents are synced (after the loop that calls `_sync_agent`), add:

```python
from orchestrator.skill_importer import import_hermes_skills
from orchestrator.skill_importer import scan_skill_dirs, get_skill_source_path
```

At the top of `Orchestrator.__init__`, add:
```python
self._skills_imported = False
```

Inside `_run_cycle()`, at the beginning of the cycle (before the agent loop), add:
```python
if not self._skills_imported:
    try:
        n = import_hermes_skills(self.db_conn)
        logger.info("Hermes skills import complete: %d skills upserted", n)
        self._skills_imported = True
    except Exception as exc:
        logger.error("Failed to import hermes skills: %s", exc)
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "feat: call skill importer on orchestrator startup"
```

---

### Task 3: Sync enabled skills to agent profile via symlinks

**Files:**
- Modify: `hermes-gateway/orchestrator/orchestrator.py`

- [ ] **Step 1: Add `_sync_agent_skills()` method to Orchestrator class**

This method reads `paperclipSkillSync.desiredSkills` from `adapter_config`, resolves skill keys to source paths, and creates symlinks in the profile's `skills/` directory.

```python
def _sync_agent_skills(self, agent: dict, profile_dir: Path):
    adapter_config = agent.get("adapter_config", {})
    if isinstance(adapter_config, str):
        try:
            adapter_config = json.loads(adapter_config)
        except (json.JSONDecodeError, TypeError):
            adapter_config = {}

    sync_config = adapter_config.get("paperclipSkillSync", {})
    desired_keys = sync_config.get("desiredSkills", [])

    if not desired_keys:
        desired_keys = []

    company_id = agent.get("companyId", "")
    if not company_id:
        return

    skills_dir = profile_dir / "skills"
    if skills_dir.exists():
        for item in skills_dir.rglob("*"):
            if item.is_symlink():
                item.unlink()

    for key in desired_keys:
        source_path = get_skill_source_path(self.db_conn, company_id, key)
        if not source_path:
            continue
        source = Path(source_path)
        if not source.is_dir():
            continue

        category = source.parent.name if source.parent.name != source.parent.parent.name else "general"
        skill_name = source.name

        target = skills_dir / category / skill_name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(source)
        except OSError as exc:
            logger.warning("Failed to symlink skill %s: %s", skill_name, exc)
```

- [ ] **Step 2: Call `_sync_agent_skills` during agent provisioning**

In the `_sync_agent` method (or equivalent where the orchestrator sets up the agent), after writing config.yaml and SOUL.md, add:

```python
self._sync_agent_skills(agent, profile_dir)
```

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "feat: sync enabled skills to agent profile via symlinks"
```

---

### Task 4: Remove external_dirs from config

**Files:**
- Modify: `hermes-gateway/config-template.yaml`

- [ ] **Step 1: Remove skills.external_dirs section**

Remove the `skills:` block added in the previous change:

```yaml
# REMOVE this entire block:
skills:
  external_dirs:
    - /opt/hermes-agent/skills
    - /opt/hermes-agent/optional-skills
    - /opt/skills
```

The agents now load skills from their profile `skills/` directory (populated by symlinks from Task 3).

- [ ] **Step 2: Bump config version**

Change `_config_version: 10` to `_config_version: 11`

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/config-template.yaml
git commit -m "feat: remove external_dirs, skills loaded from profile only"
```

---

### Task 5: Deploy and verify

- [ ] **Step 1: Rebuild and restart hermes-gateway**

```bash
docker compose up -d --build hermes-gateway
```

Wait for orchestrator to complete pip install (~30s).

- [ ] **Step 2: Verify skills imported into DB**

```bash
docker exec paperclip-db psql -U paperclip -d paperclip -c "SELECT key, name, metadata->>'category' as cat FROM company_skills WHERE metadata->>'sourceKind' = 'hermes_bundled' AND company_id = '8291e5b4-49f7-4214-aede-a2db830a27b7' LIMIT 20;"
```

Expected: ~120 rows (73 bundled + 46 optional + 1 custom docker-management)

- [ ] **Step 3: Verify Paperclip UI shows skills**

Open Paperclip UI `/agents/sw-dev/skills` — should show all hermes skills.

- [ ] **Step 4: Verify agent profile has symlinks**

```bash
docker exec hermes-gateway ls -la /root/.hermes/profiles/d75fa50c-7213-4801-b04c-cf719ede5277/skills/
```

Expected: category directories with symlinks to source skill dirs.

- [ ] **Step 5: Verify agent sees skills**

Ask agent "Какие у тебя есть навыки?" via Telegram — should report skills matching the enabled set.

- [ ] **Step 6: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: hermes skills imported into Paperclip company_skills"
```
