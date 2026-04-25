# Hidden Skills & Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `hidden` boolean flag to company skills and roles so admins can exclude them from sync and agent provisioning, with a reversible hide/restore UI.

**Architecture:** Add `hidden` column to both `company_skills` and `company_roles` DB tables. New `PATCH` endpoints toggle the flag. List queries filter hidden items by default. Import/sync skips hidden records. UI shows a toggle to reveal hidden items (dimmed) with restore actions.

**Tech Stack:** Drizzle ORM, Express, Zod, React + TanStack Query, Lucide icons

---

### Task 1: DB migration and schema

**Files:**
- Create: `paperclip/packages/db/src/migrations/0054_hidden_skills_roles.sql`
- Modify: `paperclip/packages/db/src/schema/company_skills.ts`
- Modify: `paperclip/packages/db/src/schema/company_roles.ts`

- [ ] **Step 1: Create migration file**

```sql
ALTER TABLE company_skills ADD COLUMN hidden boolean NOT NULL DEFAULT false;
ALTER TABLE company_roles ADD COLUMN hidden boolean NOT NULL DEFAULT false;
CREATE INDEX idx_company_skills_company_hidden ON company_skills(company_id, hidden);
CREATE INDEX idx_company_roles_company_hidden ON company_roles(company_id, hidden);
```

- [ ] **Step 2: Update `company_skills.ts` schema**

Add `boolean` to the import from `drizzle-orm/pg-core`:

```typescript
import {
  pgTable,
  uuid,
  text,
  timestamp,
  jsonb,
  boolean,
  index,
  uniqueIndex,
} from "drizzle-orm/pg-core";
```

Add `hidden` column after `updatedAt`:

```typescript
    hidden: boolean("hidden").notNull().default(false),
```

- [ ] **Step 3: Update `company_roles.ts` schema**

Add `boolean` to the import from `drizzle-orm/pg-core`:

```typescript
import {
  pgTable,
  uuid,
  text,
  timestamp,
  jsonb,
  boolean,
  index,
  uniqueIndex,
} from "drizzle-orm/pg-core";
```

Add `hidden` column after `updatedAt`:

```typescript
    hidden: boolean("hidden").notNull().default(false),
```

- [ ] **Step 4: Commit**

```bash
git add paperclip/packages/db/src/migrations/0054_hidden_skills_roles.sql paperclip/packages/db/src/schema/company_skills.ts paperclip/packages/db/src/schema/company_roles.ts
git commit -m "feat(db): add hidden column to company_skills and company_roles"
```

---

### Task 2: Shared types and validators

**Files:**
- Modify: `paperclip/packages/shared/src/types/company-skill.ts`
- Modify: `paperclip/packages/shared/src/types/role.ts`
- Modify: `paperclip/packages/shared/src/validators/company-skill.ts`
- Modify: `paperclip/packages/shared/src/validators/role.ts`
- Modify: `paperclip/packages/shared/src/types/index.ts`
- Modify: `paperclip/packages/shared/src/validators/index.ts`
- Modify: `paperclip/packages/shared/src/index.ts`

- [ ] **Step 1: Add `hidden` to skill types**

In `paperclip/packages/shared/src/types/company-skill.ts`:

Add `hidden: boolean` to `CompanySkill` interface (after `updatedAt`):

```typescript
  hidden: boolean;
```

Add `hidden: boolean` to `CompanySkillListItem` interface (after `sourcePath`):

```typescript
  hidden: boolean;
```

- [ ] **Step 2: Add `hidden` to role types**

In `paperclip/packages/shared/src/types/role.ts`:

Add `hidden: boolean` to `CompanyRole` interface (after `updatedAt`):

```typescript
  hidden: boolean;
```

Add `hidden: boolean` to `CompanyRoleListItem` interface (after `assignedAgentCount`):

```typescript
  hidden: boolean;
```

- [ ] **Step 3: Add `hidden` to skill validators**

In `paperclip/packages/shared/src/validators/company-skill.ts`:

Add `hidden: z.boolean()` to `companySkillSchema` (after `updatedAt`):

```typescript
  hidden: z.boolean(),
```

Add `hidden: z.boolean()` to `companySkillListItemSchema` (after `sourceBadge`):

```typescript
  hidden: z.boolean(),
```

Add the visibility update schema at the bottom of the file (before the type exports):

