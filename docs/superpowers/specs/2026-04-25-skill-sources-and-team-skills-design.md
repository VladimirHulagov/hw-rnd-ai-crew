# Skill Sources Management & Team Skills

**Date:** 2026-04-25
**Status:** Draft

## Problem

The Company Skills page (`/skills`) shows a flat list of all `company_skills` with no source grouping. Users cannot:
- Remove all skills from a specific source in one action
- Temporarily hide a source's skills from view
- See skills that agents created in their Hermes profiles (team knowledge)

## Solution

Three capabilities added to the Company Skills page:

1. **Source grouping** — skills organized in a two-level hierarchy by `source_type` then `source_locator`
2. **Hide/remove sources** — per-source visibility toggle (server-persisted) and bulk delete
3. **Team Skills source** — virtual source reading agent skills from Hermes profiles via an orchestrator API

## Feature 1: Source Grouping

### UI Changes (`CompanySkills.tsx`)

The sidebar currently renders a flat `SkillList`. Replace with a tree view:

```
▼ GitHub (5)
  ▼ github.com/org/repo (3)         [👁] [🗑]
    ├─ skill-a
    ├─ skill-b
    └─ skill-c
  ▶ github.com/other/repo (2)       [👁] [🗑]
▼ Local (2)
  ▶ /local/path (2)                 [👁] [🗑]
▼ URL (1)
  ▶ https://example.com/skill (1)   [👁] [🗑]
▼ Team Skills (16)                  [👁]
  ▶ CEO (8)
  ▶ DevOps (5)
  ▶ Board Designer (3)
```

- Top level: `source_type` groups (GitHub, Local, URL, skills.sh, Catalog, Team)
- Second level: `source_locator` groups (exact URL/path)
- Third level: individual skills

Group headers show skill count. Each `source_locator` group has visibility toggle (`[👁]`) and delete button (`[🗑]`).

### Data Flow

Client-side grouping — `GET /companies/:id/skills` returns the existing flat list. The UI groups by `source_type` then `source_locator`. No server-side changes for grouping.

For hidden state, the API returns `hidden_sources` from the company record alongside skills.

## Feature 2: Hide/Remove Sources

### Hidden Sources Storage

**New DB field:** `hidden_sources` JSONB column on `companies` table.

**Migration:** `0053_hidden_sources.sql`
```sql
ALTER TABLE companies ADD COLUMN hidden_sources jsonb DEFAULT '[]';
```

**Format:**
```json
[
  { "source_type": "github", "source_locator": "github.com/org/repo" },
  { "source_type": "local_path", "source_locator": "/local/path" }
]
```

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/companies/:id/hidden-sources` | Get hidden sources list |
| PUT | `/companies/:id/hidden-sources` | Update hidden sources list |
| DELETE | `/companies/:id/skills-by-source` | Delete all skills matching `{ source_type, source_locator }` |

**Server route** (`company-skills.ts`):

- `PUT /companies/:id/hidden-sources` — validates body against `{ sources: [{ source_type: string, source_locator: string }] }` schema, updates `companies.hidden_sources`
- `DELETE /companies/:id/skills-by-source` — accepts `{ source_type, source_locator }`, calls `companySkillService.deleteBySource()` which:
  1. Finds all `company_skills` matching `source_type` AND `source_locator`
  2. For each skill: removes from all agent `adapterConfig.paperclipSkillSync.desiredSkills`, deletes materialized files, deletes DB row
  3. Returns `{ deletedCount }`

### UI Behavior

- Clicking `[👁]` adds/removes entry from `hidden_sources` via `PUT /companies/:id/hidden-sources`
- Hidden source groups show collapsed with grayed count badge
- Clicking `[🗑]` shows confirmation dialog, then calls `DELETE /companies/:id/skills-by-source`
- "Team Skills" source group has `[👁]` toggle but no `[🗑]` (can't bulk-delete agent skills)

## Feature 3: Team Skills

### Architecture

```
Paperclip UI
    ↓ GET /companies/:id/team-skills
Paperclip Server (proxy)
    ↓ GET http://hermes-gateway:8681/team-skills
Orchestrator Team Skills API (port 8681)
    ↓ reads filesystem
