# Hidden Skills & Roles

## Problem

Company admins need to exclude individual skills and roles from synchronization and agent usage without permanently deleting them. Excluded items should be remembered across re-imports from the same source.

## Approach

Add a `hidden` boolean column to both `company_skills` and `company_roles` tables. Hidden items remain in the database (so re-import skips them) but are excluded from agent provisioning and hidden from the UI by default.

## Database

**Migration:** `0054_hidden_skills_roles.sql`

```sql
ALTER TABLE company_skills ADD COLUMN hidden boolean NOT NULL DEFAULT false;
ALTER TABLE company_roles ADD COLUMN hidden boolean NOT NULL DEFAULT false;
CREATE INDEX idx_company_skills_company_hidden ON company_skills(company_id, hidden);
CREATE INDEX idx_company_roles_company_hidden ON company_roles(company_id, hidden);
```

**Schema changes:**
- `paperclip/packages/db/src/schema/company_skills.ts`: add `hidden: boolean("hidden").default(false).notNull()`
- `paperclip/packages/db/src/schema/company_roles.ts`: add `hidden: boolean("hidden").default(false).notNull()`

## Backend

### New endpoints

**PATCH `/companies/:companyId/skills/:skillId`**
- Body: `{ hidden: boolean, force?: boolean }`
- If `hidden=true` and skill has attached agents and `force` is not set: return `{ attachedAgents: number }` (HTTP 409 or a structured response)
- If `hidden=true` and `force=true`: remove skill from all agents' `desiredSkills`, set `hidden=true`
- If `hidden=false`: set `hidden=false` (does NOT re-attach to agents)

**PATCH `/companies/:companyId/roles/:roleId`**
- Body: `{ hidden: boolean, force?: boolean }`
- If `hidden=true` and role has assigned agents and `force` is not set: return `{ assignedAgents: number }`
- If `hidden=true` and `force=true`: clear `assignedRole` from all agents using this role, set `hidden=true`
- If `hidden=false`: set `hidden=false` (does NOT re-assign to agents)

### Modified queries

- `companySkillsService.list()` / `listFull()`: add `WHERE hidden = false` by default; accept `includeHidden` query param to include hidden items
- `companyRolesService.list()`: same pattern
- `companySkillsService.detail()`: allow access regardless of hidden state (for restore action)
- `companyRolesService.detail()`: same

### Import/sync behavior

- `upsertImportedSkills()`: when a skill already exists with `hidden=true`, skip the upsert (do not update content, do not reset hidden)
- `companyRolesService.importFromSource()`: when a role already exists with `hidden=true`, skip it
- `ensureBundledSkills()`: skip hidden skills (don't re-import bundled skills that were explicitly hidden)
- `scanProjectWorkspaces()`: skip if skill exists and is hidden

### Runtime provisioning

- `listRuntimeSkillEntries()`: filter out hidden skills (they should not be sent to adapters)
- `materializeRuntimeSkillFiles()`: skip hidden skills
- Agent skill sync (`syncSkills`): exclude hidden skills from resolution via `resolveRequestedSkillKeysOrThrow()` — if a hidden skill is in an agent's desiredSkills, skip it with a warning

## Shared types & validators

**Types (`packages/shared/src/types/`):**
- `CompanySkillListItem`: add `hidden: boolean`
- `CompanyRoleListItem`: add `hidden: boolean`

**Validators (`packages/shared/src/validators/`):**
- `company-skill.ts`: add `skillVisibilitySchema = z.object({ hidden: z.boolean(), force: z.boolean().optional() })`
- `role.ts`: add `roleVisibilitySchema = z.object({ hidden: z.boolean(), force: z.boolean().optional() })`

## UI

### CompanySkills.tsx

- **Default view**: hidden skills are not shown
- **Toggle**: "Show excluded" checkbox/toggle next to search bar
- **Excluded items**: rendered with reduced opacity (0.5), strikethrough on name, "Restore" button visible
- **Hide action**: kebab menu (three dots) on each skill card → "Exclude from sync"
- **Confirmation modal**: when skill has attached agents — "This skill is used by N agents. Exclude from sync?" with "Cancel" / "Exclude" buttons
- **Restore**: click "Restore" → skill becomes visible again, does NOT re-attach to agents

### CompanyRoles.tsx

- Same pattern: toggle, dimmed items, kebab menu "Exclude", confirmation modal
- Confirmation: "This role is assigned to N agents. Exclude from sync?"

## Edge cases

- **Re-import from GitHub/URL**: hidden skills are skipped during upsert. The `hidden` flag persists.
- **Agent already has hidden skill in desiredSkills**: during skill sync, hidden skills are skipped with a warning logged. The entry stays in desiredSkills but is not provisioned.
- **Restore after exclusion**: skill becomes available again. Agents that previously had it must be re-configured manually.
- **Delete vs Hide**: delete removes the record entirely (allows re-import). Hide keeps the record (blocks re-import). These are distinct actions.
- **Bulk source sync**: if a source contains 10 skills and 3 are hidden, only 7 are updated. Hidden skills retain their last-known content.

## Files to modify

| File | Change |
|------|--------|
| `paperclip/packages/db/src/migrations/0054_hidden_skills_roles.sql` | New migration |
| `paperclip/packages/db/src/schema/company_skills.ts` | Add `hidden` column |
| `paperclip/packages/db/src/schema/company_roles.ts` | Add `hidden` column |
| `paperclip/packages/shared/src/types/company-skill.ts` | Add `hidden` to types |
| `paperclip/packages/shared/src/types/role.ts` | Add `hidden` to types |
| `paperclip/packages/shared/src/validators/company-skill.ts` | Add visibility schema |
| `paperclip/packages/shared/src/validators/role.ts` | Add visibility schema |
| `paperclip/server/src/routes/company-skills.ts` | Add PATCH endpoint |
| `paperclip/server/src/routes/company-roles.ts` | Add PATCH endpoint |
| `paperclip/server/src/services/company-skills.ts` | Hide/unhide logic, list filtering, import skip |
| `paperclip/server/src/services/company-roles.ts` | Hide/unhide logic, list filtering, import skip |
| `paperclip/ui/src/api/companySkills.ts` | Add `updateSkillVisibility()` API call |
| `paperclip/ui/src/api/roles.ts` | Add `updateRoleVisibility()` API call |
| `paperclip/ui/src/pages/CompanySkills.tsx` | Toggle, dimmed items, hide/restore actions |
| `paperclip/ui/src/pages/CompanyRoles.tsx` | Toggle, dimmed items, hide/restore actions |
