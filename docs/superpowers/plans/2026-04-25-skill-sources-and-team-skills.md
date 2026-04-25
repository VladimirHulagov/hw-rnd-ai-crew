# Skill Sources Management & Team Skills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add source grouping with hide/remove to the Company Skills page, and a "Team Skills" virtual source showing agent skills from Hermes profiles.

**Architecture:** Orchestrator exposes a Team Skills HTTP API (port 8681) reading from `/root/.hermes/profiles/*/skills/`. Paperclip-server proxies requests to it. UI groups company skills by source type → source locator in a tree view, with hide/remove actions. Team Skills appear as a special "team" source group.

**Tech Stack:** FastAPI (orchestrator team skills API), Express proxy (paperclip-server), React (UI), Drizzle/PostgreSQL (hidden_sources), Supervisor (process management)

---

## File Structure

### New files:
- `hermes-gateway/orchestrator/team_skills_api.py` — FastAPI server (port 8681), reads agent skill files from hermes profiles
- `paperclip/packages/db/src/migrations/0053_hidden_sources.sql` — adds `hidden_sources` JSONB column to `companies`
- `paperclip/packages/shared/src/types/team-skill.ts` — TeamSkill and TeamSkillDetail types

### Modified files:
- `hermes-gateway/supervisord.conf` — add `team-skills-api` supervisor program
- `docker-compose.yml` — add `TEAM_SKILLS_API_KEY` env var to both `hermes-gateway` and `paperclip-server`
- `paperclip/packages/db/src/schema/companies.ts` — add `hiddenSources` column
- `paperclip/packages/shared/src/types/company-skill.ts` — add `"team"` to `CompanySkillSourceType`
- `paperclip/packages/shared/src/types/index.ts` — export new team-skill types
- `paperclip/server/src/services/company-skills.ts` — add `deleteBySource()` method
- `paperclip/server/src/routes/company-skills.ts` — add hidden-sources routes, delete-by-source route, team-skills proxy routes
- `paperclip/ui/src/api/companySkills.ts` — add API client methods for hidden sources, delete by source, and team skills
- `paperclip/ui/src/lib/queryKeys.ts` — add query keys for team skills and hidden sources
- `paperclip/ui/src/pages/CompanySkills.tsx` — replace flat SkillList with source-grouped tree view + Team Skills section

---

### Task 1: DB migration — hidden_sources column

**Files:**
- Create: `paperclip/packages/db/src/migrations/0053_hidden_sources.sql`
- Modify: `paperclip/packages/db/src/schema/companies.ts:1-33`

- [ ] **Step 1: Create migration SQL**

```sql
-- paperclip/packages/db/src/migrations/0053_hidden_sources.sql
ALTER TABLE "companies" ADD COLUMN "hidden_sources" jsonb DEFAULT '[]';
```

- [ ] **Step 2: Add hiddenSources to Drizzle schema**

In `paperclip/packages/db/src/schema/companies.ts`, add after the `budgetMetric` line (line 26):

```typescript
hiddenSources: jsonb("hidden_sources").default([]),
```

Add `jsonb` to the import from `drizzle-orm/pg-core` on line 1:

```typescript
import { pgTable, uuid, text, integer, timestamp, boolean, jsonb, uniqueIndex } from "drizzle-orm/pg-core";
```

- [ ] **Step 3: Run migration in container**

```bash
docker exec paperclip-server node -e "
const { drizzle } = require('drizzle-orm/node-postgres');
const { Pool } = require('pg');
const pool = new Pool({ connectionString: 'postgres://paperclip:paperclip@paperclip-db:5432/paperclip' });
pool.query(\"ALTER TABLE companies ADD COLUMN IF NOT EXISTS hidden_sources jsonb DEFAULT '[]'\").then(() => { console.log('Migration applied'); pool.end(); });
"
```

Expected: `Migration applied`

- [ ] **Step 4: Commit**

```bash
git add paperclip/packages/db/src/migrations/0053_hidden_sources.sql paperclip/packages/db/src/schema/companies.ts
git commit -m "feat(db): add hidden_sources column to companies table"
```

---

### Task 2: Shared types — TeamSkill types and updated CompanySkillSourceType

**Files:**
- Create: `paperclip/packages/shared/src/types/team-skill.ts`
- Modify: `paperclip/packages/shared/src/types/company-skill.ts:1` (add `"team"` to source types)
- Modify: `paperclip/packages/shared/src/types/index.ts` (export new types)

- [ ] **Step 1: Create TeamSkill types**

```typescript
// paperclip/packages/shared/src/types/team-skill.ts
export interface TeamSkill {
  agentId: string;
  agentName: string;
  category: string;
  skillName: string;
  path: string;
  description: string;
  tags: string[];
  version: string;
  fileCount: number;
  modifiedAt: string;
}

export interface TeamSkillDetail extends TeamSkill {
  markdown: string;
  files: { path: string; kind: string }[];
}
```

- [ ] **Step 2: Add "team" to CompanySkillSourceType**

In `paperclip/packages/shared/src/types/company-skill.ts`, change line 1:

```typescript
export type CompanySkillSourceType = "local_path" | "github" | "url" | "catalog" | "skills_sh" | "team";
```

- [ ] **Step 3: Export new types from index**

In `paperclip/packages/shared/src/types/index.ts`, add after the `adapter-skills.js` export block (after line 43):

```typescript
export type {
  TeamSkill,
  TeamSkillDetail,
} from "./team-skill.js";
```

- [ ] **Step 4: Commit**

```bash
git add paperclip/packages/shared/src/types/team-skill.ts paperclip/packages/shared/src/types/company-skill.ts paperclip/packages/shared/src/types/index.ts
git commit -m "feat(shared): add TeamSkill types and 'team' source type"
```

