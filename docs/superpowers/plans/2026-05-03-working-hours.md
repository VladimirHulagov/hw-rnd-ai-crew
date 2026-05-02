# Working Hours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable working hours that suppress heartbeat triggers outside the configured window, with instance-level default and per-agent override.

**Architecture:** New `workingHours` jsonb column on `instance_settings` for default. Agent-level override in `runtimeConfig.heartbeat.workingHours`. Single check in `tickTimers()` before `enqueueWakeup`. Instance timezone from `general.timezone` used for time calculation.

**Tech Stack:** Drizzle ORM, Zod, Express, React, date-fns-tz (already in project)

---

### Task 1: Shared Types and Validators

**Files:**
- Modify: `paperclip/packages/shared/src/types/instance.ts`
- Modify: `paperclip/packages/shared/src/validators/instance.ts`
- Modify: `paperclip/packages/shared/src/types/index.ts`
- Modify: `paperclip/packages/shared/src/validators/index.ts`

- [ ] **Step 1: Add types to `instance.ts`**

After the `InstanceSkillsSyncSettings` interface, add:

```typescript
export type DayOfWeek = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";

export interface WorkingHours {
  enabled: boolean;
  start: string;
  end: string;
  days: DayOfWeek[];
}

export interface InstanceSettings {
  id: string;
  general: InstanceGeneralSettings;
  experimental: InstanceExperimentalSettings;
  messaging: InstanceMessagingSettings;
  skillsSync: InstanceSkillsSyncSettings;
  workingHours: WorkingHours;
  createdAt: Date;
  updatedAt: Date;
}
```

- [ ] **Step 2: Add Zod schemas to `validators/instance.ts`**

After the `skillsSyncSettingsSchema`, add:

```typescript
export const dayOfWeekSchema = z.enum(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]);

export const workingHoursSchema = z.object({
  enabled: z.boolean().default(false),
  start: z.string().regex(/^\d{2}:\d{2}$/, "Must be HH:MM format").default("09:00"),
  end: z.string().regex(/^\d{2}:\d{2}$/, "Must be HH:MM format").default("18:00"),
  days: z.array(dayOfWeekSchema).min(0).default(["mon", "tue", "wed", "thu", "fri"]),
}).strict();

export const patchWorkingHoursSchema = workingHoursSchema.partial();
```

After the existing type exports at the bottom, add:

```typescript
export type WorkingHours = z.infer<typeof workingHoursSchema>;
export type PatchWorkingHours = z.infer<typeof patchWorkingHoursSchema>;
```

- [ ] **Step 3: Add re-exports to `types/index.ts`**

Update the export from `./instance.js` to include the new types. Find the existing line:

```typescript
export type { InstanceExperimentalSettings, InstanceGeneralSettings, InstanceSettings, InstanceMessagingSettings, InstanceMessagingTelegramSettings, InstanceSkillsSyncSettings, TimeFormat } from "./instance.js";
```

Replace with:

```typescript
export type { InstanceExperimentalSettings, InstanceGeneralSettings, InstanceSettings, InstanceMessagingSettings, InstanceMessagingTelegramSettings, InstanceSkillsSyncSettings, TimeFormat, WorkingHours, DayOfWeek } from "./instance.js";
```

- [ ] **Step 4: Add re-exports to `validators/index.ts`**

After the existing `skillsSync` exports block, add:

```typescript
export {
  dayOfWeekSchema,
  workingHoursSchema,
  patchWorkingHoursSchema,
  type WorkingHours,
  type PatchWorkingHours,
} from "./instance.js";
```

- [ ] **Step 5: Commit**

```bash
git add paperclip/packages/shared/src/types/instance.ts paperclip/packages/shared/src/validators/instance.ts paperclip/packages/shared/src/types/index.ts paperclip/packages/shared/src/validators/index.ts
git commit -m "feat: add WorkingHours types and validators in shared package"
```

---

### Task 2: DB Schema and Migration

**Files:**
- Modify: `paperclip/packages/db/src/schema/instance_settings.ts`
- Create: `paperclip/packages/db/src/migrations/0053_instance_working_hours.sql`

- [ ] **Step 1: Add `workingHours` column to Drizzle schema**

In `instance_settings.ts`, add after the `skillsSync` column (line 11):

```typescript
    workingHours: jsonb("working_hours").$type<Record<string, unknown>>().notNull().default({}),
```

