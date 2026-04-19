# Roles System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Company-scoped role library with CRUD, git-based import, and agent creation integration.

**Architecture:** Two new DB tables (`role_sources`, `company_roles`), service layer, API routes, UI pages (Roles management + role picker in NewAgent), agent linkage via `adapterConfig.assignedRole`.

**Tech Stack:** Drizzle ORM (schema + migrations), Zod (validators), Express routes, React + TanStack Query (UI), simple-git for repo cloning.

---

### Task 1: DB Schema — role_sources and company_roles tables

**Files:**
- Create: `paperclip/packages/db/src/schema/role_sources.ts`
- Create: `paperclip/packages/db/src/schema/company_roles.ts`
- Modify: `paperclip/packages/db/src/schema/index.ts`

- [ ] **Step 1: Create `role_sources.ts`**

```ts
import {
  pgTable,
  uuid,
  text,
  timestamp,
  uniqueIndex,
} from "drizzle-orm/pg-core";
import { companies } from "./companies.js";

export const roleSources = pgTable(
  "role_sources",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    companyId: uuid("company_id").notNull().references(() => companies.id),
    name: text("name").notNull(),
    url: text("url").notNull(),
    ref: text("ref").notNull().default("main"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    companyUrlUniqueIdx: uniqueIndex("role_sources_company_url_idx").on(
      table.companyId,
      table.url,
    ),
  }),
);
```

- [ ] **Step 2: Create `company_roles.ts`**

```ts
import {
  pgTable,
  uuid,
  text,
  timestamp,
  jsonb,
  index,
  uniqueIndex,
} from "drizzle-orm/pg-core";
import { companies } from "./companies.js";
import { roleSources } from "./role_sources.js";

export const companyRoles = pgTable(
  "company_roles",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    companyId: uuid("company_id").notNull().references(() => companies.id),
    sourceId: uuid("source_id").references(() => roleSources.id),
    key: text("key").notNull(),
    slug: text("slug").notNull(),
    name: text("name").notNull(),
    description: text("description"),
    category: text("category"),
    markdown: text("markdown").notNull(),
    sourceType: text("source_type").notNull().default("local"),
    sourceRef: text("source_ref"),
    sourcePath: text("source_path"),
    metadata: jsonb("metadata").$type<Record<string, unknown>>(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    companyKeyUniqueIdx: uniqueIndex("company_roles_company_key_idx").on(
      table.companyId,
      table.key,
    ),
    companyNameIdx: index("company_roles_company_name_idx").on(
      table.companyId,
      table.name,
    ),
  }),
);
```

- [ ] **Step 3: Export from `schema/index.ts`**

Add these lines to `paperclip/packages/db/src/schema/index.ts`:

```ts
export { roleSources } from "./role_sources.js";
export { companyRoles } from "./company_roles.js";
```

- [ ] **Step 4: Compile and generate migration**

```bash
cd paperclip && pnpm --filter @paperclipai/db build && pnpm --filter @paperclipai/db db:generate
```

- [ ] **Step 5: Run migration against dev DB**

```bash
cd paperclip && pnpm --filter @paperclipai/db db:migrate
```

- [ ] **Step 6: Commit**

```bash
git add paperclip/packages/db/src/schema/role_sources.ts paperclip/packages/db/src/schema/company_roles.ts paperclip/packages/db/src/schema/index.ts paperclip/packages/db/src/migrations/
git commit -m "feat(roles): add role_sources and company_roles DB schema"
```

---

### Task 2: Shared Types — role types and validators

**Files:**
- Create: `paperclip/packages/shared/src/types/role.ts`
- Create: `paperclip/packages/shared/src/validators/role.ts`
- Modify: `paperclip/packages/shared/src/types/index.ts`
- Modify: `paperclip/packages/shared/src/validators/index.ts`

- [ ] **Step 1: Create `types/role.ts`**

```ts
export type RoleSourceType = "git" | "local";

export interface RoleSource {
  id: string;
  companyId: string;
  name: string;
  url: string;
  ref: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface CompanyRole {
  id: string;
  companyId: string;
  sourceId: string | null;
  key: string;
  slug: string;
  name: string;
  description: string | null;
  category: string | null;
  markdown: string;
  sourceType: RoleSourceType;
  sourceRef: string | null;
  sourcePath: string | null;
  metadata: Record<string, unknown> | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface CompanyRoleListItem {
  id: string;
  companyId: string;
  key: string;
  slug: string;
  name: string;
  description: string | null;
  category: string | null;
  sourceType: RoleSourceType;
  sourcePath: string | null;
  createdAt: Date;
  updatedAt: Date;
  assignedAgentCount: number;
}

export interface CompanyRoleDetail extends CompanyRole {
  assignedAgentCount: number;
  usedByAgents: CompanyRoleUsageAgent[];
}

export interface CompanyRoleUsageAgent {
  id: string;
  name: string;
  urlKey: string;
}

export interface RoleSourceBrowseEntry {
  path: string;
  name: string;
  description: string | null;
  category: string;
}

export interface RoleSourceBrowseResult {
  sourceId: string;
  categories: {
    name: string;
    entries: RoleSourceBrowseEntry[];
  }[];
}

export interface CompanyRoleCreateRequest {
  name: string;
  slug?: string | null;
  description?: string | null;
  category?: string | null;
  markdown: string;
}

export interface CompanyRoleImportRequest {
  sourceId: string;
  paths: string[];
}

export interface CompanyRoleImportResult {
  imported: CompanyRole[];
  warnings: string[];
}

export interface RoleSourceCreateRequest {
  name: string;
  url: string;
  ref?: string | null;
}
```

- [ ] **Step 2: Create `validators/role.ts`**