```typescript
export const companySkillVisibilitySchema = z.object({
  hidden: z.boolean(),
  force: z.boolean().optional(),
});

export type CompanySkillVisibility = z.infer<typeof companySkillVisibilitySchema>;
```

- [ ] **Step 4: Add `hidden` to role validators**

In `paperclip/packages/shared/src/validators/role.ts`:

Add `hidden: z.boolean()` to `companyRoleSchema` (after `updatedAt`):

```typescript
  hidden: z.boolean(),
```

Add `hidden: z.boolean()` to `companyRoleListItemSchema` (after `assignedAgentCount`):

```typescript
  hidden: z.boolean(),
```

Add the visibility update schema at the bottom of the file (before the type exports):

```typescript
export const companyRoleVisibilitySchema = z.object({
  hidden: z.boolean(),
  force: z.boolean().optional(),
});

export type CompanyRoleVisibility = z.infer<typeof companyRoleVisibilitySchema>;
```

- [ ] **Step 5: Export new schemas from validators/index.ts**

In `paperclip/packages/shared/src/validators/index.ts`:

Add to the skill validators re-export block (after `companySkillFileUpdateSchema`):

```typescript
  companySkillVisibilitySchema,
```

Add to the role validators re-export block (after `companyRoleImportResultSchema`):

```typescript
  companyRoleVisibilitySchema,
```

- [ ] **Step 6: Export new schemas from shared/src/index.ts**

In `paperclip/packages/shared/src/index.ts`:

Add to the validators re-export block for skills (after `companySkillFileUpdateSchema`):

```typescript
  companySkillVisibilitySchema,
```

Add to the validators re-export block for roles (after `companyRoleImportResultSchema`):

```typescript
  companyRoleVisibilitySchema,
```

- [ ] **Step 7: Commit**

```bash
git add paperclip/packages/shared/src/
git commit -m "feat(shared): add hidden field to skill/role types and validators"
```

---

### Task 3: Server service — skills hide/unhide and list filtering

**Files:**
- Modify: `paperclip/server/src/services/company-skills.ts`

This is a large file (~2390 lines). The service is created by `companySkillService(db)` which returns an object of closures. All internal functions are defined inside this factory.

- [ ] **Step 1: Modify `list()` to filter hidden skills by default**

The `list()` function currently calls `listFull()` then counts attached agents. Change it to accept an `includeHidden` parameter and filter accordingly:

```typescript
  async function list(companyId: string, options: { includeHidden?: boolean } = {}): Promise<CompanySkillListItem[]> {
    const rows = await listFull(companyId);
    const visible = options.includeHidden ? rows : rows.filter(r => !r.hidden);
    const agentRows = await agents.list(companyId);
    return visible.map((skill) => {
      const attachedAgentCount = agentRows.filter((agent) => {
        const desiredSkills = resolveDesiredSkillKeys(rows, agent.adapterConfig as Record<string, unknown>);
        return desiredSkills.includes(skill.key);
      }).length;
      return toCompanySkillListItem(skill, attachedAgentCount);
    });
  }
```

- [ ] **Step 2: Modify `listFull()` to accept `includeHidden`**

```typescript
  async function listFull(companyId: string, options: { includeHidden?: boolean } = {}): Promise<CompanySkill[]> {
    await ensureSkillInventoryCurrent(companyId);
    const conditions = [eq(companySkills.companyId, companyId)];
    if (!options.includeHidden) {
      conditions.push(eq(companySkills.hidden, false));
    }
    const rows = await db
      .select()
      .from(companySkills)
      .where(and(...conditions))
      .orderBy(asc(companySkills.name), asc(companySkills.key));
    return rows.map((row) => toCompanySkill(row));
  }
```

Note: `and` must be imported from `drizzle-orm`. Check if it's already imported at the top of the file — it likely is (used in `deleteSkill`). If not, add it.

- [ ] **Step 3: Add `setVisibility()` function**

Add inside the `companySkillService` factory, before the return statement:

```typescript
  async function setVisibility(companyId: string, skillId: string, hidden: boolean, force?: boolean): Promise<{ skill: CompanySkill; attachedAgentCount: number } | { error: string; attachedAgentCount: number }> {
    const row = await db
      .select()
      .from(companySkills)
      .where(and(eq(companySkills.id, skillId), eq(companySkills.companyId, companyId)))
      .then((rows) => rows[0] ?? null);
    if (!row) throw notFound("Skill not found");

    const skill = toCompanySkill(row);
    const allSkills = await listFull(companyId, { includeHidden: true });
    const agentRows = await agents.list(companyId);
    const attachedAgentCount = agentRows.filter((agent) => {
      const desiredSkills = resolveDesiredSkillKeys(allSkills, agent.adapterConfig as Record<string, unknown>);
      return desiredSkills.includes(skill.key);
    }).length;

    if (hidden && attachedAgentCount > 0 && !force) {
      return { error: "Skill is used by agents", attachedAgentCount };
    }

    if (hidden && attachedAgentCount > 0 && force) {
      for (const agent of agentRows) {
        const config = agent.adapterConfig as Record<string, unknown>;
        const preference = readPaperclipSkillSyncPreference(config);
        const referencesSkill = preference.desiredSkills.some((ref) => {
          const resolved = resolveSkillReference(allSkills, ref);
          return resolved.skill?.id === skillId;
        });
        if (referencesSkill) {
          const filtered = preference.desiredSkills.filter((ref) => {
            const resolved = resolveSkillReference(allSkills, ref);
            return resolved.skill?.id !== skillId;
          });
          await agents.update(agent.id, {
            adapterConfig: writePaperclipSkillSyncPreference(config, filtered),
          });
        }
      }
    }

    await db
      .update(companySkills)
      .set({ hidden, updatedAt: new Date() })
      .where(eq(companySkills.id, skillId));

    return { skill, attachedAgentCount };
  }
```

- [ ] **Step 4: Modify `upsertImportedSkills()` to skip hidden skills**

In the `upsertImportedSkills()` function, find the early-exist check that skips `paperclip_bundled` skills. Add another skip right after it for hidden skills:

```typescript
    if (existing && existing.hidden) {
      out.push(existing);
      continue;
    }
```

Insert this after the existing `paperclip_bundled` skip block:

```typescript
    if (
      existing
      && existingMeta.sourceKind === "paperclip_bundled"
      && incomingKind === "github"
      && incomingOwner === "paperclipai"
      && incomingRepo === "paperclip"
    ) {
      out.push(existing);
      continue;
    }

    if (existing && (existing as CompanySkill & { hidden?: boolean }).hidden) {
      out.push(existing);
      continue;
    }
```

Actually, since `CompanySkill` type now includes `hidden: boolean`, the cast is unnecessary:

```typescript
    if (existing && existing.hidden) {
      out.push(existing);
      continue;
    }
```

- [ ] **Step 5: Modify `listRuntimeSkillEntries()` to skip hidden skills**

In `listRuntimeSkillEntries()`, after `const skills = await listFull(companyId);`, add filter:

```typescript
    const visibleSkills = skills.filter(s => !s.hidden);
```

Then iterate `visibleSkills` instead of `skills`:

```typescript
    const out: PaperclipSkillEntry[] = [];
    for (const skill of visibleSkills) {
```

- [ ] **Step 6: Add `hidden` to `toCompanySkillListItem()` mapping**

The `toCompanySkillListItem` function (around line 1431) manually maps fields. Add `hidden` after `sourcePath`:

```typescript
    sourcePath: source.sourcePath,
    hidden: skill.hidden,
```

- [ ] **Step 7: Add `setVisibility` to the returned object**

Add to the return object of `companySkillService`:

```typescript
    setVisibility,
```

- [ ] **Step 8: Commit**

```bash
git add paperclip/server/src/services/company-skills.ts
git commit -m "feat(server): add skill visibility toggle and hidden filtering"
```

---

### Task 4: Server service — roles hide/unhide and list filtering

**Files:**
- Modify: `paperclip/server/src/services/company-roles.ts`

- [ ] **Step 1: Modify `list()` to filter hidden roles by default and include `hidden` in response**

The current `list()` manually maps each field (lines 57-70). Two changes needed:

1. Add `includeHidden` option and filter query with `and`:
2. Add `hidden: r.hidden` to the returned object

```typescript
    async list(companyId: string, options: { includeHidden?: boolean } = {}) {
      const conditions = [eq(companyRoles.companyId, companyId)];
      if (!options.includeHidden) {
        conditions.push(eq(companyRoles.hidden, false));
      }
      const roles = await db
        .select()
        .from(companyRoles)
        .where(and(...conditions))
        .orderBy(companyRoles.name);
```