---

### Task 3: Orchestrator Team Skills API

**Files:**
- Create: `hermes-gateway/orchestrator/team_skills_api.py`
- Modify: `hermes-gateway/supervisord.conf` (add program)
- Modify: `docker-compose.yml` (add env vars)

- [ ] **Step 1: Create the FastAPI team skills API**

```python
# hermes-gateway/orchestrator/team_skills_api.py
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

PROFILES_DIR = Path("/root/.hermes/profiles")
DB_URL = os.environ.get("DATABASE_URL", "")
API_KEY = os.environ.get("TEAM_SKILLS_API_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("team-skills-api")

app = FastAPI(title="Team Skills API")


def _check_auth(authorization: str = Header(default="")):
    if not API_KEY:
        return
    token = authorization.replace("Bearer ", "").strip()
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _get_agent_name(agent_id: str) -> str:
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT name FROM agents WHERE id = %s", (agent_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else agent_id[:8]
    except Exception:
        return agent_id[:8]


def _parse_frontmatter(content: str) -> dict:
    if not content.startswith("---\n"):
        return {}
    closing = content.find("\n---\n", 4)
    if closing < 0:
        return {}
    fm_text = content[4:closing]
    result = {}
    for line in fm_text.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [t.strip().strip("'\"") for t in val[1:-1].split(",")]
            result[key] = items
        elif val.startswith('"') and val.endswith('"'):
            result[key] = val[1:-1]
        else:
            result[key] = val
    return result


def _scan_skills():
    skills = []
    if not PROFILES_DIR.exists():
        return skills
    for agent_dir in sorted(PROFILES_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_id = agent_dir.name
        if agent_id == "indexer-state.json":
            continue
        agent_name = _get_agent_name(agent_id)
        skills_dir = agent_dir / "skills"
        if not skills_dir.exists():
            continue
        for category_dir in sorted(skills_dir.iterdir()):
            if not category_dir.is_dir():
                continue
            for skill_dir in sorted(category_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                try:
                    content = skill_md.read_text(encoding="utf-8")
                except Exception:
                    continue
                fm = _parse_frontmatter(content)
                file_count = sum(1 for _ in skill_dir.rglob("*") if _.is_file())
                mtime = skill_md.stat().st_mtime
                skills.append({
                    "agentId": agent_id,
                    "agentName": agent_name,
                    "category": category_dir.name,
                    "skillName": skill_dir.name,
                    "path": f"skills/{category_dir.name}/{skill_dir.name}",
                    "description": fm.get("description", ""),
                    "tags": fm.get("tags", []),
                    "version": fm.get("version", ""),
                    "fileCount": file_count,
                    "modifiedAt": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                })
    return skills


class SkillUpdateBody(BaseModel):
    markdown: str


class FileUpdateBody(BaseModel):
    content: str


@app.get("/team-skills")
async def list_team_skills(authorization: str = Header(default="")):
    _check_auth(authorization)
    return _scan_skills()


@app.get("/team-skills/{agent_id}/{category}/{skill_name}")
async def get_team_skill(agent_id: str, category: str, skill_name: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    skill_dir = PROFILES_DIR / agent_id / "skills" / category / skill_name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise HTTPException(status_code=404, detail="Skill not found")
    content = skill_md.read_text(encoding="utf-8")
    fm = _parse_frontmatter(content)
    agent_name = _get_agent_name(agent_id)
    files = []
    for f in sorted(skill_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(skill_dir)
            files.append({"path": str(rel), "kind": "skill" if rel.name == "SKILL.md" else "other"})
    return {
        "agentId": agent_id,
        "agentName": agent_name,
        "category": category,
        "skillName": skill_name,
        "path": f"skills/{category}/{skill_name}",
        "description": fm.get("description", ""),
        "tags": fm.get("tags", []),
        "version": fm.get("version", ""),
        "fileCount": len(files),
        "modifiedAt": datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc).isoformat(),
        "markdown": content,
        "files": files,
    }


@app.put("/team-skills/{agent_id}/{category}/{skill_name}")
async def update_team_skill(agent_id: str, category: str, skill_name: str, body: SkillUpdateBody, authorization: str = Header(default="")):
    _check_auth(authorization)
    skill_md = PROFILES_DIR / agent_id / "skills" / category / skill_name / "SKILL.md"
    if not skill_md.exists():
        raise HTTPException(status_code=404, detail="Skill not found")
    skill_md.write_text(body.markdown, encoding="utf-8")
    return {"ok": True}


@app.delete("/team-skills/{agent_id}/{category}/{skill_name}")
async def delete_team_skill(agent_id: str, category: str, skill_name: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    skill_dir = PROFILES_DIR / agent_id / "skills" / category / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail="Skill not found")
    shutil.rmtree(skill_dir)
    return {"ok": True}


@app.get("/team-skills/{agent_id}/{category}/{skill_name}/files/{file_path:path}")
async def read_team_skill_file(agent_id: str, category: str, skill_name: str, file_path: str, authorization: str = Header(default="")):
    _check_auth(authorization)
    full_path = PROFILES_DIR / agent_id / "skills" / category / skill_name / file_path
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = full_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = f"[binary file: {full_path.stat().st_size} bytes]"
    return {"path": file_path, "content": content}


@app.put("/team-skills/{agent_id}/{category}/{skill_name}/files/{file_path:path}")
async def write_team_skill_file(agent_id: str, category: str, skill_name: str, file_path: str, body: FileUpdateBody, authorization: str = Header(default="")):
    _check_auth(authorization)
    full_path = PROFILES_DIR / agent_id / "skills" / category / skill_name / file_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(body.content, encoding="utf-8")
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8681, log_level="info")
```

- [ ] **Step 2: Add supervisor program**