```ts
import { z } from "zod";

export const roleSourceTypeSchema = z.enum(["git", "local"]);

export const roleSourceSchema = z.object({
  id: z.string().uuid(),
  companyId: z.string().uuid(),
  name: z.string().min(1),
  url: z.string().min(1),
  ref: z.string().min(1),
  createdAt: z.coerce.date(),
  updatedAt: z.coerce.date(),
});

export const companyRoleSchema = z.object({
  id: z.string().uuid(),
  companyId: z.string().uuid(),
  sourceId: z.string().uuid().nullable(),
  key: z.string().min(1),
  slug: z.string().min(1),
  name: z.string().min(1),
  description: z.string().nullable(),
  category: z.string().nullable(),
  markdown: z.string(),
  sourceType: roleSourceTypeSchema,
  sourceRef: z.string().nullable(),
  sourcePath: z.string().nullable(),
  metadata: z.record(z.unknown()).nullable(),
  createdAt: z.coerce.date(),
  updatedAt: z.coerce.date(),
});

export const companyRoleListItemSchema = z.object({
  id: z.string().uuid(),
  companyId: z.string().uuid(),
  key: z.string().min(1),
  slug: z.string().min(1),
  name: z.string().min(1),
  description: z.string().nullable(),
  category: z.string().nullable(),
  sourceType: roleSourceTypeSchema,
  sourcePath: z.string().nullable(),
  createdAt: z.coerce.date(),
  updatedAt: z.coerce.date(),
  assignedAgentCount: z.number().int().nonnegative(),
});

export const companyRoleUsageAgentSchema = z.object({
  id: z.string().uuid(),
  name: z.string().min(1),
  urlKey: z.string().min(1),
});

export const companyRoleDetailSchema = companyRoleSchema.extend({
  assignedAgentCount: z.number().int().nonnegative(),
  usedByAgents: z.array(companyRoleUsageAgentSchema).default([]),
});

export const roleSourceBrowseEntrySchema = z.object({
  path: z.string().min(1),
  name: z.string().min(1),
  description: z.string().nullable(),
  category: z.string().min(1),
});

export const roleSourceBrowseResultSchema = z.object({
  sourceId: z.string().uuid(),
  categories: z.array(
    z.object({
      name: z.string().min(1),
      entries: z.array(roleSourceBrowseEntrySchema),
    }),
  ),
});

export const companyRoleCreateSchema = z.object({
  name: z.string().min(1),
  slug: z.string().min(1).nullable().optional(),
  description: z.string().nullable().optional(),
  category: z.string().nullable().optional(),
  markdown: z.string().min(1),
});

export const companyRoleImportSchema = z.object({
  sourceId: z.string().uuid(),
  paths: z.array(z.string().min(1)).min(1),
});

export const companyRoleImportResultSchema = z.object({
  imported: z.array(companyRoleSchema),
  warnings: z.array(z.string()),
});

export const roleSourceCreateSchema = z.object({
  name: z.string().min(1),
  url: z.string().min(1),
  ref: z.string().min(1).nullable().optional(),
});

export type RoleSourceCreate = z.infer<typeof roleSourceCreateSchema>;
export type CompanyRoleCreate = z.infer<typeof companyRoleCreateSchema>;
export type CompanyRoleImport = z.infer<typeof companyRoleImportSchema>;
```

- [ ] **Step 3: Add exports to `types/index.ts`**

Add to `paperclip/packages/shared/src/types/index.ts`:

```ts
export type {
  RoleSourceType,
  RoleSource,
  CompanyRole,
  CompanyRoleListItem,
  CompanyRoleDetail,
  CompanyRoleUsageAgent,
  RoleSourceBrowseEntry,
  RoleSourceBrowseResult,
  CompanyRoleCreateRequest,
  CompanyRoleImportRequest,
  CompanyRoleImportResult,
  RoleSourceCreateRequest,
} from "./role.js";
```

- [ ] **Step 4: Add exports to `validators/index.ts`**

Add to `paperclip/packages/shared/src/validators/index.ts`:

```ts
export {
  roleSourceTypeSchema,
  roleSourceSchema,
  companyRoleSchema,
  companyRoleListItemSchema,
  companyRoleUsageAgentSchema,
  companyRoleDetailSchema,
  roleSourceBrowseEntrySchema,
  roleSourceBrowseResultSchema,
  companyRoleCreateSchema,
  companyRoleImportSchema,
  companyRoleImportResultSchema,
  roleSourceCreateSchema,
  type RoleSourceCreate,
  type CompanyRoleCreate,
  type CompanyRoleImport,
} from "./role.js";
```

- [ ] **Step 5: Compile shared package**

```bash
cd paperclip && pnpm --filter @paperclipai/shared build
```

- [ ] **Step 6: Commit**

```bash
git add paperclip/packages/shared/src/types/role.ts paperclip/packages/shared/src/validators/role.ts paperclip/packages/shared/src/types/index.ts paperclip/packages/shared/src/validators/index.ts
git commit -m "feat(roles): add shared types and validators"
```

---

### Task 3: Service Layer — role sources

**Files:**
- Create: `paperclip/server/src/services/role-sources.ts`

- [ ] **Step 1: Create `role-sources.ts` service**

The service handles CRUD for role sources + git clone/browse logic.