In the returned array mapping, add `hidden`:

```typescript
        return roles.map((r) => ({
          id: r.id,
          companyId: r.companyId,
          key: r.key,
          slug: r.slug,
          name: r.name,
          description: r.description,
          category: r.category,
          sourceType: r.sourceType,
          sourcePath: r.sourcePath,
          createdAt: r.createdAt,
          updatedAt: r.updatedAt,
          assignedAgentCount: agentRoleCounts.get(r.key) || 0,
          hidden: r.hidden,
        }));
```

Note: `and` is already imported from `drizzle-orm` (line 1).

- [ ] **Step 2: Add `setVisibility()` function**

The roles service uses `db` directly with Drizzle table `agents` (imported from `@paperclipai/db`). To update an agent's `assignedRole`, use `db.update(agents)`.

Add before the return statement in `companyRoleService`:

```typescript
    async setVisibility(companyId: string, roleId: string, hidden: boolean, force?: boolean) {
      const [row] = await db
        .select()
        .from(companyRoles)
        .where(and(eq(companyRoles.companyId, companyId), eq(companyRoles.id, roleId)));
      if (!row) throw new Error("Role not found");

      const agentRows = await db
        .select({ id: agents.id, name: agents.name, adapterConfig: agents.adapterConfig })
        .from(agents)
        .where(eq(agents.companyId, companyId));

      const assignedAgentCount = agentRows.filter((a) => {
        const config = typeof a.adapterConfig === "object" && a.adapterConfig !== null
          ? (a.adapterConfig as Record<string, unknown>)
          : {};
        return config.assignedRole === row.key;
      }).length;

      if (hidden && assignedAgentCount > 0 && !force) {
        return { error: "Role is assigned to agents", assignedAgentCount };
      }

      if (hidden && assignedAgentCount > 0 && force) {
        for (const agent of agentRows) {
          const config = typeof agent.adapterConfig === "object" && agent.adapterConfig !== null
            ? { ...(agent.adapterConfig as Record<string, unknown>) }
            : {};
          if (config.assignedRole === row.key) {
            delete config.assignedRole;
            await db
              .update(agents)
              .set({ adapterConfig: config })
              .where(eq(agents.id, agent.id));
          }
        }
      }

      await db
        .update(companyRoles)
        .set({ hidden, updatedAt: new Date() })
        .where(eq(companyRoles.id, roleId));

      return { row, assignedAgentCount };
    },
```

- [ ] **Step 3: Modify `importFromSource()` to skip hidden roles**

In the `importFromSource()` function, the `for (const relativePath of paths)` loop does an `insert(...).onConflictDoUpdate(...)`. Before the insert, check if the role exists and is hidden:

```typescript
      for (const relativePath of paths) {
        try {
          // ...existing parsing code...

          const key = `${slugify(source.name)}/${category}/${rawSlug}`;

          // Check if existing role is hidden — skip import
          const [existing] = await db
            .select({ hidden: companyRoles.hidden })
            .from(companyRoles)
            .where(and(eq(companyRoles.companyId, companyId), eq(companyRoles.key, key)));
          if (existing?.hidden) {
            continue;
          }

          // ...existing insert/onConflictDoUpdate code...
```

- [ ] **Step 4: Add `setVisibility` to the returned object**

```typescript
    setVisibility,
```

- [ ] **Step 5: Commit**

```bash
git add paperclip/server/src/services/company-roles.ts
git commit -m "feat(server): add role visibility toggle and hidden filtering"
```

---

### Task 5: Server routes — PATCH endpoints

**Files:**
- Modify: `paperclip/server/src/routes/company-skills.ts`
- Modify: `paperclip/server/src/routes/company-roles.ts`

- [ ] **Step 1: Add PATCH endpoint for skill visibility**

In `paperclip/server/src/routes/company-skills.ts`, add the import for the new schema:

```typescript
import {
  companySkillCreateSchema,
  companySkillFileUpdateSchema,
  companySkillImportSchema,
  companySkillProjectScanRequestSchema,
  companySkillVisibilitySchema,
} from "@paperclipai/shared";
```

Add the route after the existing `router.delete` block for skills (before the `install-update` route):