Append to `hermes-gateway/supervisord.conf`:

```ini

[program:team-skills-api]
command=python -u /opt/orchestrator/team_skills_api.py
autostart=true
autorestart=true
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
redirect_stderr=true
priority=5
```

- [ ] **Step 3: Add env vars to docker-compose.yml**

In `docker-compose.yml`, add `TEAM_SKILLS_API_KEY` to both `hermes-gateway` (after line 118) and `paperclip-server` (after line 184) environment blocks:

For `hermes-gateway` environment (after `OUTLINE_DB_URL` line):
```yaml
      TEAM_SKILLS_API_KEY: "${TEAM_SKILLS_API_KEY:-}"
```

For `paperclip-server` environment (after `PAPERCLIP_ALLOWED_ATTACHMENT_TYPES` line):
```yaml
      HERMES_GATEWAY_TEAM_SKILLS_URL: "http://hermes-gateway:8681"
      TEAM_SKILLS_API_KEY: "${TEAM_SKILLS_API_KEY:-}"
```

- [ ] **Step 4: Add TEAM_SKILLS_API_KEY to .env**

```bash
grep -q TEAM_SKILLS_API_KEY /mnt/services/hw-rnd-ai-crew/.env || echo 'TEAM_SKILLS_API_KEY="team-skills-secret-key"' >> /mnt/services/hw-rnd-ai-crew/.env
```

- [ ] **Step 5: Verify FastAPI and uvicorn available in container**

```bash
docker exec hermes-gateway python -c "import uvicorn; import fastapi; print('OK')"
```

Expected: `OK`. If missing, add `uvicorn[standard]` and `fastapi` to the hermes-gateway Dockerfile pip install.

- [ ] **Step 6: Rebuild and test**

```bash
docker compose up -d --force-recreate hermes-gateway
docker exec hermes-gateway curl -s http://localhost:8681/team-skills | head -c 200
```

Expected: JSON array of team skills.

- [ ] **Step 7: Commit**

```bash
git add hermes-gateway/orchestrator/team_skills_api.py hermes-gateway/supervisord.conf docker-compose.yml
git commit -m "feat(hermes): add team skills API server (port 8681)"
```

---

### Task 4: Server — hidden sources routes and delete-by-source

**Files:**
- Modify: `paperclip/server/src/services/company-skills.ts` (add `deleteBySource`)
- Modify: `paperclip/server/src/routes/company-skills.ts` (add routes)

- [ ] **Step 1: Add deleteBySource to company-skills service**

In `paperclip/server/src/services/company-skills.ts`, add the following method before the `return` block (before line 2350). Insert after line 2348:

```typescript
  async function deleteBySource(companyId: string, sourceType: string, sourceLocator: string): Promise<{ deletedCount: number }> {
    const rows = await db
      .select()
      .from(companySkills)
      .where(
        and(
          eq(companySkills.companyId, companyId),
          eq(companySkills.sourceType, sourceType),
          eq(companySkills.sourceLocator, sourceLocator),
        ),
      );
    if (rows.length === 0) return { deletedCount: 0 };

    for (const row of rows) {
      await deleteSkill(companyId, row.id);
    }
    return { deletedCount: rows.length };
  }
```

Add it to the return object (after line 2364 `deleteSkill,`):

```typescript
    deleteBySource,
```

- [ ] **Step 2: Add routes to company-skills router**

In `paperclip/server/src/routes/company-skills.ts`, add these routes before the `return router` statement (before line 321). Also add `companies` import.

Add import at top (after line 7):
```typescript
import { companies } from "@paperclipai/db";
```

Add routes before `return router`:

```typescript
  router.get("/companies/:companyId/hidden-sources", async (req, res) => {
    const companyId = req.params.companyId as string;
    assertCompanyAccess(req, companyId);
    const row = await db.select({ hiddenSources: companies.hiddenSources }).from(companies).where(eq(companies.id, companyId)).then(r => r[0]);
    res.json(row?.hiddenSources ?? []);
  });

  router.put("/companies/:companyId/hidden-sources", async (req, res) => {
    const companyId = req.params.companyId as string;
    await assertCanMutateCompanySkills(req, companyId);
    const sources = req.body;
    if (!Array.isArray(sources)) {
      res.status(400).json({ error: "Expected array" });
      return;
    }
    await db.update(companies).set({ hiddenSources: sources }).where(eq(companies.id, companyId));
    res.json(sources);
  });

  router.delete("/companies/:companyId/skills-by-source", async (req, res) => {
    const companyId = req.params.companyId as string;
    await assertCanMutateCompanySkills(req, companyId);
    const sourceType = String(req.query.sourceType ?? "");
    const sourceLocator = String(req.query.sourceLocator ?? "");
    if (!sourceType || !sourceLocator) {
      res.status(400).json({ error: "sourceType and sourceLocator query params are required" });
      return;
    }
    const result = await svc.deleteBySource(companyId, sourceType, sourceLocator);

    const actor = getActorInfo(req);
    await logActivity(db, {
      companyId,
      actorType: actor.actorType,
      actorId: actor.actorId,
      agentId: actor.agentId,
      runId: actor.runId,
      action: "company.skills_deleted_by_source",
      entityType: "company",
      entityId: companyId,
      details: { sourceType, sourceLocator, deletedCount: result.deletedCount },
    });

    res.json(result);
  });

  // Team Skills proxy
  const TEAM_SKILLS_URL = process.env.HERMES_GATEWAY_TEAM_SKILLS_URL || "http://hermes-gateway:8681";
  const TEAM_SKILLS_KEY = process.env.TEAM_SKILLS_API_KEY || "";

  router.get("/companies/:companyId/team-skills", async (req, res) => {
    const companyId = req.params.companyId as string;
    assertCompanyAccess(req, companyId);
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills`, {
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}` },
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });

  router.get("/companies/:companyId/team-skills/:agentId/:category/:skillName", async (req, res) => {
    const companyId = req.params.companyId as string;
    assertCompanyAccess(req, companyId);
    const { agentId, category, skillName } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}`, {
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}` },
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });

  router.put("/companies/:companyId/team-skills/:agentId/:category/:skillName", async (req, res) => {
    const companyId = req.params.companyId as string;
    await assertCanMutateCompanySkills(req, companyId);
    const { agentId, category, skillName } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify(req.body),
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });

  router.delete("/companies/:companyId/team-skills/:agentId/:category/:skillName", async (req, res) => {
    const companyId = req.params.companyId as string;
    await assertCanMutateCompanySkills(req, companyId);
    const { agentId, category, skillName } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}` },
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });

  router.get("/companies/:companyId/team-skills/:agentId/:category/:skillName/files/:filePath(*)", async (req, res) => {
    const companyId = req.params.companyId as string;
    assertCompanyAccess(req, companyId);
    const { agentId, category, skillName, filePath } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}/files/${filePath}`, {
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}` },
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });

  router.put("/companies/:companyId/team-skills/:agentId/:category/:skillName/files/:filePath(*)", async (req, res) => {
    const companyId = req.params.companyId as string;
    await assertCanMutateCompanySkills(req, companyId);
    const { agentId, category, skillName, filePath } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}/files/${filePath}`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify(req.body),
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });
```

- [ ] **Step 3: Deploy server changes**

Since paperclip-server runs from an image, the server dist files need to be updated inside the container:

```bash
docker compose restart paperclip-server
```

Note: Server files are compiled JS in the image. Changes to TypeScript source in `paperclip/server/src/` must be compiled and deployed via `docker cp` + restart, or the image must be rebuilt.

- [ ] **Step 4: Commit**

```bash
git add paperclip/server/src/services/company-skills.ts paperclip/server/src/routes/company-skills.ts
git commit -m "feat(server): add hidden-sources, delete-by-source, and team-skills proxy routes"
```

---

### Task 5: UI API client — new methods

**Files:**
- Modify: `paperclip/ui/src/api/companySkills.ts`
- Modify: `paperclip/ui/src/lib/queryKeys.ts`

- [ ] **Step 1: Add API client methods**

In `paperclip/ui/src/api/companySkills.ts`, add imports and methods. Add to imports at top:

```typescript
import type { TeamSkill, TeamSkillDetail } from "@paperclipai/shared";
```

Add these methods to the `companySkillsApi` object (before the closing `}`):

```typescript
  hiddenSources: (companyId: string) =>
    api.get<{ source_type: string; source_locator: string }[]>(
      `/companies/${encodeURIComponent(companyId)}/hidden-sources`,
    ),
  setHiddenSources: (companyId: string, sources: { source_type: string; source_locator: string }[]) =>
    api.put<{ source_type: string; source_locator: string }[]>(
      `/companies/${encodeURIComponent(companyId)}/hidden-sources`,
      sources,
    ),
  deleteBySource: (companyId: string, sourceType: string, sourceLocator: string) =>
    api.delete<{ deletedCount: number }>(
      `/companies/${encodeURIComponent(companyId)}/skills-by-source?sourceType=${encodeURIComponent(sourceType)}&sourceLocator=${encodeURIComponent(sourceLocator)}`,
    ),
  listTeamSkills: (companyId: string) =>
    api.get<TeamSkill[]>(
      `/companies/${encodeURIComponent(companyId)}/team-skills`,
    ),
  getTeamSkill: (companyId: string, agentId: string, category: string, skillName: string) =>
    api.get<TeamSkillDetail>(
      `/companies/${encodeURIComponent(companyId)}/team-skills/${agentId}/${category}/${skillName}`,
    ),
  updateTeamSkill: (companyId: string, agentId: string, category: string, skillName: string, markdown: string) =>
    api.put<{ ok: boolean }>(
      `/companies/${encodeURIComponent(companyId)}/team-skills/${agentId}/${category}/${skillName}`,
      { markdown },
    ),
  deleteTeamSkill: (companyId: string, agentId: string, category: string, skillName: string) =>
    api.delete<{ ok: boolean }>(
      `/companies/${encodeURIComponent(companyId)}/team-skills/${agentId}/${category}/${skillName}`,
    ),
```

- [ ] **Step 2: Add query keys**

In `paperclip/ui/src/lib/queryKeys.ts`, add inside the `companySkills` object (after the `file` key, line 13):

```typescript
    hiddenSources: (companyId: string) => ["company-skills", companyId, "hidden-sources"] as const,
    teamSkills: (companyId: string) => ["company-skills", companyId, "team-skills"] as const,
    teamSkillDetail: (companyId: string, agentId: string, category: string, skillName: string) =>
      ["company-skills", companyId, "team-skills", agentId, category, skillName] as const,
```

- [ ] **Step 3: Commit**

```bash
git add paperclip/ui/src/api/companySkills.ts paperclip/ui/src/lib/queryKeys.ts
git commit -m "feat(ui): add API client methods for hidden sources, delete-by-source, and team skills"
```

---

### Task 6: UI — Source-grouped tree view in CompanySkills

**Files:**
- Modify: `paperclip/ui/src/pages/CompanySkills.tsx`

This is the largest change. The existing `SkillList` component (lines 382-487) renders a flat filtered list. We replace it with a `SourceGroupedList` that groups skills by `sourceType` → `sourceLocator`, and adds a "Team Skills" section.

- [ ] **Step 1: Add new types and helpers at the top of CompanySkills.tsx**

After the existing imports and before the `SkillTreeNode` type (before line 57), add:

```typescript
import type { TeamSkill, TeamSkillDetail } from "@paperclipai/shared";
import { Trash2, EyeOff, Users } from "lucide-react";
```

After the `skillRoute` function (after line 232), add:

```typescript
type HiddenSource = { source_type: string; source_locator: string };

type SourceGroup = {
  sourceType: string;
  sourceLocator: string;
  label: string;
  skills: CompanySkillListItem[];
};

function groupSkillsBySource(skills: CompanySkillListItem[]): SourceGroup[] {
  const map = new Map<string, SourceGroup>();
  for (const skill of skills) {
    const key = `${skill.sourceType}::${skill.sourceLocator ?? ""}`;
    if (!map.has(key)) {
      map.set(key, {
        sourceType: skill.sourceType,
        sourceLocator: skill.sourceLocator ?? "",
        label: skill.sourceLabel ?? skill.sourceType,
        skills: [],
      });
    }
    map.get(key)!.skills.push(skill);
  }
  const groups = Array.from(map.values());

  const typeOrder = ["local_path", "github", "url", "skills_sh", "catalog"];
  groups.sort((a, b) => {
    const ai = typeOrder.indexOf(a.sourceType);
    const bi = typeOrder.indexOf(b.sourceType);
    if (ai !== bi) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    return a.label.localeCompare(b.label);
  });
  return groups;
}

function sourceTypeLabel(sourceType: string): string {
  switch (sourceType) {
    case "local_path": return "Local";
    case "github": return "GitHub";
    case "url": return "URL";
    case "skills_sh": return "skills.sh";
    case "catalog": return "Catalog";
    case "team": return "Team Skills";
    default: return sourceType;
  }
}
```

- [ ] **Step 2: Add SourceGroupedList component**

After the `SkillList` component (after line 487), add the new `SourceGroupedList` component:

```typescript
function SourceGroupedList({
  skills,
  teamSkills,
  hiddenSources,
  selectedSkillId,
  selectedTeamSkill,
  expandedSkillId,
  expandedDirs,
  expandedGroups,
  selectedPaths,
  skillFilter,
  onToggleSkill,
  onToggleDir,
  onSelectSkill,
  onSelectPath,
  onToggleGroup,
  onToggleVisibility,
  onDeleteSource,
  onSelectTeamSkill,
}: {
  skills: CompanySkillListItem[];
  teamSkills: TeamSkill[];
  hiddenSources: HiddenSource[];
  selectedSkillId: string | null;
  selectedTeamSkill: { agentId: string; category: string; skillName: string } | null;
  expandedSkillId: string | null;
  expandedDirs: Record<string, Set<string>>;
  expandedGroups: Set<string>;
  selectedPaths: Record<string, string>;
  skillFilter: string;
  onToggleSkill: (skillId: string) => void;
  onToggleDir: (skillId: string, path: string) => void;
  onSelectSkill: (skillId: string) => void;
  onSelectPath: (skillId: string, path: string) => void;
  onToggleGroup: (key: string) => void;
  onToggleVisibility: (sourceType: string, sourceLocator: string) => void;
  onDeleteSource: (sourceType: string, sourceLocator: string) => void;
  onSelectTeamSkill: (agentId: string, category: string, skillName: string) => void;
}) {
  const groups = groupSkillsBySource(skills);
  const filter = skillFilter.toLowerCase();
  const isHidden = (st: string, sl: string) => hiddenSources.some(h => h.source_type === st && h.source_locator === sl);

  const filteredGroups = groups.map(g => ({
    ...g,
    skills: g.skills.filter(s => {
      const haystack = `${s.name} ${s.key} ${s.slug} ${s.sourceLabel ?? ""}`.toLowerCase();
      return haystack.includes(filter);
    }),
  })).filter(g => g.skills.length > 0);

  const filteredTeamSkills = teamSkills.filter(ts => {
    const haystack = `${ts.skillName} ${ts.agentName} ${ts.category} ${ts.description} ${ts.tags.join(" ")}`.toLowerCase();
    return haystack.includes(filter);
  });

  const teamAgents = new Map<string, TeamSkill[]>();
  for (const ts of filteredTeamSkills) {
    if (!teamAgents.has(ts.agentName)) teamAgents.set(ts.agentName, []);
    teamAgents.get(ts.agentName)!.push(ts);
  }

  const teamGroupKey = "team::";
  const teamHidden = isHidden("team", "");
  const teamExpanded = expandedGroups.has(teamGroupKey);

  return (
    <div>
      {filteredGroups.map((group) => {
        const groupKey = `${group.sourceType}::${group.sourceLocator}`;
        const hidden = isHidden(group.sourceType, group.sourceLocator);
        const expanded = expandedGroups.has(groupKey);
        const SourceIcon = sourceMeta(
          group.skills[0]?.sourceBadge ?? "catalog",
          group.skills[0]?.sourceLabel ?? null,
        ).icon;

        return (
          <div key={groupKey} className="border-b border-border">
            <div
              className={cn(
                "group grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-x-1 px-3 py-1.5 hover:bg-accent/30 cursor-pointer",
              )}
              onClick={() => onToggleGroup(groupKey)}
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground">
                  {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                </span>
                <SourceIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 truncate text-[13px] font-medium">{group.label}</span>
                <span className={cn("ml-1 text-xs text-muted-foreground", hidden && "line-through opacity-50")}>
                  ({group.skills.length})
                </span>
              </div>
              <div className="flex items-center justify-end gap-0.5">
                <button
                  type="button"
                  className="flex h-7 w-7 items-center justify-center rounded-sm text-muted-foreground opacity-0 transition-[opacity,background-color,color] hover:bg-accent hover:text-foreground group-hover:opacity-70"
                  onClick={(e) => { e.stopPropagation(); onToggleVisibility(group.sourceType, group.sourceLocator); }}
                  title={hidden ? "Show skills" : "Hide skills"}
                >
                  <EyeOff className={cn("h-3.5 w-3.5", hidden && "text-foreground")} />
                </button>
                <button
                  type="button"
                  className="flex h-7 w-7 items-center justify-center rounded-sm text-muted-foreground opacity-0 transition-[opacity,background-color,color] hover:bg-destructive/10 hover:text-destructive group-hover:opacity-70"
                  onClick={(e) => { e.stopPropagation(); onDeleteSource(group.sourceType, group.sourceLocator); }}
                  title="Delete all skills from this source"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            {expanded && !hidden && (
              <SkillList
                skills={group.skills}
                selectedSkillId={selectedSkillId}
                skillFilter=""
                expandedSkillId={expandedSkillId}
                expandedDirs={expandedDirs}
                selectedPaths={selectedPaths}
                onToggleSkill={onToggleSkill}
                onToggleDir={onToggleDir}
                onSelectSkill={onSelectSkill}
                onSelectPath={onSelectPath}
              />
            )}
          </div>
        );
      })}

      {filteredTeamSkills.length > 0 && (
        <div className="border-b border-border">
          <div
            className={cn(
              "group grid grid-cols-[minmax(0,1fr)_2.25rem] items-center gap-x-1 px-3 py-1.5 hover:bg-accent/30 cursor-pointer",
            )}
            onClick={() => onToggleGroup(teamGroupKey)}
          >
            <div className="flex min-w-0 items-center gap-2">
              <span className="flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground">
                {teamExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              </span>
              <Users className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 truncate text-[13px] font-medium">Team Skills</span>
              <span className={cn("ml-1 text-xs text-muted-foreground", teamHidden && "line-through opacity-50")}>
                ({filteredTeamSkills.length})
              </span>
            </div>
            <button
              type="button"
              className="flex h-7 w-7 items-center justify-center rounded-sm text-muted-foreground opacity-0 transition-[opacity,background-color,color] hover:bg-accent hover:text-foreground group-hover:opacity-70"
              onClick={(e) => { e.stopPropagation(); onToggleVisibility("team", ""); }}
              title={teamHidden ? "Show team skills" : "Hide team skills"}
            >
              <EyeOff className={cn("h-3.5 w-3.5", teamHidden && "text-foreground")} />
            </button>
          </div>
          {teamExpanded && !teamHidden && (
            <div>
              {Array.from(teamAgents.entries()).map(([agentName, agentSkills]) => (
                <div key={agentName}>
                  <div className="flex items-center gap-2 px-4 py-1 text-[12px] font-medium text-muted-foreground uppercase tracking-wider">
                    <span>{agentName}</span>
                    <span className="text-[10px] normal-case tracking-normal">({agentSkills.length})</span>
                  </div>
                  {agentSkills.map((ts) => {
                    const isSelected = selectedTeamSkill?.agentId === ts.agentId && selectedTeamSkill?.category === ts.category && selectedTeamSkill?.skillName === ts.skillName;
                    return (
                      <button
                        key={`${ts.agentId}/${ts.category}/${ts.skillName}`}
                        type="button"
                        className={cn(
                          "flex w-full items-center gap-2 px-6 py-1.5 text-left text-sm text-muted-foreground hover:bg-accent/30 hover:text-foreground",
                          isSelected && "text-foreground bg-accent/20",
                        )}
                        onClick={() => onSelectTeamSkill(ts.agentId, ts.category, ts.skillName)}
                      >
                        <FileText className="h-3.5 w-3.5 shrink-0" />
                        <span className="min-w-0 truncate text-[13px] font-medium">{ts.skillName}</span>
                        {ts.tags.length > 0 && (
                          <span className="text-[10px] text-muted-foreground truncate">
                            {ts.tags.slice(0, 2).join(", ")}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Update CompanySkills component to use SourceGroupedList**

In the `CompanySkills` component (starting at line 735), add state for team skills, hidden sources, and expanded groups. After the existing state declarations (after line 753), add:

```typescript
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [selectedTeamSkill, setSelectedTeamSkill] = useState<{ agentId: string; category: string; skillName: string } | null>(null);
  const [teamSkillDraft, setTeamSkillDraft] = useState("");
  const [teamSkillEditMode, setTeamSkillEditMode] = useState(false);
  const [confirmDeleteSource, setConfirmDeleteSource] = useState<{ sourceType: string; sourceLocator: string } | null>(null);
```

Add queries for team skills and hidden sources (after the `updateStatusQuery` block, around line 802):

```typescript
  const teamSkillsQuery = useQuery({
    queryKey: queryKeys.companySkills.teamSkills(selectedCompanyId ?? ""),
    queryFn: () => companySkillsApi.listTeamSkills(selectedCompanyId!),
    enabled: Boolean(selectedCompanyId),
  });

  const hiddenSourcesQuery = useQuery({
    queryKey: queryKeys.companySkills.hiddenSources(selectedCompanyId ?? ""),
    queryFn: () => companySkillsApi.hiddenSources(selectedCompanyId!),
    enabled: Boolean(selectedCompanyId),
  });
```

Add mutations for hidden sources and delete-by-source (after the existing mutations, around line 933):

```typescript
  const toggleVisibility = useMutation({
    mutationFn: (sources: HiddenSource[]) => companySkillsApi.setHiddenSources(selectedCompanyId!, sources),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.companySkills.hiddenSources(selectedCompanyId!) });
    },
  });

  const deleteSource = useMutation({
    mutationFn: ({ sourceType, sourceLocator }: { sourceType: string; sourceLocator: string }) =>
      companySkillsApi.deleteBySource(selectedCompanyId!, sourceType, sourceLocator),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.companySkills.list(selectedCompanyId!) });
      setConfirmDeleteSource(null);
      pushToast({ tone: "success", title: "Source deleted", body: "All skills from this source have been removed." });
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Delete failed", body: error instanceof Error ? error.message : "Failed to delete source." });
    },
  });

  const teamSkillDetailQuery = useQuery({
    queryKey: queryKeys.companySkills.teamSkillDetail(selectedCompanyId ?? "", selectedTeamSkill?.agentId ?? "", selectedTeamSkill?.category ?? "", selectedTeamSkill?.skillName ?? ""),
    queryFn: () => companySkillsApi.getTeamSkill(selectedCompanyId!, selectedTeamSkill!.agentId, selectedTeamSkill!.category, selectedTeamSkill!.skillName),
    enabled: Boolean(selectedCompanyId && selectedTeamSkill),
  });

  const updateTeamSkill = useMutation({
    mutationFn: ({ agentId, category, skillName, markdown }: { agentId: string; category: string; skillName: string; markdown: string }) =>
      companySkillsApi.updateTeamSkill(selectedCompanyId!, agentId, category, skillName, markdown),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.companySkills.teamSkills(selectedCompanyId!) });
      setTeamSkillEditMode(false);
      pushToast({ tone: "success", title: "Team skill updated" });
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Update failed", body: error instanceof Error ? error.message : "Failed to update team skill." });
    },
  });

  const deleteTeamSkillMutation = useMutation({
    mutationFn: ({ agentId, category, skillName }: { agentId: string; category: string; skillName: string }) =>
      companySkillsApi.deleteTeamSkill(selectedCompanyId!, agentId, category, skillName),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.companySkills.teamSkills(selectedCompanyId!) });
      setSelectedTeamSkill(null);
      pushToast({ tone: "success", title: "Team skill deleted" });
    },
    onError: (error) => {
      pushToast({ tone: "error", title: "Delete failed", body: error instanceof Error ? error.message : "Failed to delete team skill." });
    },
  });