```ts
import { eq, and } from "drizzle-orm";
import { mkdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { simpleGit } from "simple-git";
import { roleSources } from "@paperclipai/db";
import type { Db } from "@paperclipai/db";

const ROLE_SOURCES_DIR = process.env.ROLE_SOURCES_DIR || "/tmp/role-sources";

function sourceCloneDir(sourceId: string) {
  return join(ROLE_SOURCES_DIR, sourceId);
}

export function roleSourceService(db: Db) {
  return {
    async list(companyId: string) {
      return db
        .select()
        .from(roleSources)
        .where(eq(roleSources.companyId, companyId))
        .orderBy(roleSources.name);
    },

    async getById(companyId: string, sourceId: string) {
      const [row] = await db
        .select()
        .from(roleSources)
        .where(and(eq(roleSources.companyId, companyId), eq(roleSources.id, sourceId)));
      return row ?? null;
    },

    async create(companyId: string, data: { name: string; url: string; ref: string }) {
      const [row] = await db
        .insert(roleSources)
        .values({ companyId, ...data })
        .returning();
      return row;
    },

    async delete(companyId: string, sourceId: string) {
      await db
        .delete(roleSources)
        .where(and(eq(roleSources.companyId, companyId), eq(roleSources.id, sourceId)));
      try {
        await rm(sourceCloneDir(sourceId), { recursive: true, force: true });
      } catch {}
    },

    async browse(companyId: string, sourceId: string) {
      const source = await this.getById(companyId, sourceId);
      if (!source) throw new Error("Source not found");

      const cloneDir = sourceCloneDir(sourceId);
      const git = simpleGit();

      try {
        await mkdir(cloneDir, { recursive: true });
        const isRepo = await git.cwd(cloneDir).checkIsRepo();
        if (isRepo) {
          await git.cwd(cloneDir).fetch("origin");
          await git.cwd(cloneDir).checkout(source.ref);
          await git.cwd(cloneDir).pull("origin", source.ref);
        } else {
          await rm(cloneDir, { recursive: true, force: true });
          await git.clone(source.url, cloneDir, ["--branch", source.ref, "--depth", "1"]);
        }
      } catch {
        await rm(cloneDir, { recursive: true, force: true });
        await git.clone(source.url, cloneDir, ["--depth", "1"]);
        await git.cwd(cloneDir).checkout(source.ref);
      }

      const { readdir, readFile, stat } = await import("node:fs/promises");
      const categories: { name: string; entries: { path: string; name: string; description: string | null; category: string }[] }[] = [];
      const topDirs = (await readdir(cloneDir)).filter((d) => !d.startsWith(".") && d !== "node_modules");

      for (const dir of topDirs) {
        const fullDir = join(cloneDir, dir);
        const dirStat = await stat(fullDir);
        if (!dirStat.isDirectory()) continue;

        const entries: { path: string; name: string; description: string | null; category: string }[] = [];
        const files = (await readdir(fullDir)).filter((f) => f.endsWith(".md"));

        for (const file of files) {
          const filePath = join(fullDir, file);
          const content = await readFile(filePath, "utf8");
          const parsed = parseFrontmatter(content);
          const relativePath = `${dir}/${file}`;
          entries.push({
            path: relativePath,
            name: parsed.name || file.replace(/\.md$/, ""),
            description: parsed.description || null,
            category: dir,
          });
        }

        if (entries.length > 0) {
          categories.push({ name: dir, entries });
        }
      }

      return { sourceId, categories };
    },

    async readFileFromSource(sourceId: string, relativePath: string) {
      const cloneDir = sourceCloneDir(sourceId);
      const { readFile } = await import("node:fs/promises");
      const filePath = join(cloneDir, relativePath);
      return readFile(filePath, "utf8");
    },
  };
}

function parseFrontmatter(content: string): Record<string, string> {
  const result: Record<string, string> = {};
  const normalized = content.replace(/\r\n/g, "\n");
  if (!normalized.startsWith("---\n")) return result;
  const closing = normalized.indexOf("\n---\n", 4);
  if (closing < 0) return result;
  const frontmatter = normalized.slice(4, closing);
  for (const line of frontmatter.split("\n")) {
    const colonIdx = line.indexOf(":");
    if (colonIdx < 0) continue;
    const key = line.slice(0, colonIdx).trim();
    const val = line.slice(colonIdx + 1).trim();
    result[key] = val;
  }
  return result;
}
```

- [ ] **Step 2: Install simple-git dependency**

```bash
cd paperclip/server && pnpm add simple-git
```

- [ ] **Step 3: Commit**

```bash
git add paperclip/server/src/services/role-sources.ts paperclip/server/package.json paperclip/pnpm-lock.yaml
git commit -m "feat(roles): add role-sources service with git clone/browse"
```

---

### Task 4: Service Layer — company roles

**Files:**
- Create: `paperclip/server/src/services/company-roles.ts`

- [ ] **Step 1: Create `company-roles.ts` service**