```typescript
  router.patch(
    "/companies/:companyId/skills/:skillId",
    validate(companySkillVisibilitySchema),
    async (req, res) => {
      const companyId = req.params.companyId as string;
      const skillId = req.params.skillId as string;
      await assertCanMutateCompanySkills(req, companyId);
      const result = await svc.setVisibility(companyId, skillId, req.body.hidden, req.body.force);
      if ("error" in result) {
        res.status(409).json({ error: result.error, attachedAgentCount: result.attachedAgentCount });
        return;
      }

      const actor = getActorInfo(req);
      await logActivity(db, {
        companyId,
        actorType: actor.actorType,
        actorId: actor.actorId,
        agentId: actor.agentId,
        runId: actor.runId,
        action: req.body.hidden ? "company.skill_hidden" : "company.skill_restored",
        entityType: "company_skill",
        entityId: skillId,
        details: { hidden: req.body.hidden },
      });

      res.json({ hidden: req.body.hidden });
    },
  );
```

Also modify the `list` route to pass `includeHidden`:

```typescript
  router.get("/companies/:companyId/skills", async (req, res) => {
    const companyId = req.params.companyId as string;
    assertCompanyAccess(req, companyId);
    const includeHidden = req.query.includeHidden === "true";
    const result = await svc.list(companyId, { includeHidden });
    res.json(result);
  });
```

- [ ] **Step 2: Add PATCH endpoint for role visibility**

In `paperclip/server/src/routes/company-roles.ts`, add the import:

```typescript
import { companyRoleCreateSchema, companyRoleImportSchema, companyRoleVisibilitySchema } from "@paperclipai/shared";
```

Also add the `validate` import if not present (it is already imported in the file).

Add the route after the `router.delete` for roles:

```typescript
  router.patch(
    "/companies/:companyId/roles/:roleId",
    validate(companyRoleVisibilitySchema),
    async (req, res) => {
      const companyId = req.params.companyId as string;
      const roleId = req.params.roleId as string;
      assertCompanyAccess(req, companyId);
      const result = await svc.setVisibility(companyId, roleId, req.body.hidden, req.body.force);
      if ("error" in result) {
        res.status(409).json({ error: result.error, assignedAgentCount: result.assignedAgentCount });
        return;
      }

      const actor = getActorInfo(req);
      await logActivity(db, {
        companyId,
        actorType: actor.actorType,
        actorId: actor.actorId,
        agentId: actor.agentId,
        runId: actor.runId,
        action: req.body.hidden ? "company.role_hidden" : "company.role_restored",
        entityType: "company_role",
        entityId: roleId,
        details: { hidden: req.body.hidden },
      });

      res.json({ hidden: req.body.hidden });
    },
  );
```

Also modify the `list` route to pass `includeHidden`:

```typescript
  router.get("/companies/:companyId/roles", async (req, res) => {
    const companyId = req.params.companyId as string;
    assertCompanyAccess(req, companyId);
    const includeHidden = req.query.includeHidden === "true";
    const roles = await svc.list(companyId, { includeHidden });
    res.json(roles);
  });
```

- [ ] **Step 3: Commit**

```bash
git add paperclip/server/src/routes/company-skills.ts paperclip/server/src/routes/company-roles.ts
git commit -m "feat(server): add PATCH endpoints for skill/role visibility"
```

---

### Task 6: API client updates

**Files:**
- Modify: `paperclip/ui/src/api/companySkills.ts`
- Modify: `paperclip/ui/src/api/roles.ts`

- [ ] **Step 1: Add skill visibility API methods**

In `paperclip/ui/src/api/companySkills.ts`, add after the `list` method:

```typescript
  listIncludingHidden: (companyId: string) =>
    api.get<CompanySkillListItem[]>(`/companies/${encodeURIComponent(companyId)}/skills?includeHidden=true`),
```

Add after the `installUpdate` method:

```typescript
  setVisibility: (companyId: string, skillId: string, hidden: boolean, force?: boolean) =>
    api.patch<{ hidden: boolean } | { error: string; attachedAgentCount: number }>(
      `/companies/${encodeURIComponent(companyId)}/skills/${encodeURIComponent(skillId)}`,
      { hidden, force },
    ),
```

- [ ] **Step 2: Add role visibility API methods**

In `paperclip/ui/src/api/roles.ts`, add to `companyRolesApi` after the `list` method:

```typescript
  listIncludingHidden: (companyId: string) =>
    api.get<CompanyRoleListItem[]>(`/companies/${encodeURIComponent(companyId)}/roles?includeHidden=true`),
```

Add after the `delete` method:

```typescript
  setVisibility: (companyId: string, roleId: string, hidden: boolean, force?: boolean) =>
    api.patch<{ hidden: boolean } | { error: string; assignedAgentCount: number }>(
      `/companies/${encodeURIComponent(companyId)}/roles/${encodeURIComponent(roleId)}`,
      { hidden, force },
    ),
```

- [ ] **Step 3: Commit**

```bash
git add paperclip/ui/src/api/companySkills.ts paperclip/ui/src/api/roles.ts
git commit -m "feat(ui): add API client methods for skill/role visibility"
```

---

### Task 7: UI — Skills page hide/restore

**Files:**
- Modify: `paperclip/ui/src/pages/CompanySkills.tsx`

- [ ] **Step 1: Add state for hidden skills toggle**

In the `CompanySkills` component, add state after the existing state declarations:

```typescript
  const [showHidden, setShowHidden] = useState(false);
  const [confirmHideSkill, setConfirmHideSkill] = useState<{ skillId: string; skillName: string; agentCount: number } | null>(null);
```

- [ ] **Step 2: Replace skillsQuery to use includeHidden based on toggle**

The current query uses `companySkillsApi.list(selectedCompanyId!)`. Replace the `queryFn` to use the appropriate method:

```typescript
  const skillsQuery = useQuery({
    queryKey: queryKeys.companySkills.list(selectedCompanyId ?? ""),
    queryFn: () => showHidden
      ? companySkillsApi.listIncludingHidden(selectedCompanyId!)
      : companySkillsApi.list(selectedCompanyId!),
    enabled: Boolean(selectedCompanyId),
  });
```

- [ ] **Step 3: Add visibility toggle mutation**

Add after the existing `deleteSource` mutation:

```typescript
  const setSkillVisibility = useMutation({
    mutationFn: ({ skillId, hidden, force }: { skillId: string; hidden: boolean; force?: boolean }) =>
      companySkillsApi.setVisibility(selectedCompanyId!, skillId, hidden, force),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.companySkills.list(selectedCompanyId!) });
      setConfirmHideSkill(null);
      if ("error" in result) return;
      pushToast({
        tone: "success",
        title: result.hidden ? "Skill excluded" : "Skill restored",
      });
    },
    onError: (error) => {
      pushToast({
        tone: "error",
        title: "Failed to update skill visibility",
        body: error instanceof Error ? error.message : "Unknown error",
      });
    },
  });
```

- [ ] **Step 4: Add show-hidden toggle to the left panel header**

Find the `<aside>` section header where the search bar is. After the search input section, add a toggle:

```tsx
            <div className="mt-3 flex items-center gap-2 border-b border-border pb-2">
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={showHidden}
                  onChange={(e) => setShowHidden(e.target.checked)}
                  className="rounded"
                />
                Show excluded
              </label>
            </div>
```

- [ ] **Step 5: Pass hide/restore handlers to SourceGroupedList**

In the `SourceGroupedList` component props, add:

```typescript
  onHideSkill: (skillId: string, skillName: string, agentCount: number) => void;
  onRestoreSkill: (skillId: string) => void;
```

And pass them from the parent:

```typescript
              onHideSkill={(skillId, skillName, agentCount) => setConfirmHideSkill({ skillId, skillName, agentCount })}
              onRestoreSkill={(skillId) => setSkillVisibility.mutate({ skillId, hidden: false })}
```

- [ ] **Step 6: Modify SkillList items to show hide/restore buttons**

In the `SkillList` component, each skill item already has a tree toggle button area. Add a kebab menu or inline buttons for hidden skills. Find the skill item render (the `<button>` for each skill in `SkillList`) and modify:

For hidden skills, wrap the name in a span with dimmed styling and add a "Restore" button:

```tsx
<button
  key={skill.id}
  type="button"
  className={cn(
    "flex w-full items-center gap-2 px-4 py-1.5 text-left text-[13px] hover:bg-accent/30",
    selectedSkillId === skill.id && "bg-accent/20 text-foreground",
    skill.hidden && "opacity-50",
  )}
  onClick={() => onSelectSkill(skill.id)}
>
  {skill.hidden ? (
    <EyeOff className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
  ) : (
    <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
  )}
  <span className={cn("min-w-0 truncate font-medium", skill.hidden && "line-through")}>{skill.name}</span>
  {skill.hidden && (
    <button
      type="button"
      className="ml-auto text-xs text-muted-foreground hover:text-foreground"
      onClick={(e) => { e.stopPropagation(); onRestoreSkill(skill.id); }}
    >
      Restore
    </button>
  )}
  {!skill.hidden && (
    <button
      type="button"
      className="ml-auto opacity-0 group-hover:opacity-70 text-muted-foreground hover:text-foreground"
      onClick={(e) => { e.stopPropagation(); onHideSkill(skill.id, skill.name, skill.attachedAgentCount); }}
      title="Exclude from sync"
    >
      <EyeOff className="h-3.5 w-3.5" />
    </button>
  )}
</button>
```

Note: The `SkillList` component's props need to be extended with `onHideSkill` and `onRestoreSkill`. The `CompanySkillListItem` type now includes `hidden: boolean`.

- [ ] **Step 7: Add confirmation dialog for hiding skills**

Add before the closing `</>` of the `CompanySkills` component, after the existing delete source dialog:

```tsx
      <Dialog open={confirmHideSkill !== null} onOpenChange={() => setConfirmHideSkill(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Exclude skill from sync</DialogTitle>
            <DialogDescription>
              This skill will be excluded from synchronization and agent provisioning.
              {confirmHideSkill && confirmHideSkill.agentCount > 0 && (
                <> It is currently used by <strong>{confirmHideSkill.agentCount} agent{confirmHideSkill.agentCount === 1 ? "" : "s"}</strong> and will be removed from their configuration.</>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmHideSkill(null)}>Cancel</Button>
            <Button variant="destructive" onClick={() => {
              if (confirmHideSkill) {
                setSkillVisibility.mutate({
                  skillId: confirmHideSkill.skillId,
                  hidden: true,
                  force: confirmHideSkill.agentCount > 0,
                });
              }
            }} disabled={setSkillVisibility.isPending}>
              {setSkillVisibility.isPending ? "Excluding..." : "Exclude"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
```

Note: `DialogDescription` import needs to be checked — it's already imported from `@/components/ui/dialog`.

- [ ] **Step 8: Commit**

```bash
git add paperclip/ui/src/pages/CompanySkills.tsx
git commit -m "feat(ui): add skill hide/restore to CompanySkills page"
```

---

### Task 8: UI — Roles page hide/restore

**Files:**
- Modify: `paperclip/ui/src/pages/CompanyRoles.tsx`

- [ ] **Step 1: Add imports**

Add `EyeOff` to the lucide-react imports:

```typescript
import { ChevronRight, ChevronDown, Folder, FolderOpen, Plus, Trash2, Download, Search, Users, EyeOff } from "lucide-react";
```

Add `DialogDescription` to the dialog imports if not present:

```typescript
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
```

- [ ] **Step 2: Add state**

In `CompanyRoles` component, add after existing state:

```typescript
  const [showHidden, setShowHidden] = useState(false);
  const [confirmHideRole, setConfirmHideRole] = useState<{ roleId: string; roleName: string; agentCount: number } | null>(null);
```

- [ ] **Step 3: Replace rolesQuery to use includeHidden**

```typescript
  const rolesQuery = useQuery({
    queryKey: queryKeys.companyRoles.list(selectedCompanyId ?? ""),
    queryFn: () => showHidden
      ? companyRolesApi.listIncludingHidden(selectedCompanyId!)
      : companyRolesApi.list(selectedCompanyId!),
    enabled: Boolean(selectedCompanyId),
  });
```

- [ ] **Step 4: Add visibility toggle mutation**

After `deleteRoleMutation`:

```typescript
  const setRoleVisibility = useMutation({
    mutationFn: ({ roleId, hidden, force }: { roleId: string; hidden: boolean; force?: boolean }) =>
      companyRolesApi.setVisibility(selectedCompanyId!, roleId, hidden, force),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.companyRoles.list(selectedCompanyId!) });
      setConfirmHideRole(null);
      if ("error" in result) return;
      pushToast({
        tone: "success",
        title: result.hidden ? "Role excluded" : "Role restored",
      });
    },
    onError: (error) => {
      pushToast({
        tone: "error",
        title: "Failed to update role visibility",
        body: error instanceof Error ? error.message : "Unknown error",
      });
    },
  });
```