```

- [ ] **Step 4: Replace SkillList usage with SourceGroupedList in the render**

In the JSX, replace the `<SkillList>` block (lines 1119-1139) with:

```tsx
            <SourceGroupedList
              skills={skillsQuery.data ?? []}
              teamSkills={teamSkillsQuery.data ?? []}
              hiddenSources={hiddenSourcesQuery.data ?? []}
              selectedSkillId={selectedSkillId}
              selectedTeamSkill={selectedTeamSkill}
              expandedSkillId={expandedSkillId}
              expandedDirs={expandedDirs}
              expandedGroups={expandedGroups}
              selectedPaths={selectedSkillId ? { [selectedSkillId]: selectedPath } : {}}
              skillFilter={skillFilter}
              onToggleSkill={(currentSkillId) =>
                setExpandedSkillId((current) => current === currentSkillId ? null : currentSkillId)
              }
              onToggleDir={(currentSkillId, path) => {
                setExpandedDirs((current) => {
                  const next = new Set(current[currentSkillId] ?? []);
                  if (next.has(path)) next.delete(path);
                  else next.add(path);
                  return { ...current, [currentSkillId]: next };
                });
              }}
              onSelectSkill={(currentSkillId) => setExpandedSkillId(currentSkillId)}
              onSelectPath={() => {}}
              onToggleGroup={(key) => {
                setExpandedGroups((current) => {
                  const next = new Set(current);
                  if (next.has(key)) next.delete(key);
                  else next.add(key);
                  return next;
                });
              }}
              onToggleVisibility={(sourceType, sourceLocator) => {
                const current = hiddenSourcesQuery.data ?? [];
                const exists = current.some(h => h.source_type === sourceType && h.source_locator === sourceLocator);
                const updated = exists
                  ? current.filter(h => !(h.source_type === sourceType && h.source_locator === sourceLocator))
                  : [...current, { source_type: sourceType, source_locator: sourceLocator }];
                toggleVisibility.mutate(updated);
              }}
              onDeleteSource={(sourceType, sourceLocator) => {
                setConfirmDeleteSource({ sourceType, sourceLocator });
              }}
              onSelectTeamSkill={(agentId, category, skillName) => {
                setSelectedTeamSkill({ agentId, category, skillName });
              }}
            />