```ts
import { eq, and, sql } from "drizzle-orm";
import { companyRoles, roleSources, agents } from "@paperclipai/db";
import type { Db } from "@paperclipai/db";
import { roleSourceService } from "./role-sources.js";

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function parseFrontmatter(content: string): { frontmatter: Record<string, string>; body: string } {
  const result: Record<string, string> = {};
  const normalized = content.replace(/\r\n/g, "\n");
  if (!normalized.startsWith("---\n")) return { frontmatter: result, body: content };
  const closing = normalized.indexOf("\n---\n", 4);
  if (closing < 0) return { frontmatter: result, body: content };
  const frontmatter = normalized.slice(4, closing);
  for (const line of frontmatter.split("\n")) {
    const colonIdx = line.indexOf(":");
    if (colonIdx < 0) continue;
    const key = line.slice(0, colonIdx).trim();
    const val = line.slice(colonIdx + 1).trim();
    result[key] = val;
  }
  return { frontmatter: result, body: normalized.slice(closing + 5) };
}

export function companyRoleService(db: Db) {
  const sources = roleSourceService(db);

  return {
    async list(companyId: string) {
      const roles = await db
        .select()
        .from(companyRoles)
        .where(eq(companyRoles.companyId, companyId))
        .orderBy(companyRoles.name);

      const agentRows = await db
        .select({ adapterConfig: agents.adapterConfig })
        .from(agents)
        .where(eq(agents.companyId, companyId));

      const agentRoleCounts = new Map<string, number>();
      for (const row of agentRows) {
        const config = typeof row.adapterConfig === "object" && row.adapterConfig !== null
          ? (row.adapterConfig as Record<string, unknown>)
          : {};
        const roleKey = config.assignedRole;
        if (typeof roleKey === "string" && roleKey) {
          agentRoleCounts.set(roleKey, (agentRoleCounts.get(roleKey) || 0) + 1);
        }
      }

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
      }));
    },

    async detail(companyId: string, roleId: string) {
      const [row] = await db
        .select()
        .from(companyRoles)
        .where(and(eq(companyRoles.companyId, companyId), eq(companyRoles.id, roleId)));
      if (!row) return null;

      const agentRows = await db
        .select({ id: agents.id, name: agents.name, urlKey: agents.urlKey, adapterConfig: agents.adapterConfig })
        .from(agents)
        .where(eq(agents.companyId, companyId));

      const usedByAgents = agentRows.filter((a) => {
        const config = typeof a.adapterConfig === "object" && a.adapterConfig !== null
          ? (a.adapterConfig as Record<string, unknown>)
          : {};
        return config.assignedRole === row.key;
      }).map((a) => ({ id: a.id, name: a.name, urlKey: a.urlKey }));

      return {
        ...row,
        assignedAgentCount: usedByAgents.length,
        usedByAgents,
      };
    },

    async getByKey(companyId: string, key: string) {
      const [row] = await db
        .select()
        .from(companyRoles)
        .where(and(eq(companyRoles.companyId, companyId), eq(companyRoles.key, key)));
      return row ?? null;
    },

    async create(companyId: string, data: { name: string; slug?: string | null; description?: string | null; category?: string | null; markdown: string }) {
      const slug = data.slug || slugify(data.name);
      const key = `local/${slug}`;
      const [row] = await db
        .insert(companyRoles)
        .values({
          companyId,
          key,
          slug,
          name: data.name,
          description: data.description || null,
          category: data.category || null,
          markdown: data.markdown,
          sourceType: "local",
        })
        .returning();
      return row;
    },

    async update(companyId: string, roleId: string, data: Partial<{ name: string; description: string | null; category: string | null; markdown: string }>) {
      const [row] = await db
        .update(companyRoles)
        .set({ ...data, updatedAt: new Date() })
        .where(and(eq(companyRoles.companyId, companyId), eq(companyRoles.id, roleId)))
        .returning();
      return row ?? null;
    },

    async deleteRole(companyId: string, roleId: string) {
      await db
        .delete(companyRoles)
        .where(and(eq(companyRoles.companyId, companyId), eq(companyRoles.id, roleId)));
    },

    async importFromSource(companyId: string, sourceId: string, paths: string[]) {
      const source = await sources.getById(companyId, sourceId);
      if (!source) throw new Error("Source not found");

      const imported: typeof companyRoles.$inferSelect[] = [];
      const warnings: string[] = [];

      for (const relativePath of paths) {
        try {
          const content = await sources.readFileFromSource(sourceId, relativePath);
          const { frontmatter } = parseFrontmatter(content);

          const fileName = relativePath.split("/").pop() || relativePath;
          const rawSlug = slugify(frontmatter.name || fileName.replace(/\.md$/, ""));
          const category = relativePath.split("/")[0] || "uncategorized";
          const key = `${slugify(source.name)}/${category}/${rawSlug}`;

          const [row] = await db
            .insert(companyRoles)
            .values({
              companyId,
              sourceId,
              key,
              slug: rawSlug,
              name: frontmatter.name || fileName.replace(/\.md$/, ""),
              description: frontmatter.description || null,
              category,
              markdown: content,
              sourceType: "git",
              sourceRef: source.ref,
              sourcePath: relativePath,
              metadata: Object.keys(frontmatter).length > 0 ? frontmatter : null,
            })
            .onConflictDoUpdate({
              target: [companyRoles.companyId, companyRoles.key],
              set: {
                name: frontmatter.name || fileName.replace(/\.md$/, ""),
                description: frontmatter.description || null,
                markdown: content,
                sourceRef: source.ref,
                sourcePath: relativePath,
                metadata: Object.keys(frontmatter).length > 0 ? frontmatter : null,
                updatedAt: new Date(),
              },
            })
            .returning();

          if (row) imported.push(row);
        } catch (err) {
          warnings.push(`Failed to import ${relativePath}: ${err instanceof Error ? err.message : String(err)}`);
        }
      }

      return { imported, warnings };
    },

    async resolveRoleKey(companyId: string, ref: string) {
      const [byId] = await db
        .select()
        .from(companyRoles)
        .where(and(eq(companyRoles.companyId, companyId), eq(companyRoles.id, ref)));
      if (byId) return byId.key;

      const [byKey] = await db
        .select()
        .from(companyRoles)
        .where(and(eq(companyRoles.companyId, companyId), eq(companyRoles.key, ref)));
      if (byKey) return byKey.key;

      const [bySlug] = await db
        .select()
        .from(companyRoles)
        .where(and(eq(companyRoles.companyId, companyId), eq(companyRoles.slug, ref)));
      if (bySlug) return bySlug.key;

      return null;
    },
  };
}
```

- [ ] **Step 2: Commit**

```bash
git add paperclip/server/src/services/company-roles.ts
git commit -m "feat(roles): add company-roles service with CRUD and import"
```

---

### Task 5: API Routes — role sources and company roles

**Files:**
- Create: `paperclip/server/src/routes/role-sources.ts`
- Create: `paperclip/server/src/routes/company-roles.ts`
- Modify: `paperclip/server/src/routes/index.ts` (register new routers)

- [ ] **Step 1: Create `role-sources.ts` routes**

```ts
import { Router } from "express";
import { validate } from "../middleware/validate.js";
import { roleSourceCreateSchema } from "@paperclipai/shared";
import { roleSourceService } from "../services/role-sources.js";
import { assertCompanyAccess } from "../authz.js";
import { logActivity } from "../services/activity-log.js";
import type { Db } from "@paperclipai/db";

export function roleSourceRoutes(db: Db) {
  const router = Router();
  const svc = roleSourceService(db);

  router.get("/companies/:companyId/role-sources", async (req, res) => {
    const companyId = req.params.companyId as string;
    await assertCompanyAccess(req, companyId);
    const sources = await svc.list(companyId);
    res.json(sources);
  });

  router.post(
    "/companies/:companyId/role-sources",
    validate(roleSourceCreateSchema),
    async (req, res) => {
      const companyId = req.params.companyId as string;
      await assertCompanyAccess(req, companyId);
      const ref = req.body.ref || "main";
      const source = await svc.create(companyId, { name: req.body.name, url: req.body.url, ref });
      await logActivity(db, {
        companyId,
        actorType: req.actor.type,
        actorId: req.actor.id,
        action: "company.role_source_added",
        entityType: "company",
        entityId: companyId,
        details: { sourceId: source.id, url: source.url },
      });
      res.status(201).json(source);
    },
  );

  router.delete("/companies/:companyId/role-sources/:sourceId", async (req, res) => {
    const companyId = req.params.companyId as string;
    const sourceId = req.params.sourceId as string;
    await assertCompanyAccess(req, companyId);
    await svc.delete(companyId, sourceId);
    await logActivity(db, {
      companyId,
      actorType: req.actor.type,
      actorId: req.actor.id,
      action: "company.role_source_removed",
      entityType: "company",
      entityId: companyId,
      details: { sourceId },
    });
    res.status(204).end();
  });

  router.get("/companies/:companyId/role-sources/:sourceId/browse", async (req, res) => {
    const companyId = req.params.companyId as string;
    const sourceId = req.params.sourceId as string;
    await assertCompanyAccess(req, companyId);
    const result = await svc.browse(companyId, sourceId);
    res.json(result);
  });

  return router;
}
```