- [ ] **Step 5: Add show-hidden toggle to left panel**

After the search input section in the left panel header:

```tsx
            <div className="mt-3 flex items-center gap-2 border-b border-border pb-2">
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={showHidden}
                  onChange={(e) => setShowHidden(e.target.checked)}
                  className="rounded"
                />
                Show excluded
              </label>
            </div>
```

- [ ] **Step 6: Modify role list items**

In the roles list by category, modify each role button to handle hidden state:

```tsx
              {roles.map((role) => (
                <div key={role.id} className="group flex items-center">
                  <button
                    type="button"
                    className={cn(
                      "flex-1 flex items-center gap-2 px-4 py-2 text-left text-sm hover:bg-accent/30 transition-colors",
                      role.id === selectedRoleId && "bg-accent text-foreground",
                      role.hidden && "opacity-50",
                    )}
                    onClick={() => setSelectedRoleId(role.id)}
                  >
                    {role.hidden ? (
                      <EyeOff className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    ) : (
                      <Users className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    )}
                    <span className={cn("truncate flex-1", role.hidden && "line-through")}>{role.name}</span>
                    {role.assignedAgentCount > 0 && !role.hidden && (
                      <span className="text-xs text-muted-foreground">{role.assignedAgentCount}</span>
                    )}
                  </button>
                  {role.hidden ? (
                    <button
                      type="button"
                      className="px-3 py-2 text-xs text-muted-foreground hover:text-foreground"
                      onClick={() => setRoleVisibility.mutate({ roleId: role.id, hidden: false })}
                    >
                      Restore
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="px-3 py-2 text-muted-foreground opacity-0 group-hover:opacity-70 hover:text-foreground"
                      onClick={() => setConfirmHideRole({ roleId: role.id, roleName: role.name, agentCount: role.assignedAgentCount })}
                      title="Exclude from sync"
                    >
                      <EyeOff className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              ))}
```

- [ ] **Step 7: Add confirmation dialog**

Add before the closing `</div>` of the component:

```tsx
      <Dialog open={confirmHideRole !== null} onOpenChange={() => setConfirmHideRole(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Exclude role from sync</DialogTitle>
            <DialogDescription>
              This role will be excluded from synchronization and agent provisioning.
              {confirmHideRole && confirmHideRole.agentCount > 0 && (
                <> It is currently assigned to <strong>{confirmHideRole.agentCount} agent{confirmHideRole.agentCount === 1 ? "" : "s"}</strong> and will be unassigned.</>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmHideRole(null)}>Cancel</Button>
            <Button variant="destructive" onClick={() => {
              if (confirmHideRole) {
                setRoleVisibility.mutate({
                  roleId: confirmHideRole.roleId,
                  hidden: true,
                  force: confirmHideRole.agentCount > 0,
                });
              }
            }} disabled={setRoleVisibility.isPending}>
              {setRoleVisibility.isPending ? "Excluding..." : "Exclude"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
```

- [ ] **Step 8: Commit**

```bash
git add paperclip/ui/src/pages/CompanyRoles.tsx
git commit -m "feat(ui): add role hide/restore to CompanyRoles page"
```

---

### Task 9: Verification and deployment

- [ ] **Step 1: Run typecheck**

```bash
cd paperclip && pnpm -r typecheck
```

Fix any type errors.

- [ ] **Step 2: Run build**

```bash
cd paperclip && pnpm build
```

- [ ] **Step 3: Apply migration in the running instance**

Copy migration to the container and apply:

```bash
docker cp paperclip/packages/db/src/migrations/0054_hidden_skills_roles.sql paperclip-db:/tmp/0054_hidden_skills_roles.sql
docker exec paperclip-db psql -U postgres -d paperclip -f /tmp/0054_hidden_skills_roles.sql
```

- [ ] **Step 4: Rebuild and deploy paperclip-server**

```bash
docker compose up -d --force-recreate --build paperclip-server
```

- [ ] **Step 5: Deploy UI changes**

Build UI in container:

```bash
docker exec -w /app/ui paperclip-server node node_modules/vite/bin/vite.js build
```

Bump service worker cache if needed.