- [ ] **Step 2: Create migration SQL**

Create `paperclip/packages/db/src/migrations/0053_instance_working_hours.sql`:

```sql
ALTER TABLE instance_settings ADD COLUMN IF NOT EXISTS working_hours jsonb NOT NULL DEFAULT '{}';
```

- [ ] **Step 3: Commit**

```bash
git add paperclip/packages/db/src/schema/instance_settings.ts paperclip/packages/db/src/migrations/0053_instance_working_hours.sql
git commit -m "feat: add workingHours jsonb column to instance_settings"
```

---

### Task 3: Server Service — normalizeWorkingHoursSettings + CRUD

**Files:**
- Modify: `paperclip/server/src/services/instance-settings.ts`

- [ ] **Step 1: Add import for workingHours schema**

Add `workingHoursSchema` and `type WorkingHours` to the imports from `@paperclipai/shared` (line 3-16 area). Also add `type PatchWorkingHours`.

- [ ] **Step 2: Add normalizeWorkingHoursSettings function**

After `normalizeSkillsSyncSettings` (line 76), add:

```typescript
function normalizeWorkingHoursSettings(raw: unknown): WorkingHours {
  const parsed = workingHoursSchema.safeParse(raw ?? {});
  if (parsed.success) {
    return parsed.data;
  }
  return {
    enabled: false,
    start: "09:00",
    end: "18:00",
    days: ["mon", "tue", "wed", "thu", "fri"],
  };
}
```

- [ ] **Step 3: Update `toInstanceSettings` to include workingHours**

In `toInstanceSettings` (line 78), add `workingHours` field:

```typescript
    workingHours: normalizeWorkingHoursSettings(row.workingHours),
```

- [ ] **Step 4: Add getWorkingHours and updateWorkingHours methods**

After the `updateSkillsSync` method (line 209), add:

```typescript
    getWorkingHours: async (): Promise<WorkingHours> => {
      const row = await getOrCreateRow();
      return normalizeWorkingHoursSettings(row.workingHours);
    },

    updateWorkingHours: async (patch: PatchWorkingHours): Promise<InstanceSettings> => {
      const current = await getOrCreateRow();
      const next = normalizeWorkingHoursSettings({
        ...normalizeWorkingHoursSettings(current.workingHours),
        ...patch,
      });
      const now = new Date();
      const [updated] = await db
        .update(instanceSettings)
        .set({
          workingHours: { ...next },
          updatedAt: now,
        })
        .where(eq(instanceSettings.id, current.id))
        .returning();
      return toInstanceSettings(updated ?? current);
    },
```

- [ ] **Step 5: Commit**

```bash
git add paperclip/server/src/services/instance-settings.ts
git commit -m "feat: add workingHours normalize/get/update to instance-settings service"
```

---

### Task 4: Server Routes — GET/PATCH working-hours

**Files:**
- Modify: `paperclip/server/src/routes/instance-settings.ts`

- [ ] **Step 1: Add import for patchWorkingHoursSchema**

Add `patchWorkingHoursSchema` to the imports from `@paperclipai/shared` (line 3).

- [ ] **Step 2: Add GET route**

Before the `skills-sync/trigger` POST route (line 185), add:

```typescript
  router.get("/instance/settings/working-hours", async (req, res) => {
    if (req.actor.type !== "board") {
      throw forbidden("Board access required");
    }
    res.json(await svc.getWorkingHours());
  });
```

- [ ] **Step 3: Add PATCH route**

After the GET route, add:

```typescript
  router.patch(
    "/instance/settings/working-hours",
    validate(patchWorkingHoursSchema),
    async (req, res) => {
      assertCanManageInstanceSettings(req);
      const updated = await svc.updateWorkingHours(req.body);
      const actor = getActorInfo(req);
      const companyIds = await svc.listCompanyIds();
      await Promise.all(
        companyIds.map((companyId) =>
          logActivity(db, {
            companyId,
            actorType: actor.actorType,
            actorId: actor.actorId,
            agentId: actor.agentId,
            runId: actor.runId,
            action: "instance.settings.working_hours_updated",
            entityType: "instance_settings",
            entityId: updated.id,
            details: {
              workingHours: updated.workingHours,
              changedKeys: Object.keys(req.body).sort(),
            },
          }),
        ),
      );
      res.json(updated.workingHours);
    },
  );
```