Hermes Profiles (/root/.hermes/profiles/*/skills/)
```

### Orchestrator API (`team_skills_api.py`)

New FastAPI server in `hermes-gateway/orchestrator/`, running as a supervisor process on port 8681.

**Authentication:** Shared secret via `TEAM_SKILLS_API_KEY` env var. All requests must include `Authorization: Bearer <key>` header. 401 on missing/invalid key.

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/team-skills` | List all agent skills with metadata |
| GET | `/team-skills/{agent_id}/{category}/{skill_name}` | Read skill detail (SKILL.md content + file list) |
| PUT | `/team-skills/{agent_id}/{category}/{skill_name}` | Update SKILL.md content |
| DELETE | `/team-skills/{agent_id}/{category}/{skill_name}` | Delete skill directory |
| GET | `/team-skills/{agent_id}/{category}/{skill_name}/files/{path:path}` | Read additional file |
| PUT | `/team-skills/{agent_id}/{category}/{skill_name}/files/{path:path}` | Write additional file |

**`GET /team-skills` response:**
```json
[
  {
    "agent_id": "c7826470-3b08-49ad-b1d9-e73911ed64f9",
    "agent_name": "CEO",
    "category": "tools",
    "skill_name": "pdf-schematic-to-kicad",
    "path": "skills/tools/pdf-schematic-to-kicad",
    "description": "Convert PDF schematics...",
    "tags": ["kicad", "pdf"],
    "version": "0.1",
    "file_count": 1,
    "modified_at": "2026-04-21T10:00:00Z"
  }
]
```

**Implementation details:**
- Scans `/root/.hermes/profiles/*/skills/*/SKILL.md` recursively (2-level: category/skill)
- Parses YAML frontmatter from SKILL.md for name, description, tags, version
- Resolves `agent_id` → `agent_name` from DB (`SELECT name FROM agents WHERE id = $1`)
- File operations are synchronous (small files, local filesystem)
- On DELETE: removes entire skill directory (`rm -rf skills/<category>/<skill_name>`)

**Supervisor config** (`supervisord.conf`):
```ini
[program:team-skills-api]
command=python -m orchestrator.team_skills_api
directory=/opt/orchestrator
environment=TEAM_SKILLS_API_KEY="%(ENV_TEAM_SKILLS_API_KEY)s",DB_URL="%(ENV_DATABASE_URL)s"
autostart=true
autorestart=true
```

### Paperclip Server Proxy

New routes in `paperclip/server/src/routes/company-skills.ts`:

```typescript
// Proxy to hermes-gateway team skills API
router.get("/companies/:companyId/team-skills", async (req, res) => {
  const response = await fetch(`${HERMES_GATEWAY_URL}/team-skills`, {
    headers: { Authorization: `Bearer ${TEAM_SKILLS_API_KEY}` }
  });
  const data = await response.json();
  res.json(data);
});

router.get("/companies/:companyId/team-skills/:agentId/:category/:skillName", proxyHandler);
router.put("/companies/:companyId/team-skills/:agentId/:category/:skillName", proxyHandler);
router.delete("/companies/:companyId/team-skills/:agentId/:category/:skillName", proxyHandler);
```

`HERMES_GATEWAY_URL` and `TEAM_SKILLS_API_KEY` are env vars added to `docker-compose.yml` for paperclip-server.

### UI Integration

In `CompanySkills.tsx`, "Team Skills" appears as a special `source_type` group in the tree.

**Team skills sub-groups by agent name:**
```
▼ Team Skills (16)
  ▼ CEO (8)
    ├─ pdf-schematic-to-kicad    [tags: kicad, pdf]
    ├─ outline-document-extraction
    └─ ...
  ▼ DevOps (5)
    ├─ outline-rest-api-fallback
    └─ ...
  ▶ Board Designer (3)
```

**Skill detail pane:**
- Shows SKILL.md content in markdown preview mode
- "Edit" button switches to code editor (textarea)
- "Save" calls `PUT /companies/:id/team-skills/:agentId/:category/:skillName`
- "Delete" button with confirmation → `DELETE /companies/:id/team-skills/:agentId/:category/:skillName`
- Agent name badge + category badge + tags display

**New API client methods** in `companySkills.ts`:
```typescript
listTeamSkills(companyId: string): Promise<TeamSkill[]>
getTeamSkill(companyId: string, agentId: string, category: string, skillName: string): Promise<TeamSkillDetail>
updateTeamSkill(companyId: string, agentId: string, category: string, skillName: string, content: string): Promise<void>
deleteTeamSkill(companyId: string, agentId: string, category: string, skillName: string): Promise<void>
```

**New types** in `packages/shared/src/types/`:
```typescript
interface TeamSkill {
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

interface TeamSkillDetail extends TeamSkill {
  markdown: string;
  files: { path: string; kind: string }[];
}
```

## DB Changes

### Migration 0053: `hidden_sources`
```sql
ALTER TABLE companies ADD COLUMN hidden_sources jsonb DEFAULT '[]';
```

## Docker Compose Changes

```yaml
hermes-gateway:
  environment:
    TEAM_SKILLS_API_KEY: ${TEAM_SKILLS_API_KEY}

paperclip-server:
  environment:
    HERMES_GATEWAY_URL: "http://hermes-gateway:8681"
    TEAM_SKILLS_API_KEY: ${TEAM_SKILLS_API_KEY}
```

## File Inventory

### New files:
- `hermes-gateway/orchestrator/team_skills_api.py` — FastAPI server for team skills CRUD
- `paperclip/packages/db/src/migrations/0053_hidden_sources.sql` — hidden_sources column
- `paperclip/packages/shared/src/types/team-skill.ts` — TeamSkill, TeamSkillDetail types

### Modified files:
- `hermes-gateway/supervisord.conf` — add team-skills-api program
- `docker-compose.yml` — add env vars for hermes-gateway and paperclip-server
- `paperclip/server/src/routes/company-skills.ts` — add proxy routes + hidden sources + delete-by-source
- `paperclip/server/src/services/company-skills.ts` — add `deleteBySource()` method
- `paperclip/ui/src/pages/CompanySkills.tsx` — source tree view + team skills section
- `paperclip/ui/src/api/companySkills.ts` — new API client methods
- `paperclip/packages/db/src/schema/companies.ts` — hidden_sources column (if exists)
