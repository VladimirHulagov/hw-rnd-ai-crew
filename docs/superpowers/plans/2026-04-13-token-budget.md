# Token-based Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add token-based budget tracking as an alternative to dollar-based budgets, controlled by a company-level setting.

**Architecture:** Add `budget_metric` column to `companies` table (selecting between `"billed_cents"` and `"total_tokens"`). Extend `budget_policies` with `anchor_ts` for anchor-week windows. The budget engine (`budgets.ts`) switches its query logic based on the policy's metric. All budget UI adapts to show either `$` or token amounts.

**Tech Stack:** Drizzle ORM (schema+migrations), Zod (validators), React (UI), Express (routes).

---

### Task 1: Schema — Add `budget_metric` to companies and `anchor_ts` to budget_policies

**Files:**
- Modify: `paperclip/packages/db/src/schema/companies.ts`
- Modify: `paperclip/packages/db/src/schema/budget_policies.ts`
- Create: `paperclip/packages/db/src/migrations/0049_token_budget.sql`

- [ ] **Step 1: Add `budgetMetric` to companies schema**

In `paperclip/packages/db/src/schema/companies.ts`, add after `brandColor` (line 25):

```ts
budgetMetric: text("budget_metric").notNull().default("billed_cents"),
```

- [ ] **Step 2: Add `anchorTs` to budget_policies schema**

In `paperclip/packages/db/src/schema/budget_policies.ts`, add after `isActive` (line 18):

```ts
anchorTs: timestamp("anchor_ts", { withTimezone: true }),
```

- [ ] **Step 3: Write migration SQL**

Create `paperclip/packages/db/src/migrations/0049_token_budget.sql`:

```sql
ALTER TABLE "companies" ADD COLUMN "budget_metric" text DEFAULT 'billed_cents' NOT NULL;
ALTER TABLE "budget_policies" ADD COLUMN "anchor_ts" timestamp with time zone;
```

- [ ] **Step 4: Commit**

```bash
git add packages/db/src/schema/companies.ts packages/db/src/schema/budget_policies.ts packages/db/src/migrations/0049_token_budget.sql
git commit -m "feat(db): add budget_metric to companies and anchor_ts to budget_policies"
```

---

### Task 2: Shared Constants and Validators

**Files:**
- Modify: `paperclip/packages/shared/src/constants.ts` (lines 272-276)
- Modify: `paperclip/packages/shared/src/validators/budget.ts`

- [ ] **Step 1: Extend BUDGET_METRICS and BUDGET_WINDOW_KINDS**

In `paperclip/packages/shared/src/constants.ts`, replace lines 272-276:

```ts
export const BUDGET_METRICS = ["billed_cents", "total_tokens"] as const;
export type BudgetMetric = (typeof BUDGET_METRICS)[number];

export const BUDGET_WINDOW_KINDS = ["calendar_month_utc", "lifetime", "anchor_week"] as const;
export type BudgetWindowKind = (typeof BUDGET_WINDOW_KINDS)[number];

export const BUDGET_METRIC_LABELS: Record<BudgetMetric, string> = {
  billed_cents: "Dollars",
  total_tokens: "Tokens",
};
```

- [ ] **Step 2: Extend upsertBudgetPolicySchema**

In `paperclip/packages/shared/src/validators/budget.ts`, add `anchorTs` field:

```ts
export const upsertBudgetPolicySchema = z.object({
  scopeType: z.enum(BUDGET_SCOPE_TYPES),
  scopeId: z.string().uuid(),
  metric: z.enum(BUDGET_METRICS).optional().default("billed_cents"),
  windowKind: z.enum(BUDGET_WINDOW_KINDS).optional().default("calendar_month_utc"),
  anchorTs: z.string().datetime().optional(),
  amount: z.number().int().nonnegative(),
  warnPercent: z.number().int().min(1).max(99).optional().default(80),
  hardStopEnabled: z.boolean().optional().default(true),
  notifyEnabled: z.boolean().optional().default(true),
  isActive: z.boolean().optional().default(true),
});
```

- [ ] **Step 3: Commit**

```bash
git add packages/shared/src/constants.ts packages/shared/src/validators/budget.ts
git commit -m "feat(shared): add total_tokens metric and anchor_week window kind"
```

---

### Task 3: Budget Engine — Token Queries and Anchor Week Window