- [ ] **Step 4: Commit**

```bash
git add paperclip/server/src/routes/instance-settings.ts
git commit -m "feat: add GET/PATCH /instance/settings/working-hours routes"
```

---

### Task 5: Heartbeat — working hours check in tickTimers

**Files:**
- Modify: `paperclip/server/src/services/heartbeat.ts`

- [ ] **Step 1: Add imports**

At the top of the file, add to the imports from `@paperclipai/shared`:

```typescript
  workingHoursSchema,
  type WorkingHours,
```

Also add import for the instance settings service:

```typescript
import { instanceSettingsService } from "./instance-settings.js";
```

Also add `toZonedTime` import — check if `date-fns-tz` is available in the project. If not, use `Intl.DateTimeFormat`:

```typescript
function getTimeInTimezone(date: Date, timezone: string): { day: number; hours: number; minutes: number } {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    weekday: "short",
    hour: "numeric",
    minute: "numeric",
    hour12: false,
  });
  const parts = fmt.formatToParts(date);
  const weekday = parts.find((p) => p.type === "weekday")?.value?.toLowerCase() ?? "";
  const hours = parseInt(parts.find((p) => p.type === "hour")?.value ?? "0", 10);
  const minutes = parseInt(parts.find((p) => p.type === "minute")?.value ?? "0", 10);
  const dayMap: Record<string, string> = { mon: "mon", tue: "tue", wed: "wed", thu: "thu", fri: "fri", sat: "sat", sun: "sun" };
  return { day: dayMap[weekday] ?? "", hours, minutes };
}
```

- [ ] **Step 2: Add `isWithinWorkingHours` helper**

After the `normalizeMaxHeartbeatRuns` function (line ~285), add:

```typescript
function normalizeWorkingHours(raw: unknown): WorkingHours {
  const parsed = workingHoursSchema.safeParse(raw ?? {});
  return parsed.success ? parsed.data : { enabled: false, start: "09:00", end: "18:00", days: ["mon", "tue", "wed", "thu", "fri"] };
}

function isWithinWorkingHours(now: Date, wh: WorkingHours, timezone: string): boolean {
  if (!wh.enabled) return true;
  const { day, hours, minutes } = getTimeInTimezone(now, timezone);
  if (!wh.days.includes(day as WorkingHours["days"][number])) return false;
  const currentMinutes = hours * 60 + minutes;
  const [startH, startM] = wh.start.split(":").map(Number);
  const [endH, endM] = wh.end.split(":").map(Number);
  return currentMinutes >= startH * 60 + startM && currentMinutes < endH * 60 + endM;
}
```

- [ ] **Step 3: Extend `parseHeartbeatPolicy` to include workingHours**

Add `workingHours` to the return value of `parseHeartbeatPolicy` (line ~1919):

```typescript
function parseHeartbeatPolicy(agent: typeof agents.$inferSelect) {
  const runtimeConfig = parseObject(agent.runtimeConfig);
  const heartbeat = parseObject(runtimeConfig.heartbeat);

  return {
    enabled: asBoolean(heartbeat.enabled, true),
    intervalSec: Math.max(0, asNumber(heartbeat.intervalSec, 0)),
    wakeOnDemand: asBoolean(heartbeat.wakeOnDemand ?? heartbeat.wakeOnAssignment ?? heartbeat.wakeOnOnDemand ?? heartbeat.wakeOnAutomation, true),
    maxConcurrentRuns: normalizeMaxConcurrentRuns(heartbeat.maxConcurrentRuns),
    maxHeartbeatRuns: normalizeMaxHeartbeatRuns(heartbeat.maxHeartbeatRuns),
    workingHours: normalizeWorkingHours(heartbeat.workingHours),
  };
}
```

- [ ] **Step 4: Add working hours check in `tickTimers`**

At the start of `tickTimers` (line ~4187), before the agent loop, load instance settings:

```typescript
tickTimers: async (now = new Date()) => {
  const settingsSvc = instanceSettingsService(db);
  const [generalSettings, instanceWorkingHours] = await Promise.all([
    settingsSvc.getGeneral(),
    settingsSvc.getWorkingHours(),
  ]);
  const timezone = generalSettings.timezone || "UTC";

  const allAgents = await db.select().from(agents);
  let checked = 0;
  let enqueued = 0;
  let skipped = 0;
```

