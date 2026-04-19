# Roles System Design

## Problem

CEO agents need role descriptions when hiring new agents. Currently there's no structured way to manage a library of role templates. The agency-agents repository has rich role descriptions but there's no mechanism to import and use them.

## Solution

Build a Roles system modeled after the existing Skills system: company-scoped role library with CRUD, git-based import from external repositories, and integration into the agent creation flow.

## MVP Scope

- **Pull**: Import roles from git repositories (browse repo tree, select roles to import)
- **CRUD**: Full create/read/update/delete on company roles
- **Agent creation**: Dropdown to select a role when creating a new agent
- **Not in MVP**: Push changes back to git repo (future phase)

## Data Model

### Table: `role_sources`

Git repositories as role sources.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid PK | Auto-generated |
| `companyId` | uuid FK → companies | Company scope |
| `name` | text | Display name for the source |
| `url` | text | Git repository URL (HTTPS or SSH) |
| `ref` | text | Branch, tag, or commit SHA (default: `main`) |
| `createdAt` | timestamptz | |
| `updatedAt` | timestamptz | |

Constraints:
- `UNIQUE (companyId, url)` — one entry per repo per company

### Table: `company_roles`

Role templates available to the company.

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid PK | Auto-generated |
| `companyId` | uuid FK → companies | Company scope |
| `sourceId` | uuid FK → role_sources (nullable) | Null for locally created roles |
| `key` | text | Canonical key, e.g. `agency-agents/engineering/backend-architect` |
| `slug` | text | URL-friendly name, e.g. `backend-architect` |
| `name` | text | Human-readable, e.g. `Backend Architect` |
| `description` | text | Short description (from YAML frontmatter or manual) |
| `category` | text | Category: `engineering`, `marketing`, `product`, etc. |
| `markdown` | text | Full role file content (frontmatter + body) |
| `sourceType` | text | `git` or `local` |
| `sourceRef` | text | Git commit SHA (for git-sourced roles) |
| `sourcePath` | text | Original file path in repo (e.g. `engineering/engineering-backend-architect.md`) |
| `metadata` | jsonb | Extra frontmatter fields (emoji, color, vibe, etc.) |
| `createdAt` | timestamptz | |
| `updatedAt` | timestamptz | |

Constraints:
- `UNIQUE (companyId, key)` — canonical key uniqueness per company

### Agent Linkage

Stored in `agents.adapterConfig` JSONB:

```json
{
  "assignedRole": "agency-agents/engineering/backend-architect"
}
```

One role per agent (string, not array). Resolved at runtime by querying `company_roles` where `key = assignedRole`.

### File Inventory (markdown-only)

Unlike Skills, roles are single markdown files. No `fileInventory` needed — the entire role is stored in `markdown`.

## API

### Role Sources

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/companies/:id/role-sources` | Board | List sources |
| POST | `/companies/:id/role-sources` | Board | Add source `{ url, ref, name }` |
| PATCH | `/companies/:id/role-sources/:sourceId` | Board | Update source |
| DELETE | `/companies/:id/role-sources/:sourceId` | Board | Delete source (roles remain) |
| GET | `/companies/:id/role-sources/:sourceId/browse` | Board | Browse repo tree (categories + files) |

### Company Roles

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/companies/:id/roles` | Board | List roles |
| GET | `/companies/:id/roles/:roleId` | Board | Role detail |
| POST | `/companies/:id/roles` | Board | Create local role |
| PATCH | `/companies/:id/roles/:roleId` | Board | Update role |
| DELETE | `/companies/:id/roles/:roleId` | Board | Delete role |
| POST | `/companies/:id/roles/import` | Board | Import from source `{ sourceId, paths: string[] }` |

### Agent Role

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/agents/:id/role` | Board/Agent | Get agent's assigned role |
| POST | `/agents/:id/role` | Board | Assign role `{ roleKey }` |

## Import Flow

1. User adds source: `POST /companies/:id/role-sources { url, ref, name }`
   - Server validates URL is reachable (optional, best-effort)
   - Stores source record

2. User browses source: `GET /companies/:id/role-sources/:srcId/browse`
   - Server clones/pulls repo to a temp directory (cached per source+ref)
   - Scans for `.md` files, parses YAML frontmatter
   - Returns tree structure:
     ```json
     {
       "categories": [
         {
           "name": "engineering",
           "files": [
             { "path": "engineering/engineering-backend-architect.md", "name": "Backend Architect", "description": "..." }
           ]
         }
       ]
     }
     ```

3. User selects roles: `POST /companies/:id/roles/import { sourceId, paths }`
   - Server reads selected files from cloned repo
   - Parses frontmatter (name, description, emoji, color, vibe)
   - Category derived from directory name
   - Key: `{sourceName}/{pathWithoutExtension}` (slugified)
   - Upserts into `company_roles`

## Agent Creation Integration

### NewAgent.tsx

- Fetch company roles: `GET /companies/:id/roles`
- Add "Role" dropdown (optional — can be empty)
- Selected role → `adapterConfig.assignedRole`
- Role's `markdown` used as `promptTemplate` → passed to `materializeDefaultInstructionsBundleForNewAgent()`
- If role not in list: link to Roles page for import

### AgentDetail.tsx

- Show assigned role name in Overview tab
- Role section: display role name + link to role detail
- Allow changing role (dropdown with auto-save)

## UI Pages

### CompanyRoles (new page)

Two-panel layout (same pattern as CompanySkills):

**Left panel:**
- Role list with category grouping
- Search/filter by category
- Source management section (list sources, add/delete)

**Right panel:**
- Role detail: name, description, category, source
- Markdown preview (rendered)
- Edit mode for locally created roles
- Import button → opens browse dialog for a source
- "Used by" agents list

### Navigation

Add "Roles" link in company navigation (same level as Skills).

## Git Clone Strategy

- Clone to `{dataDir}/role-sources/{sourceId}/` (persistent across restarts)
- On browse: `git fetch` + `git checkout {ref}` if already cloned, otherwise `git clone`
- Cache SHA in `role_sources` table for staleness detection
- Cleanup: delete clone directory when source is deleted

## Cleanup from Previous Implementation

- Remove `agency-agents` git submodule
- Remove `agency-agents` bind mount from `docker-compose.yml`
- Revert CEO AGENTS.md changes that reference `/opt/agency-agents/`
- Revert CEO HEARTBEAT.md changes that reference agency-agents catalog

## Future (Post-MVP)

### Push to Git

- `POST /companies/:id/roles/:roleId/push` — push role markdown back to source repo
- Credentials: SSH key or PAT stored per source (encrypted at rest)
- Conflict resolution: overwrite (simple) or merge (advanced)

### Auto-sync

- Webhook-based: on push to repo, auto-re-import changed roles
- Scheduled: periodic pull to check for updates

### Role Versioning

- Track `sourceRef` (commit SHA) per role
- Detect when source has newer commits
- UI to review and apply updates (like Skills update-status)