**Files:**
- Modify: `paperclip/server/src/services/budgets.ts`

This is the core change. Three functions need modification:

- [ ] **Step 1: Extend `resolveWindow()` to handle `anchor_week`**

In `budgets.ts`, replace `resolveWindow()` (lines 55-63). The function needs access to `anchorTs`:

```ts
function resolveWindow(windowKind: BudgetWindowKind, anchorTs?: Date | null, now = new Date()) {
  if (windowKind === "lifetime") {
    return {
      start: new Date(Date.UTC(1970, 0, 1, 0, 0, 0, 0)),
      end: new Date(Date.UTC(9999, 0, 1, 0, 0, 0, 0)),
    };
  }
  if (windowKind === "anchor_week" && anchorTs) {
    const weekMs = 7 * 86400_000;
    const elapsed = now.getTime() - anchorTs.getTime();
    const weeksElapsed = Math.max(0, Math.floor(elapsed / weekMs));
    const start = new Date(anchorTs.getTime() + weeksElapsed * weekMs);
    return { start, end: new Date(start.getTime() + weekMs) };
  }
  return currentUtcMonthWindow(now);
}
```

- [ ] **Step 2: Extend `computeObservedAmount()` to support `total_tokens`**

Replace the function body (lines 142-165):

```ts
async function computeObservedAmount(
  db: Db,
  policy: Pick<PolicyRow, "companyId" | "scopeType" | "scopeId" | "windowKind" | "metric" | "anchorTs">,
) {
  const conditions = [eq(costEvents.companyId, policy.companyId)];
  if (policy.scopeType === "agent") conditions.push(eq(costEvents.agentId, policy.scopeId));
  if (policy.scopeType === "project") conditions.push(eq(costEvents.projectId, policy.scopeId));
  const { start, end } = resolveWindow(policy.windowKind as BudgetWindowKind, policy.anchorTs);
  conditions.push(gte(costEvents.occurredAt, start));
  conditions.push(lt(costEvents.occurredAt, end));

  let sumExpr;
  if (policy.metric === "total_tokens") {
    sumExpr = sql<number>`coalesce(sum(${costEvents.inputTokens} + ${costEvents.outputTokens}), 0)::bigint`;
  } else {
    sumExpr = sql<number>`coalesce(sum(${costEvents.costCents}), 0)::int`;
  }

  const [row] = await db
    .select({ total: sumExpr })
    .from(costEvents)
    .where(and(...conditions));

  return Number(row?.total ?? 0);
}
```

Remove the old early return `if (policy.metric !== "billed_cents") return 0;`.

- [ ] **Step 3: Extend `PolicyRow` type to include `anchorTs`**

Find the `PolicyRow` type (or equivalent) and add:

```ts
anchorTs: Date | null;
```

In practice this comes from the DB schema, so the `budgetPolicies` table already has it after Task 1. Just make sure all `select()` queries that read policies include `anchorTs: budgetPolicies.anchorTs`.

- [ ] **Step 4: Fix `evaluateCostEvent()` metric check**

At line 667, replace:

```ts
if (policy.metric !== "billed_cents" || policy.amount <= 0) continue;
```

with:

```ts
if (policy.amount <= 0) continue;
```

The metric-specific logic is now in `computeObservedAmount()`.

- [ ] **Step 5: Fix `getInvocationBlock()` — remove hardcoded `billed_cents` filter**

At lines 764, 798, 839, replace:

```ts
eq(budgetPolicies.metric, "billed_cents"),
```

with:

```ts
inArray(budgetPolicies.metric, ["billed_cents", "total_tokens"]),
```

This makes invocation blocking work for both metric types.

- [ ] **Step 6: Update `upsertPolicy()` to pass `anchorTs`**

In the `upsertPolicy()` method (around line 550-566), add `anchorTs` to the insert values:

```ts
anchorTs: input.anchorTs ? new Date(input.anchorTs) : null,
```

And to the update set (only if provided):

```ts
...(input.anchorTs ? { anchorTs: new Date(input.anchorTs) } : {}),
```

- [ ] **Step 7: Commit**

```bash
git add server/src/services/budgets.ts
git commit -m "feat(server): support total_tokens metric and anchor_week window in budget engine"
```

---