```

- [ ] **Step 5: Add delete confirmation dialog and team skill pane**

After the `<Dialog>` for `emptySourceHelpOpen` (after line 1044), add:

```tsx
      <Dialog open={confirmDeleteSource !== null} onOpenChange={() => setConfirmDeleteSource(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete source</DialogTitle>
            <DialogDescription>
              This will permanently delete all skills from this source and remove them from agent configurations.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDeleteSource(null)}>Cancel</Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (confirmDeleteSource) {
                  deleteSource.mutate(confirmDeleteSource);
                }
              }}
              disabled={deleteSource.isPending}
            >
              {deleteSource.isPending ? "Deleting..." : "Delete all skills"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
```

And in the main content area (the right pane), modify the `<SkillPane>` section to also handle team skills. After the `<SkillPane>` block, add a team skills pane when `selectedTeamSkill` is set:

```tsx
          {!selectedTeamSkill && (
            <SkillPane
              loading={skillsQuery.isLoading || detailQuery.isLoading}
              detail={activeDetail}
              file={activeFile}
              fileLoading={fileQuery.isLoading && !activeFile}
              updateStatus={updateStatusQuery.data}
              updateStatusLoading={updateStatusQuery.isLoading}
              viewMode={viewMode}
              editMode={editMode}
              draft={draft}
              setViewMode={setViewMode}
              setEditMode={setEditMode}
              setDraft={setDraft}
              onCheckUpdates={() => { void updateStatusQuery.refetch(); }}
              checkUpdatesPending={updateStatusQuery.isFetching}
              onInstallUpdate={() => installUpdate.mutate()}
              installUpdatePending={installUpdate.isPending}
              onSave={() => saveFile.mutate()}
              savePending={saveFile.isPending}
            />
          )}
          {selectedTeamSkill && (
            <div className="min-w-0">
              {teamSkillDetailQuery.isLoading ? (
                <PageSkeleton variant="detail" />
              ) : !teamSkillDetailQuery.data ? (
                <EmptyState icon={Users} message="Select a team skill to inspect." />
              ) : (
                <>
                  <div className="border-b border-border px-5 py-4">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="min-w-0">
                        <h1 className="flex items-center gap-2 truncate text-2xl font-semibold">
                          <Users className="h-5 w-5 shrink-0 text-muted-foreground" />
                          {teamSkillDetailQuery.data.skillName}
                        </h1>
                        <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
                          {teamSkillDetailQuery.data.description}
                        </p>
                        <div className="mt-3 flex flex-wrap gap-2 text-xs">
                          <span className="rounded-full bg-accent px-2.5 py-0.5">{teamSkillDetailQuery.data.agentName}</span>
                          <span className="rounded-full bg-accent px-2.5 py-0.5">{teamSkillDetailQuery.data.category}</span>
                          {teamSkillDetailQuery.data.tags.map(t => (
                            <span key={t} className="rounded-full bg-accent/60 px-2.5 py-0.5">{t}</span>
                          ))}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
                          onClick={() => {
                            if (teamSkillEditMode) {
                              setTeamSkillEditMode(false);
                            } else {
                              setTeamSkillDraft(teamSkillDetailQuery.data!.markdown);
                              setTeamSkillEditMode(true);
                            }
                          }}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                          {teamSkillEditMode ? "Stop editing" : "Edit"}
                        </button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => {
                            deleteTeamSkillMutation.mutate({
                              agentId: selectedTeamSkill.agentId,
                              category: selectedTeamSkill.category,
                              skillName: selectedTeamSkill.skillName,
                            });
                          }}
                          disabled={deleteTeamSkillMutation.isPending}
                        >
                          <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                          Delete
                        </Button>
                      </div>
                    </div>
                  </div>
                  <div className="border-b border-border px-5 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-sm">SKILL.md</span>
                      {teamSkillEditMode && (
                        <div className="flex items-center gap-2">
                          <Button variant="ghost" size="sm" onClick={() => setTeamSkillEditMode(false)}>Cancel</Button>
                          <Button
                            size="sm"
                            onClick={() => {
                              updateTeamSkill.mutate({
                                agentId: selectedTeamSkill.agentId,
                                category: selectedTeamSkill.category,
                                skillName: selectedTeamSkill.skillName,
                                markdown: teamSkillDraft,
                              });
                            }}
                            disabled={updateTeamSkill.isPending}
                          >
                            <Save className="mr-1.5 h-3.5 w-3.5" />
                            {updateTeamSkill.isPending ? "Saving..." : "Save"}
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="min-h-[560px] px-5 py-5">
                    {teamSkillEditMode ? (
                      <MarkdownEditor
                        value={teamSkillDraft}
                        onChange={setTeamSkillDraft}
                        bordered={false}
                        className="min-h-[520px]"
                      />
                    ) : (
                      <MarkdownBody>{stripFrontmatter(teamSkillDetailQuery.data.markdown)}</MarkdownBody>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
```

Wrap the existing `<SkillPane>` in `{!selectedTeamSkill && (...)}` so only one pane shows at a time.

- [ ] **Step 6: Build UI in container**

```bash
docker exec -w /app/ui paperclip-server node node_modules/vite/bin/vite.js build
```

Expected: Build succeeds.

- [ ] **Step 7: Verify UI loads**

Open the Paperclip UI in the browser, navigate to Skills page. Check:
- Skills are grouped by source type → source locator
- Hide/show toggle works per source
- Delete source shows confirmation dialog
- Team Skills section shows agent skills from hermes profiles
- Clicking a team skill opens detail pane with edit/delete

- [ ] **Step 8: Commit**

```bash
git add paperclip/ui/src/pages/CompanySkills.tsx
git commit -m "feat(ui): add source-grouped tree view, hide/remove sources, and team skills section"
```

---

### Task 7: Integration test and deploy

**Files:** None new — verification only.

- [ ] **Step 1: Verify orchestrator team skills API is running**

```bash
docker exec hermes-gateway curl -s http://localhost:8681/team-skills | python3 -m json.tool | head -30
```

Expected: JSON array with agent skills.

- [ ] **Step 2: Verify paperclip-server proxy**

```bash
docker exec paperclip-server curl -s http://localhost:3100/api/companies/$(docker exec paperclip-db psql -U paperclip -t -A -c "SELECT id FROM companies LIMIT 1")/team-skills | head -c 200
```

Expected: JSON array.

- [ ] **Step 3: Verify hidden sources endpoint**

```bash
docker exec paperclip-server curl -s http://localhost:3100/api/companies/$(docker exec paperclip-db psql -U paperclip -t -A -c "SELECT id FROM companies LIMIT 1")/hidden-sources
```

Expected: `[]`

- [ ] **Step 4: Full e2e check in UI**

Open browser → Skills page:
1. Verify source groups are shown
2. Click hide on a source → skills disappear
3. Click delete on a source → confirmation → skills removed
4. Expand "Team Skills" → agent skills listed
5. Click a team skill → detail pane opens
6. Edit and save a team skill
7. Delete a team skill

- [ ] **Step 5: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: integration fixes for skill sources and team skills"
```