- [ ] **Step 2: Create `company-roles.ts` routes**

```ts
import { Router } from "express";
import { validate } from "../middleware/validate.js";
import { companyRoleCreateSchema, companyRoleImportSchema } from "@paperclipai/shared";
import { companyRoleService } from "../services/company-roles.js";
import { assertCompanyAccess } from "../authz.js";
import { logActivity } from "../services/activity-log.js";
import type { Db } from "@paperclipai/db";

export function companyRoleRoutes(db: Db) {
  const router = Router();
  const svc = companyRoleService(db);

  router.get("/companies/:companyId/roles", async (req, res) => {
    const companyId = req.params.companyId as string;
    await assertCompanyAccess(req, companyId);
    const roles = await svc.list(companyId);
    res.json(roles);
  });

  router.get("/companies/:companyId/roles/:roleId", async (req, res) => {
    const companyId = req.params.companyId as string;
    const roleId = req.params.roleId as string;
    await assertCompanyAccess(req, companyId);
    const role = await svc.detail(companyId, roleId);
    if (!role) {
      res.status(404).json({ error: "Role not found" });
      return;
    }
    res.json(role);
  });

  router.post(
    "/companies/:companyId/roles",
    validate(companyRoleCreateSchema),
    async (req, res) => {
      const companyId = req.params.companyId as string;
      await assertCompanyAccess(req, companyId);
      const role = await svc.create(companyId, req.body);
      await logActivity(db, {
        companyId,
        actorType: req.actor.type,
        actorId: req.actor.id,
        action: "company.role_created",
        entityType: "company_role",
        entityId: role.id,
        details: { name: role.name, key: role.key },
      });
      res.status(201).json(role);
    },
  );

  router.delete("/companies/:companyId/roles/:roleId", async (req, res) => {
    const companyId = req.params.companyId as string;
    const roleId = req.params.roleId as string;
    await assertCompanyAccess(req, companyId);
    await svc.deleteRole(companyId, roleId);
    await logActivity(db, {
      companyId,
      actorType: req.actor.type,
      actorId: req.actor.id,
      action: "company.role_deleted",
      entityType: "company_role",
      entityId: roleId,
    });
    res.status(204).end();
  });

  router.post(
    "/companies/:companyId/roles/import",
    validate(companyRoleImportSchema),
    async (req, res) => {
      const companyId = req.params.companyId as string;
      await assertCompanyAccess(req, companyId);
      const result = await svc.importFromSource(companyId, req.body.sourceId, req.body.paths);
      await logActivity(db, {
        companyId,
        actorType: req.actor.type,
        actorId: req.actor.id,
        action: "company.roles_imported",
        entityType: "company",
        entityId: companyId,
        details: { count: result.imported.length, warnings: result.warnings },
      });
      res.status(201).json(result);
    },
  );

  return router;
}
```

- [ ] **Step 3: Register routes**

Find where other company routes are mounted in `paperclip/server/src/routes/index.ts`. Add:

```ts
import { roleSourceRoutes } from "./role-sources.js";
import { companyRoleRoutes } from "./company-roles.js";
```

And mount them:

```ts
router.use(roleSourceRoutes(db));
router.use(companyRoleRoutes(db));
```

- [ ] **Step 4: Commit**

```bash
git add paperclip/server/src/routes/role-sources.ts paperclip/server/src/routes/company-roles.ts paperclip/server/src/routes/index.ts
git commit -m "feat(roles): add API routes for role sources and company roles"
```

---

### Task 6: UI — API client and query keys

**Files:**
- Create: `paperclip/ui/src/api/roles.ts`
- Modify: `paperclip/ui/src/lib/queryKeys.ts`

- [ ] **Step 1: Create `api/roles.ts`**

```ts
import type {
  RoleSource,
  RoleSourceBrowseResult,
  CompanyRoleListItem,
  CompanyRoleDetail,
  CompanyRoleCreateRequest,
  CompanyRoleImportResult,
} from "@paperclipai/shared";
import { api } from "./client";

export const roleSourcesApi = {
  list: (companyId: string) =>
    api.get<RoleSource[]>(`/companies/${encodeURIComponent(companyId)}/role-sources`),

  create: (companyId: string, payload: { name: string; url: string; ref?: string | null }) =>
    api.post<RoleSource>(`/companies/${encodeURIComponent(companyId)}/role-sources`, payload),

  delete: (companyId: string, sourceId: string) =>
    api.delete<void>(`/companies/${encodeURIComponent(companyId)}/role-sources/${encodeURIComponent(sourceId)}`),

  browse: (companyId: string, sourceId: string) =>
    api.get<RoleSourceBrowseResult>(
      `/companies/${encodeURIComponent(companyId)}/role-sources/${encodeURIComponent(sourceId)}/browse`,
    ),
};

export const companyRolesApi = {
  list: (companyId: string) =>
    api.get<CompanyRoleListItem[]>(`/companies/${encodeURIComponent(companyId)}/roles`),

  detail: (companyId: string, roleId: string) =>
    api.get<CompanyRoleDetail>(
      `/companies/${encodeURIComponent(companyId)}/roles/${encodeURIComponent(roleId)}`,
    ),

  create: (companyId: string, payload: CompanyRoleCreateRequest) =>
    api.post<CompanyRoleDetail>(`/companies/${encodeURIComponent(companyId)}/roles`, payload),

  delete: (companyId: string, roleId: string) =>
    api.delete<void>(`/companies/${encodeURIComponent(companyId)}/roles/${encodeURIComponent(roleId)}`),

  importFromSource: (companyId: string, sourceId: string, paths: string[]) =>
    api.post<CompanyRoleImportResult>(
      `/companies/${encodeURIComponent(companyId)}/roles/import`,
      { sourceId, paths },
    ),
};
```