Then inside the agent loop, after `const policy = parseHeartbeatPolicy(agent);` and after `if (!policy.enabled || policy.intervalSec <= 0) continue;`, add the working hours check:

```typescript
    const effectiveWorkingHours = policy.workingHours.enabled
      ? policy.workingHours
      : instanceWorkingHours;
    if (!isWithinWorkingHours(now, effectiveWorkingHours, timezone)) {
      skipped += 1;
      continue;
    }
```

This must be placed BEFORE the `checked += 1;` line so that out-of-hours agents are not counted as "checked".

- [ ] **Step 5: Commit**

```bash
git add paperclip/server/src/services/heartbeat.ts
git commit -m "feat: add working hours check to heartbeat tickTimers"
```

---

### Task 6: UI API Client

**Files:**
- Modify: `paperclip/ui/src/api/instanceSettings.ts`

- [ ] **Step 1: Add working hours API functions**

Read the existing file first to see the pattern. Add after the existing skills-sync functions:

```typescript
export async function getWorkingHours() {
  const res = await fetch("/api/instance/settings/working-hours", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch working hours");
  return res.json();
}

export async function updateWorkingHours(patch: Record<string, unknown>) {
  const res = await fetch("/api/instance/settings/working-hours", {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error("Failed to update working hours");
  return res.json();
}
```

- [ ] **Step 2: Commit**

```bash
git add paperclip/ui/src/api/instanceSettings.ts
git commit -m "feat: add working hours API client functions"
```

---

### Task 7: UI — Instance Settings Working Hours Section

**Files:**
- Modify: `paperclip/ui/src/pages/InstanceGeneralSettings.tsx`

- [ ] **Step 1: Add import for Clock icon**

Add `Clock` to the lucide-react imports.

- [ ] **Step 2: Add working hours query and mutation**

Inside the component, after the existing `useQuery` for general settings, add:

```typescript
  const { data: workingHoursData } = useQuery({
    queryKey: ["instance", "workingHours"],
    queryFn: getWorkingHours,
  });

  const updateWorkingHoursMutation = useMutation({
    mutationFn: (patch: Record<string, unknown>) => updateWorkingHours(patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["instance", "workingHours"] }),
  });

  const workingHours = workingHoursData ?? { enabled: false, start: "09:00", end: "18:00", days: ["mon", "tue", "wed", "thu", "fri"] };
```

- [ ] **Step 3: Add Working Hours section after Regional**

After the Regional section closing `</section>`, add:

```tsx
<section className="rounded-xl border border-border bg-card p-5">
  <div className="space-y-4">
    <div className="flex items-center gap-2">
      <Clock className="h-4 w-4 text-muted-foreground" />
      <h2 className="text-sm font-semibold">Working Hours</h2>
    </div>
    <p className="text-sm text-muted-foreground">
      Suppress heartbeat triggers outside working hours. Uses timezone from Regional settings.
    </p>
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Enable working hours</span>
        <button
          type="button"
          role="switch"
          aria-checked={workingHours.enabled}
          disabled={updateWorkingHoursMutation.isPending}
          onClick={() => updateWorkingHoursMutation.mutate({ enabled: !workingHours.enabled })}
          className={cn(
            "relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors disabled:cursor-not-allowed disabled:opacity-60",
            workingHours.enabled ? "bg-primary" : "bg-input",
          )}
        >
          <span className={cn(
            "pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform",
            workingHours.enabled ? "translate-x-4" : "translate-x-0",
          )} />
        </button>
      </div>
      {workingHours.enabled && (
        <>
          <div className="flex items-center gap-3">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Start</label>
              <input
                type="time"
                value={workingHours.start}
                disabled={updateWorkingHoursMutation.isPending}
                onChange={(e) => updateWorkingHoursMutation.mutate({ start: e.target.value })}
                className="rounded-md border border-border bg-background px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
              />
            </div>
            <span className="text-sm text-muted-foreground pt-5">to</span>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">End</label>
              <input
                type="time"
                value={workingHours.end}
                disabled={updateWorkingHoursMutation.isPending}
                onChange={(e) => updateWorkingHoursMutation.mutate({ end: e.target.value })}
                className="rounded-md border border-border bg-background px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <span className="text-sm font-medium">Working days</span>
            <div className="flex gap-1.5">
              {(["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const).map((day) => {
                const active = workingHours.days?.includes(day);
                return (
                  <button
                    key={day}
                    type="button"
                    disabled={updateWorkingHoursMutation.isPending}
                    className={cn(
                      "rounded-md border px-2.5 py-1.5 text-xs font-medium capitalize transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                      active
                        ? "border-foreground bg-accent text-foreground"
                        : "border-border bg-background hover:bg-accent/50",
                    )}
                    onClick={() => {
                      const current = workingHours.days ?? ["mon", "tue", "wed", "thu", "fri"];
                      const next = active
                        ? current.filter((d: string) => d !== day)
                        : [...current, day];
                      updateWorkingHoursMutation.mutate({ days: next });
                    }}
                  >
                    {day}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => updateWorkingHoursMutation.mutate({ start: "09:00", end: "18:00", days: ["mon", "tue", "wed", "thu", "fri"] })}
            >
              Mon-Fri 9:00-18:00
            </button>
            <span className="text-xs text-muted-foreground">|</span>
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => updateWorkingHoursMutation.mutate({ start: "10:00", end: "19:00", days: ["mon", "tue", "wed", "thu", "fri"] })}
            >
              Mon-Fri 10:00-19:00
            </button>
          </div>
        </>
      )}
    </div>
  </div>
</section>
```