### Task 4: Company Settings — Budget Metric Toggle

**Files:**
- Modify: `paperclip/server/src/routes/companies.ts` (add PATCH handler for budget_metric)
- Modify: `paperclip/ui/src/pages/CompanySettings.tsx` (add toggle)
- Modify: `paperclip/ui/src/api/companies.ts` (if needed — check existing company update API)

- [ ] **Step 1: Add API endpoint to update `budgetMetric`**

In `paperclip/server/src/routes/companies.ts`, find the existing company settings PATCH route and add `budgetMetric` to the allowed update fields. Look for where `requireBoardApprovalForNewAgents` is updated — add `budgetMetric` in the same pattern:

```ts
budgetMetric: z.enum(["billed_cents", "total_tokens"]).optional(),
```

In the handler, include `budgetMetric` in the `.set()` call alongside other fields.

- [ ] **Step 2: Add UI toggle in CompanySettings.tsx**

In `paperclip/ui/src/pages/CompanySettings.tsx`, add a new section after "Hiring" (around line 417):

```tsx
<div className="space-y-4">
  <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
    Budget Tracking
  </div>
  <div className="rounded-md border border-border px-4 py-3">
    <div className="text-sm font-medium">Track budget by</div>
    <div className="mt-2 flex gap-2">
      <Button
        size="sm"
        variant={selectedCompany.budgetMetric === "billed_cents" ? "default" : "outline"}
        onClick={() => budgetMetricMutation.mutate("billed_cents")}
        disabled={budgetMetricMutation.isPending}
      >
        Dollars ($)
      </Button>
      <Button
        size="sm"
        variant={selectedCompany.budgetMetric === "total_tokens" ? "default" : "outline"}
        onClick={() => budgetMetricMutation.mutate("total_tokens")}
        disabled={budgetMetricMutation.isPending}
      >
        Tokens
      </Button>
    </div>
    <p className="mt-2 text-xs text-muted-foreground">
      {selectedCompany.budgetMetric === "total_tokens"
        ? "Budgets are tracked in total tokens (input + output). Useful for subscription plans."
        : "Budgets are tracked in US dollars based on provider billing."}
    </p>
  </div>
</div>
```

Add the mutation (following existing patterns like `settingsMutation`):

```ts
const budgetMetricMutation = useMutation({
  mutationFn: (metric: string) => companiesApi.update(selectedCompany.id, { budgetMetric: metric }),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.companies.detail(selectedCompany.id) }),
});
```

- [ ] **Step 3: Ensure `budgetMetric` is included in company API response and shared types**

Check that the shared `Company` type exports `budgetMetric` field. If the DB schema has it and the query selects it, it should flow automatically with Drizzle.

- [ ] **Step 4: Commit**

```bash
git add server/src/routes/companies.ts ui/src/pages/CompanySettings.tsx
git commit -m "feat: add budget metric toggle in company settings"
```

---

### Task 5: UI — BudgetPolicyCard Adapts to Token Metric

**Files:**
- Modify: `paperclip/ui/src/components/BudgetPolicyCard.tsx`

- [ ] **Step 1: Add token formatting helper**

In `BudgetPolicyCard.tsx`, add after line 11:

```ts
function formatTokens(count: number): string {
  if (count >= 1_000_000_000) return `${(count / 1_000_000_000).toFixed(1)}B`;
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
  return count.toString();
}
```

- [ ] **Step 2: Add `metric` prop and conditional formatting**

Add `metric` to the component props:

```ts
metric?: "billed_cents" | "total_tokens";
```

Default to `"billed_cents"`:

```ts
const m = metric ?? "billed_cents";
const formatAmount = m === "total_tokens"
  ? (v: number) => formatTokens(v)
  : (v: number) => formatCents(v);
const inputLabel = m === "total_tokens" ? "Budget (tokens)" : "Budget (USD)";
const inputPlaceholder = m === "total_tokens" ? "50000000" : "0.00";
```

Replace all `formatCents(...)` calls with `formatAmount(...)`.

Replace the `centsInputValue`/`parseDollarInput` with metric-aware versions for the input field. For tokens, use raw integer input; for cents, keep existing dollar parsing.

- [ ] **Step 3: Update window label for anchor_week**

Update `windowLabel()`:

```ts
function windowLabel(windowKind: string, metric?: string) {
  if (windowKind === "anchor_week") return "Weekly budget (anchor)";
  if (windowKind === "lifetime") return "Lifetime budget";
  return metric === "total_tokens" ? "Monthly UTC budget (tokens)" : "Monthly UTC budget";
}
```

- [ ] **Step 4: Pass `metric` from all call sites**

In `Costs.tsx`, `AgentDetail.tsx`, and `ProjectDetail.tsx` — pass `metric={summary.metric}` to `<BudgetPolicyCard>`.

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/BudgetPolicyCard.tsx ui/src/pages/Costs.tsx ui/src/pages/AgentDetail.tsx ui/src/pages/ProjectDetail.tsx
git commit -m "feat(ui): adapt BudgetPolicyCard for token-based budgets"
```

---

### Task 6: Server Routes — Pass Metric Through Budget API

**Files:**
- Modify: `paperclip/server/src/routes/costs.ts` (lines 250-330 — budget endpoints)
- Modify: `paperclip/server/src/routes/agents.ts` (line 1517 — agent budget upsert)
- Modify: `paperclip/server/src/routes/companies.ts` (line 283 — company budget upsert)

- [ ] **Step 1: Read company's budgetMetric when creating budget policies**

In each route that creates/upserts a budget policy (companies budget PATCH, agents budget PATCH, costs policy POST), read the company's `budgetMetric` and use it as the default metric:

```ts
const company = await db.select({ budgetMetric: companies.budgetMetric }).from(companies).where(eq(companies.id, companyId)).then(r => r[0]);
const metric = input.metric ?? company?.budgetMetric ?? "billed_cents";
```

- [ ] **Step 2: Include `anchorTs` in policy queries and responses**

Make sure all budget policy SELECT queries include `anchorTs: budgetPolicies.anchorTs` and it flows through to the `BudgetPolicySummary` type.

- [ ] **Step 3: Commit**

```bash
git add server/src/routes/costs.ts server/src/routes/agents.ts server/src/routes/companies.ts
git commit -m "feat(server): route company budget_metric to budget policy creation"
```

---

### Task 7: Shared Type Updates

**Files:**
- Modify: `paperclip/packages/shared/src/types/index.ts` or wherever `BudgetPolicySummary` is defined

- [ ] **Step 1: Add `metric` and `anchorTs` to BudgetPolicySummary**

Find the `BudgetPolicySummary` type and add:

```ts
metric: BudgetMetric;
anchorTs: string | null;
```

Also ensure `Company` type includes `budgetMetric`.

- [ ] **Step 2: Export new constants**

Ensure `BUDGET_METRIC_LABELS` is exported from `packages/shared/src/index.ts`.

- [ ] **Step 3: Commit**

```bash
git add packages/shared/src/
git commit -m "feat(shared): add metric and anchorTs to budget policy summary type"
```

---

### Task 8: Rebuild, Test, Deploy

**Files:** None (build/test steps)

- [ ] **Step 1: Typecheck**

```bash
cd paperclip && pnpm -r typecheck
```

- [ ] **Step 2: Build**

```bash
cd paperclip && pnpm build
```

- [ ] **Step 3: Rebuild Docker image**

```bash
docker build -t paperclip-server:latest paperclip/
```

- [ ] **Step 4: Restart server**

```bash
docker compose up -d --force-recreate paperclip-server
```

- [ ] **Step 5: Apply migration**

The migration runs automatically if Paperclip is configured to run migrations on startup. If not, apply manually:

```bash
docker compose exec paperclip-db psql -U paperclip -d paperclip -c "
ALTER TABLE companies ADD COLUMN IF NOT EXISTS budget_metric text DEFAULT 'billed_cents' NOT NULL;
ALTER TABLE budget_policies ADD COLUMN IF NOT EXISTS anchor_ts timestamp with time zone;
"
```

- [ ] **Step 6: Verify in UI**

1. Go to Company Settings → confirm "Budget Tracking" section with Dollars/Tokens toggle
2. Switch to Tokens → verify Costs page updates
3. Create a project budget policy with `total_tokens` metric and `anchor_week` window
4. Verify BudgetPolicyCard shows token amounts

- [ ] **Step 7: Commit and push**

```bash
git add -A && git commit -m "feat: token-based budget tracking with company-level metric setting" && git push
```