- [ ] **Step 2: Add query keys to `queryKeys.ts`**

Add to the `queryKeys` object in `paperclip/ui/src/lib/queryKeys.ts`:

```ts
roleSources: {
  list: (companyId: string) => ["role-sources", companyId] as const,
  browse: (companyId: string, sourceId: string) =>
    ["role-sources", companyId, sourceId, "browse"] as const,
},
companyRoles: {
  list: (companyId: string) => ["company-roles", companyId] as const,
  detail: (companyId: string, roleId: string) =>
    ["company-roles", companyId, roleId] as const,
},
```

- [ ] **Step 3: Commit**

```bash
git add paperclip/ui/src/api/roles.ts paperclip/ui/src/lib/queryKeys.ts
git commit -m "feat(roles): add API client and query keys"
```

---

### Task 7: UI — CompanyRoles page

**Files:**
- Create: `paperclip/ui/src/pages/CompanyRoles.tsx`
- Modify: `paperclip/ui/src/components/Sidebar.tsx` (add Roles nav item)

- [ ] **Step 1: Create `CompanyRoles.tsx`**

This is a two-panel page following the CompanySkills pattern. The page includes:
- Left panel: role list grouped by category, with source management section
- Right panel: role detail with markdown preview, edit mode for local roles, import dialog
- Import flow: select source → browse categories → pick roles → import

Create the full component file. Key sections:

```tsx
import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "@/lib/router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CompanyRoleListItem, CompanyRoleDetail, RoleSource, RoleSourceBrowseResult } from "@paperclipai/shared";
import { companyRolesApi, roleSourcesApi } from "../api/roles";
import { useCompany } from "../context/CompanyContext";
import { useBreadcrumbs } from "../context/BreadcrumbContext";
import { useToast } from "../context/ToastContext";
import { queryKeys } from "../lib/queryKeys";
import { EmptyState } from "../components/EmptyState";
import { MarkdownBody } from "../components/MarkdownBody";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Users, Plus, Trash2, Download, Folder, Search } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "../lib/utils";

export function CompanyRoles() {
  const { companyId } = useParams();
  const company = useCompany();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  useBreadcrumbs([{ label: company?.name ?? "Company" }, { label: "Roles" }]);

  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [addSourceOpen, setAddSourceOpen] = useState(false);
  const [newSourceUrl, setNewSourceUrl] = useState("");
  const [newSourceName, setNewSourceName] = useState("");
  const [newSourceRef, setNewSourceRef] = useState("main");
  const [browseData, setBrowseData] = useState<RoleSourceBrowseResult | null>(null);
  const [selectedImportPaths, setSelectedImportPaths] = useState<string[]>([]);

  const { data: roles = [] } = useQuery({
    queryKey: queryKeys.companyRoles.list(companyId),
    queryFn: () => companyRolesApi.list(companyId),
  });

  const { data: sources = [] } = useQuery({
    queryKey: queryKeys.roleSources.list(companyId),
    queryFn: () => roleSourcesApi.list(companyId),
  });

  const { data: detail } = useQuery({
    queryKey: queryKeys.companyRoles.detail(companyId, selectedRoleId ?? ""),
    queryFn: () => companyRolesApi.detail(companyId, selectedRoleId!),
    enabled: Boolean(selectedRoleId),
  });

  const filteredRoles = useMemo(() => {
    if (!search) return roles;
    const lower = search.toLowerCase();
    return roles.filter(
      (r) =>
        r.name.toLowerCase().includes(lower) ||
        r.description?.toLowerCase().includes(lower) ||
        r.category?.toLowerCase().includes(lower),
    );
  }, [roles, search]);

  const groupedRoles = useMemo(() => {
    const groups = new Map<string, CompanyRoleListItem[]>();
    for (const role of filteredRoles) {
      const cat = role.category || "Uncategorized";
      if (!groups.has(cat)) groups.set(cat, []);
      groups.get(cat)!.push(role);
    }
    return Array.from(groups.entries());
  }, [filteredRoles]);

  const browseMutation = useMutation({
    mutationFn: (sourceId: string) => roleSourcesApi.browse(companyId, sourceId),
    onSuccess: (data) => {
      setBrowseData(data);
      setSelectedImportPaths([]);
    },
  });

  const importMutation = useMutation({
    mutationFn: ({ sourceId, paths }: { sourceId: string; paths: string[] }) =>
      companyRolesApi.importFromSource(companyId, sourceId, paths),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.companyRoles.list(companyId) });
      toast.success(`Imported ${result.imported.length} roles`);
      setImportDialogOpen(false);
      setBrowseData(null);
      if (result.warnings.length > 0) {
        toast.error(result.warnings.join("; "));
      }
    },
  });

  const addSourceMutation = useMutation({
    mutationFn: () => roleSourcesApi.create(companyId, { name: newSourceName, url: newSourceUrl, ref: newSourceRef }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.roleSources.list(companyId) });
      toast.success("Source added");
      setAddSourceOpen(false);
      setNewSourceUrl("");
      setNewSourceName("");
      setNewSourceRef("main");
    },
  });

  const deleteSourceMutation = useMutation({
    mutationFn: (sourceId: string) => roleSourcesApi.delete(companyId, sourceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.roleSources.list(companyId) });
      toast.success("Source removed");
    },
  });

  const deleteRoleMutation = useMutation({
    mutationFn: (roleId: string) => companyRolesApi.delete(companyId, roleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.companyRoles.list(companyId) });
      setSelectedRoleId(null);
      toast.success("Role deleted");
    },
  });

  return (
    <div className="flex h-full">
      {/* Left panel — role list */}
      <div className="w-80 shrink-0 border-r border-border overflow-y-auto">
        <div className="p-4 border-b border-border space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">Roles</h2>
            <div className="flex gap-1">
              <Button size="sm" variant="outline" onClick={() => setImportDialogOpen(true)}>
                <Download className="h-3.5 w-3.5 mr-1" /> Import
              </Button>
            </div>
          </div>
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              placeholder="Search roles..."
              className="pl-8 h-8 text-sm"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>

        {/* Sources section */}
        {sources.length > 0 && (
          <div className="p-4 border-b border-border">
            <h3 className="text-xs font-medium text-muted-foreground mb-2">Sources</h3>
            <div className="space-y-1">
              {sources.map((s) => (
                <div key={s.id} className="flex items-center justify-between text-sm py-1">
                  <span className="truncate">{s.name}</span>
                  <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => deleteSourceMutation.mutate(s.id)}>
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              ))}
            </div>
            <Button size="sm" variant="ghost" className="mt-1 text-xs" onClick={() => setAddSourceOpen(true)}>
              <Plus className="h-3 w-3 mr-1" /> Add source
            </Button>
          </div>
        )}
        {sources.length === 0 && (
          <div className="p-4 border-b border-border">
            <Button size="sm" variant="outline" className="w-full" onClick={() => setAddSourceOpen(true)}>
              <Plus className="h-3.5 w-3.5 mr-1" /> Add git source
            </Button>
          </div>
        )}

        {/* Roles by category */}
        <div className="p-2">
          {groupedRoles.map(([category, catRoles]) => (
            <div key={category}>
              <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <Folder className="h-3 w-3" /> {category}
              </div>
              {catRoles.map((role) => (
                <button
                  key={role.id}
                  className={cn(
                    "w-full text-left px-3 py-2 rounded-md text-sm hover:bg-accent",
                    selectedRoleId === role.id && "bg-accent",
                  )}
                  onClick={() => setSelectedRoleId(role.id)}
                >
                  <div className="font-medium">{role.name}</div>
                  {role.description && (
                    <div className="text-xs text-muted-foreground truncate">{role.description}</div>
                  )}
                  {role.assignedAgentCount > 0 && (
                    <div className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
                      <Users className="h-3 w-3" /> {role.assignedAgentCount} agent{role.assignedAgentCount !== 1 ? "s" : ""}
                    </div>
                  )}
                </button>
              ))}
            </div>
          ))}
          {roles.length === 0 && (
            <EmptyState title="No roles" description="Import roles from a git repository or create one manually." />
          )}
        </div>
      </div>

      {/* Right panel — role detail */}
      <div className="flex-1 overflow-y-auto">
        {detail ? (
          <div className="p-6 max-w-3xl">
            <div className="flex items-start justify-between mb-4">
              <div>
                <h1 className="text-xl font-semibold">{detail.name}</h1>
                {detail.category && (
                  <span className="text-xs text-muted-foreground bg-accent px-2 py-0.5 rounded">{detail.category}</span>
                )}
                {detail.description && <p className="text-sm text-muted-foreground mt-1">{detail.description}</p>}
              </div>
              <Button size="sm" variant="outline" onClick={() => deleteRoleMutation.mutate(detail.id)}>
                <Trash2 className="h-3.5 w-3.5 mr-1" /> Delete
              </Button>
            </div>
            {detail.usedByAgents.length > 0 && (
              <div className="mb-4 p-3 bg-accent rounded-lg">
                <h3 className="text-xs font-medium text-muted-foreground mb-1">Used by</h3>
                <div className="flex flex-wrap gap-2">
                  {detail.usedByAgents.map((a) => (
                    <span key={a.id} className="text-sm">{a.name}</span>
                  ))}
                </div>
              </div>
            )}
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <MarkdownBody content={detail.markdown} />
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
            Select a role to view details
          </div>
        )}
      </div>

      {/* Add Source Dialog */}
      <Dialog open={addSourceOpen} onOpenChange={setAddSourceOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add role source</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input placeholder="Repository URL" value={newSourceUrl} onChange={(e) => setNewSourceUrl(e.target.value)} />
            <Input placeholder="Display name" value={newSourceName} onChange={(e) => setNewSourceName(e.target.value)} />
            <Input placeholder="Branch (default: main)" value={newSourceRef} onChange={(e) => setNewSourceRef(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddSourceOpen(false)}>Cancel</Button>
            <Button disabled={!newSourceUrl || !newSourceName} onClick={() => addSourceMutation.mutate()}>Add</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import Dialog */}
      <Dialog open={importDialogOpen} onOpenChange={setImportDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Import roles</DialogTitle>
          </DialogHeader>
          {!browseData ? (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">Select a source to browse available roles:</p>
              {sources.map((s) => (
                <Button key={s.id} variant="outline" className="w-full justify-start" onClick={() => browseMutation.mutate(s.id)}>
                  <Folder className="h-4 w-4 mr-2" /> {s.name}
                  <span className="text-xs text-muted-foreground ml-2">{s.url}</span>
                </Button>
              ))}
              {sources.length === 0 && (
                <p className="text-sm text-muted-foreground">No sources added yet. Add one first.</p>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Select roles to import</span>
                <Button size="sm" variant="ghost" onClick={() => { setBrowseData(null); setSelectedImportPaths([]); }}>Back</Button>
              </div>
              {browseData.categories.map((cat) => (
                <div key={cat.name}>
                  <h3 className="text-sm font-medium mb-2 flex items-center gap-1.5">
                    <Folder className="h-4 w-4" /> {cat.name}
                  </h3>
                  <div className="space-y-1">
                    {cat.entries.map((entry) => {
                      const checked = selectedImportPaths.includes(entry.path);
                      return (
                        <label key={entry.path} className="flex items-start gap-2 p-2 rounded hover:bg-accent cursor-pointer">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() =>
                              setSelectedImportPaths((prev) =>
                                checked ? prev.filter((p) => p !== entry.path) : [...prev, entry.path],
                              )
                            }
                            className="mt-0.5"
                          />
                          <div>
                            <div className="text-sm font-medium">{entry.name}</div>
                            {entry.description && (
                              <div className="text-xs text-muted-foreground">{entry.description}</div>
                            )}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                </div>
              ))}
              <DialogFooter>
                <Button variant="outline" onClick={() => { setImportDialogOpen(false); setBrowseData(null); }}>Cancel</Button>
                <Button
                  disabled={selectedImportPaths.length === 0}
                  onClick={() => importMutation.mutate({ sourceId: browseData.sourceId, paths: selectedImportPaths })}
                >
                  Import {selectedImportPaths.length} role{selectedImportPaths !== 1 ? "s" : ""}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
```