- [ ] **Step 4: Commit**

```bash
git add paperclip/ui/src/pages/InstanceGeneralSettings.tsx
git commit -m "feat: add Working Hours section to Instance Settings UI"
```

---

### Task 8: UI — Agent Config Working Hours Override

**Files:**
- Modify: `paperclip/ui/src/components/AgentConfigForm.tsx`

- [ ] **Step 1: Add working hours fields to the heartbeat overlay section**

In the EDIT mode Run Policy section, after the Advanced Run Policy `CollapsibleSection` closing tag (around line 997, before the closing `</div>` of the Run Policy section), add a new section inside the advanced area:

After the `maxHeartbeatRuns` Field (around line 981), add:

```tsx
        <div className="border-t border-border pt-3 mt-3">
          <ToggleField
            label="Custom working hours"
            hint="Override instance working hours for this agent"
            checked={eff(
              "heartbeat",
              "workingHoursEnabled",
              (heartbeat.workingHours as Record<string, unknown> | undefined)?.enabled === true,
            )}
            onChange={(v) => {
              const current = (heartbeat.workingHours as Record<string, unknown>) ?? {};
              mark("heartbeat", "workingHours", { ...current, enabled: v });
            }}
          />
          {eff(
            "heartbeat",
            "workingHoursEnabled",
            (heartbeat.workingHours as Record<string, unknown> | undefined)?.enabled === true,
          ) && (
            <div className="mt-3 space-y-3 pl-0">
              <div className="flex items-center gap-3">
                <div className="space-y-1.5">
                  <label className="text-sm font-medium">Start</label>
                  <input
                    type="time"
                    value={String(
                      eff("heartbeat", "workingHours", heartbeat.workingHours ?? {}).start ?? "09:00"
                    )}
                    onChange={(e) => {
                      const current = (eff("heartbeat", "workingHours", heartbeat.workingHours ?? {}) as Record<string, unknown>) ?? {};
                      mark("heartbeat", "workingHours", { ...current, start: e.target.value });
                    }}
                    className="rounded-md border border-border bg-background px-3 py-2 text-sm"
                  />
                </div>
                <span className="text-sm text-muted-foreground pt-5">to</span>
                <div className="space-y-1.5">
                  <label className="text-sm font-medium">End</label>
                  <input
                    type="time"
                    value={String(
                      eff("heartbeat", "workingHours", heartbeat.workingHours ?? {}).end ?? "18:00"
                    )}
                    onChange={(e) => {
                      const current = (eff("heartbeat", "workingHours", heartbeat.workingHours ?? {}) as Record<string, unknown>) ?? {};
                      mark("heartbeat", "workingHours", { ...current, end: e.target.value });
                    }}
                    className="rounded-md border border-border bg-background px-3 py-2 text-sm"
                  />
                </div>
              </div>
              <div className="space-y-1.5">
                <span className="text-sm font-medium">Working days</span>
                <div className="flex gap-1.5">
                  {(["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const).map((day) => {
                    const wh = eff("heartbeat", "workingHours", heartbeat.workingHours ?? {}) as Record<string, unknown>;
                    const days = (wh.days as string[]) ?? ["mon", "tue", "wed", "thu", "fri"];
                    const active = days.includes(day);
                    return (
                      <button
                        key={day}
                        type="button"
                        className={cn(
                          "rounded-md border px-2.5 py-1.5 text-xs font-medium capitalize transition-colors",
                          active
                            ? "border-foreground bg-accent text-foreground"
                            : "border-border bg-background hover:bg-accent/50",
                        )}
                        onClick={() => {
                          const next = active ? days.filter((d) => d !== day) : [...days, day];
                          mark("heartbeat", "workingHours", { ...wh, days: next });
                        }}
                      >
                        {day}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
```

