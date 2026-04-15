# Dual-Metric Budget Policy UI

## Problem

An agent can have two active budget policies: `billed_cents` and `total_tokens`. The UI only shows and manages one (the company's default metric). Setting budget to 0 only zeroes one metric's policy while the other continues enforcing hard-stop — confusing and blocking.

## Solution

Render two separate `BudgetPolicyCard` components per scope — one per metric. No changes to `BudgetPolicyCard.tsx` itself; only parent components pass the correct `metric` and include it in the mutation payload.

## Changes

### Costs.tsx — Budgets tab

For each scope group (company, agent, project), instead of rendering one card per policy:

1. Group `budgetOverview.policies` by `(scopeType, scopeId)`.
2. For each group, find (or synthesize) a summary for each of `["billed_cents", "total_tokens"]`.
3. Render two `BudgetPolicyCard` components — one per metric.
4. `onSave` callback includes `metric` in the mutation payload: `{ scopeType, scopeId, amount, windowKind, metric }`.

If a policy for a metric doesn't exist yet, synthesize a placeholder `BudgetPolicySummary` with `amount: 0, observedAmount: 0, isActive: false, status: "ok"` so the user can create it by setting a non-zero amount.

### AgentDetail.tsx — Budget tab

1. Find policies matching `(scopeType === "agent", scopeId === agent.id)` from `budgetOverview.policies`.
2. Build two `agentBudgetSummary` objects — one per metric.
3. Render two `BudgetPolicyCard` components.
4. `budgetMutation` includes `metric` in the mutation payload.
5. Legacy fallback (from `agent.budgetMonthlyCents`) only applies when no policy exists for `billed_cents`; `total_tokens` gets a synthesized zero-summary.

### BudgetPolicyCard.tsx

No changes. Already accepts `metric` prop and handles both formats.

## Hide all dollar amounts when company uses token mode

When `company.budgetMetric === "total_tokens"`, NO dollar/cent values should appear anywhere.

### Costs.tsx — Overview tab
- Hide `FinanceSummaryCard` entirely (data is real-money invoices, not convertible to tokens)
- Hide "Finance net" and "Finance events" MetricTiles
- Provider/Biller "All" tab labels: hide `formatCents` sum, keep `formatTokens`

### Costs.tsx — Finance tab
- Hide entire tab content when `isTokens` (all finance data is in cents)

### AgentDetail.tsx — Run stats
- Line 1367: "Total cost" metric — show tokens instead of `formatCents` when `isTokens`

### Already correct (guarded by isTokens)
- Inference spend MetricTile, Inference ledger card, By agent/project rows, model breakdown

## API compatibility

The server already supports per-metric policies via `POST /companies/:companyId/budgets/policies` with `metric` in the body. No server changes needed.