- [ ] **Step 2: Add Roles nav item to Sidebar**

In `paperclip/ui/src/components/Sidebar.tsx`, add after the Skills nav item (line ~114):

```tsx
<SidebarNavItem to="/roles" label="Roles" icon={Users} />
```

Ensure `Users` is imported from `lucide-react`.

- [ ] **Step 3: Add route to app router**

Find the router file (likely `paperclip/ui/src/App.tsx` or a routes file) and add:

```tsx
<Route path="/roles" element={<CompanyRoles />} />
```

Import `CompanyRoles` from `./pages/CompanyRoles`.

- [ ] **Step 4: Commit**

```bash
git add paperclip/ui/src/pages/CompanyRoles.tsx paperclip/ui/src/components/Sidebar.tsx paperclip/ui/src/App.tsx
git commit -m "feat(roles): add CompanyRoles page with import flow"
```

---

### Task 8: Agent creation integration — role picker in NewAgent

**Files:**
- Modify: `paperclip/ui/src/pages/NewAgent.tsx`
- Modify: `paperclip/server/src/routes/agents.ts`

- [ ] **Step 1: Add role picker to NewAgent.tsx**

Add state and data fetching near the existing skills code:

```tsx
const [selectedRoleKey, setSelectedRoleKey] = useState<string | null>(null);

const { data: companyRoles } = useQuery({
  queryKey: queryKeys.companyRoles.list(selectedCompanyId ?? ""),
  queryFn: () => companyRolesApi.list(selectedCompanyId!),
  enabled: Boolean(selectedCompanyId),
});
```

Add to `createAgent.mutate` payload (where `desiredSkills` is passed):

```tsx
...(selectedRoleKey ? { assignedRole: selectedRoleKey } : {}),
```

Add role picker UI section (after the skills checkboxes section):

```tsx
<div className="border-t border-border px-4 py-4">
  <div className="space-y-3">
    <div>
      <h2 className="text-sm font-medium">Role</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Assign a role template. The role description will be used as the agent's instructions.
      </p>
    </div>
    {(companyRoles ?? []).length === 0 ? (
      <p className="text-xs text-muted-foreground">
        No roles available.{" "}
        <Link to="/roles" className="underline">Manage roles</Link>
      </p>
    ) : (
      <select
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        value={selectedRoleKey ?? ""}
        onChange={(e) => setSelectedRoleKey(e.target.value || null)}
      >
        <option value="">No role</option>
        {(companyRoles ?? []).map((role) => (
          <option key={role.id} value={role.key}>
            {role.category ? `${role.category} / ` : ""}{role.name}
          </option>
        ))}
      </select>
    )}
  </div>
</div>
```

- [ ] **Step 2: Handle `assignedRole` in agent creation route**

In `paperclip/server/src/routes/agents.ts`, in the `agent-hires` POST handler (around line 1281), after extracting `desiredSkills`, also extract `assignedRole`:

```ts
const {
  desiredSkills: requestedDesiredSkills,
  assignedRole: requestedAssignedRole,
  sourceIssueId: _sourceIssueId,
  sourceIssueIds: _sourceIssueIds,
  ...hireInput
} = req.body;
```

Then after building `normalizedAdapterConfig`, resolve the role key and inject into adapterConfig:

```ts
if (typeof requestedAssignedRole === "string" && requestedAssignedRole) {
  const roleSvc = companyRoleService(db);
  const resolvedKey = await roleSvc.resolveRoleKey(companyId, requestedAssignedRole);
  if (resolvedKey) {
    normalizedAdapterConfig.assignedRole = resolvedKey;
  }
}
```

Also pass the role's markdown as `promptTemplate` if no explicit promptTemplate is set:

```ts
if (resolvedKey && !normalizedAdapterConfig.promptTemplate) {
  const roleSvc = companyRoleService(db);
  const role = await roleSvc.getByKey(companyId, resolvedKey);
  if (role) {
    normalizedAdapterConfig.promptTemplate = role.markdown;
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add paperclip/ui/src/pages/NewAgent.tsx paperclip/server/src/routes/agents.ts
git commit -m "feat(roles): add role picker to NewAgent and agent creation flow"
```

---

### Task 9: Build and deploy

- [ ] **Step 1: Build shared package**

```bash
cd paperclip && pnpm --filter @paperclipai/shared build
```

- [ ] **Step 2: Typecheck full project**

```bash
cd paperclip && pnpm -r typecheck
```

- [ ] **Step 3: Build UI in container**

```bash
docker exec -w /app/ui paperclip-server node node_modules/vite/bin/vite.js build
```

- [ ] **Step 4: Rebuild paperclip-server Docker image**

```bash
docker build -t paperclip-server:latest paperclip/
```

- [ ] **Step 5: Restart services**

```bash
docker compose up -d --force-recreate --build paperclip-server
```

- [ ] **Step 6: Verify API endpoint**

```bash
curl -s http://localhost:3100/api/companies | head -20
```

Check that `/companies/:id/role-sources` and `/companies/:id/roles` endpoints respond.