- [ ] **Step 2: Verify handleSave handles workingHours in overlay**

The existing `handleSave` logic (line ~283) already handles this:

```typescript
if (Object.keys(overlay.heartbeat).length > 0) {
  const existingRc = (agent.runtimeConfig ?? {}) as Record<string, unknown>;
  const existingHb = (existingRc.heartbeat ?? {}) as Record<string, unknown>;
  patch.runtimeConfig = { ...existingRc, heartbeat: { ...existingHb, ...overlay.heartbeat } };
}
```

Since `overlay.heartbeat.workingHours` is an object, it will be spread into the heartbeat config. However, we need to ensure the `workingHoursEnabled` toggle helper key does NOT leak into runtimeConfig. Update the save logic to clean it:

```typescript
if (Object.keys(overlay.heartbeat).length > 0) {
  const existingRc = (agent.runtimeConfig ?? {}) as Record<string, unknown>;
  const existingHb = (existingRc.heartbeat ?? {}) as Record<string, unknown>;
  const { workingHoursEnabled, ...restHeartbeat } = overlay.heartbeat as Record<string, unknown> & { workingHoursEnabled?: boolean };
  patch.runtimeConfig = { ...existingRc, heartbeat: { ...existingHb, ...restHeartbeat } };
}
```

- [ ] **Step 3: Commit**

```bash
git add paperclip/ui/src/components/AgentConfigForm.tsx
git commit -m "feat: add working hours override in agent config Run Policy UI"
```

---

### Task 9: Deploy to Container

**Files:** None (container patches)

- [ ] **Step 1: Apply DB migration**

```bash
docker exec hw-rnd-ai-crew-paperclip-db-1 psql -U paperclip -d paperclip -c "ALTER TABLE instance_settings ADD COLUMN IF NOT EXISTS working_hours jsonb NOT NULL DEFAULT '{}';"
```

- [ ] **Step 2: Rebuild shared package in container**

```bash
docker exec -w /app paperclip-server npx tsc -p packages/shared/tsconfig.json
```

- [ ] **Step 3: Rebuild server files with esbuild**

For each modified server file, docker cp then restart:

```bash
docker cp paperclip/server/src/services/instance-settings.ts paperclip-server:/app/server/src/services/instance-settings.ts
docker cp paperclip/server/src/routes/instance-settings.ts paperclip-server:/app/server/src/routes/instance-settings.ts
docker cp paperclip/server/src/services/heartbeat.ts paperclip-server:/app/server/src/services/heartbeat.ts
```

Then rebuild server dist:

```bash
docker exec -w /app paperclip-server node -e "
const esbuild = require('esbuild');
esbuild.build({
  entryPoints: ['server/src/index.ts'],
  outdir: 'server/dist',
  bundle: false,
  format: 'esm',
  platform: 'node',
  target: 'node20',
}).then(() => console.log('done'));
"
```

- [ ] **Step 4: Rebuild UI**

```bash
docker cp paperclip/ui/src/api/instanceSettings.ts paperclip-server:/app/ui/src/api/instanceSettings.ts
docker cp paperclip/ui/src/pages/InstanceGeneralSettings.tsx paperclip-server:/app/ui/src/pages/InstanceGeneralSettings.tsx
docker cp paperclip/ui/src/components/AgentConfigForm.tsx paperclip-server:/app/ui/src/components/AgentConfigForm.tsx
docker exec -w /app/ui paperclip-server node node_modules/vite/bin/vite.js build
```

- [ ] **Step 5: Restart paperclip-server**

```bash
docker compose restart paperclip-server
```
