# Per-Agent Messaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Telegram messaging config from instance-level singleton to per-agent `adapter_config.messaging`, so each agent can have its own Telegram bot.

**Architecture:** Messaging config moves from `instance_settings.messaging` to `agents.adapter_config.messaging`. The orchestrator reads per-agent config from the agents table. A new "Messaging" tab on the Agent Detail page replaces the Instance Messaging Settings page.

**Tech Stack:** Python (orchestrator), TypeScript/React (Paperclip UI), Paperclip server (Express)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `hermes-gateway/orchestrator/orchestrator.py` | Modify | Read messaging from `adapter_config` per-agent |
| `paperclip/ui/src/components/AgentMessagingTab.tsx` | Create | Per-agent messaging form |
| `paperclip/ui/src/pages/AgentDetail.tsx` | Modify | Add "Messaging" tab |
| `AGENTS.md` | Modify | Update messaging section |

---

### Task 1: Update Orchestrator to read per-agent messaging

**Files:**
- Modify: `hermes-gateway/orchestrator/orchestrator.py`

- [ ] **Step 1: Update `fetch_agents_from_db()` to include `adapter_config`**

In `fetch_agents_from_db()`, add `adapter_config` to the SELECT query and include it in the returned dicts:

```python
cur.execute("""
    SELECT a.id, a.name, a.role, a.company_id, a.adapter_config
    FROM agents a
    JOIN company_memberships cm
        ON cm.principal_id = a.id::text
        AND cm.principal_type = 'agent'
    WHERE a.adapter_type = 'hermes_local'
      AND a.status NOT IN ('terminated', 'paused')
    ORDER BY a.name
""")
```

Add to the dict building loop:
```python
agents.append({
    "id": str(row["id"]),
    "name": row["name"],
    "role": row["role"],
    "companyId": str(row["company_id"]),
    "adapter_config": row["adapter_config"] or {},
})
```

- [ ] **Step 2: Remove `_fetch_messaging_config()`**

Delete the entire `_fetch_messaging_config()` function (it reads from `instance_settings`).

- [ ] **Step 3: Update `provision_agent()` to read messaging from agent data**

Replace the messaging config block in `provision_agent()`. Change from:

```python
messaging_config = _fetch_messaging_config()
agent_telegram = messaging_config.get("telegram", {})
```

To:

```python
adapter_config = agent.get("adapter_config", {}) or {}
agent_messaging = adapter_config.get("messaging", {}) or {}
agent_telegram = agent_messaging.get("telegram", {})
```

- [ ] **Step 4: Update `_agent_data_changed()` to detect adapter_config changes**

Add `adapter_config` comparison to `_agent_data_changed()`:

```python
def _agent_data_changed(self, agent_id: str, agent: dict) -> bool:
    stored = self._known_agents.get(agent_id)
    if not stored:
        return True
    return (
        stored.get("role") != agent.get("role")
        or stored.get("name") != agent.get("name")
        or stored.get("adapter_config") != agent.get("adapter_config")
    )
```

- [ ] **Step 5: Verify with docker compose build**

Run: `docker compose build hermes-gateway`
Expected: Build succeeds.

- [ ] **Step 6: Commit**

```bash
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "feat: read messaging config from per-agent adapter_config instead of instance_settings"
```

---

### Task 2: Create AgentMessagingTab component

**Files:**
- Create: `paperclip/ui/src/components/AgentMessagingTab.tsx`
- Reference: `paperclip/ui/src/pages/InstanceMessagingSettings.tsx`

- [ ] **Step 1: Create the component**

Create `paperclip/ui/src/components/AgentMessagingTab.tsx` adapted from `InstanceMessagingSettings.tsx`, but:
- Reads from `agent.adapterConfig.messaging` instead of instance settings API
- Saves via `agentsApi.update()` with `adapterConfig: { messaging: {...} }`
- Uses immediate save on blur (same UX as instance settings)

```tsx
import { useState, useEffect } from "react";
import { MessageSquare } from "lucide-react";
import { agentsApi } from "../api/agents";

interface AgentMessagingTabProps {
  agent: {
    id: string;
    companyId?: string;
    adapterConfig?: Record<string, unknown>;
  };
  onUpdated?: () => void;
}

interface TelegramConfig {
  enabled: boolean;
  botToken?: string;
  chatId?: string;
  allowedUsers?: string;
  defaultTimeout: number;
}

function getMessaging(agent: AgentMessagingTabProps["agent"]): { telegram?: TelegramConfig } {
  const ac = (agent.adapterConfig ?? {}) as Record<string, unknown>;
  return (ac.messaging as { telegram?: TelegramConfig }) ?? {};
}

export function AgentMessagingTab({ agent, onUpdated }: AgentMessagingTabProps) {
  const telegram = getMessaging(agent).telegram;
  const telegramEnabled = telegram?.enabled ?? false;

  const [draft, setDraft] = useState({
    botToken: telegram?.botToken ?? "",
    chatId: telegram?.chatId ?? "",
    allowedUsers: telegram?.allowedUsers ?? "",
    defaultTimeout: telegram?.defaultTimeout ?? 600,
  });

  useEffect(() => {
    const t = getMessaging(agent).telegram;
    setDraft({
      botToken: t?.botToken ?? "",
      chatId: t?.chatId ?? "",
      allowedUsers: t?.allowedUsers ?? "",
      defaultTimeout: t?.defaultTimeout ?? 600,
    });
  }, [agent.adapterConfig]);

  const save = (tg: TelegramConfig) => {
    const current = getMessaging(agent);
    agentsApi.update(agent.id, {
      adapterConfig: {
        ...agent.adapterConfig,
        messaging: { ...current, telegram: tg },
      },
    }, agent.companyId).then(() => onUpdated?.());
  };

  const toggleEnabled = () => {
    save({
      enabled: !telegramEnabled,
      botToken: draft.botToken || undefined,
      chatId: draft.chatId || undefined,
      allowedUsers: draft.allowedUsers || undefined,
      defaultTimeout: draft.defaultTimeout,
    });
  };

  const fieldStyle = "w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm";

  return (
    <div className="space-y-6 max-w-xl">
      <div className="flex items-center gap-3">
        <MessageSquare className="h-5 w-5 text-gray-500" />
        <h3 className="text-lg font-medium">Telegram</h3>
        <label className="relative inline-flex cursor-pointer ml-auto">
          <input type="checkbox" checked={telegramEnabled} onChange={toggleEnabled} className="sr-only peer" />
          <div className="w-9 h-5 bg-gray-200 peer-checked:bg-blue-600 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all" />
        </label>
      </div>

      {telegramEnabled && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Bot Token</label>
            <input
              type="password"
              placeholder="From @BotFather"
              className={fieldStyle}
              value={draft.botToken}
              onChange={(e) => setDraft((d) => ({ ...d, botToken: e.target.value }))}
              onBlur={() => {
                if (draft.botToken !== (telegram?.botToken ?? "")) {
                  save({ enabled: true, botToken: draft.botToken || undefined, chatId: telegram?.chatId, allowedUsers: telegram?.allowedUsers, defaultTimeout: telegram?.defaultTimeout ?? 600 });
                }
              }}
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Chat ID</label>
            <input
              type="text"
              placeholder="-1001234567890"
              className={fieldStyle}
              value={draft.chatId}
              onChange={(e) => setDraft((d) => ({ ...d, chatId: e.target.value }))}
              onBlur={() => {
                if (draft.chatId !== (telegram?.chatId ?? "")) {
                  save({ enabled: true, botToken: telegram?.botToken, chatId: draft.chatId || undefined, allowedUsers: telegram?.allowedUsers, defaultTimeout: telegram?.defaultTimeout ?? 600 });
                }
              }}
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Allowed Users</label>
            <input
              type="text"
              placeholder="Comma-separated Telegram user IDs"
              className={fieldStyle}
              value={draft.allowedUsers}
              onChange={(e) => setDraft((d) => ({ ...d, allowedUsers: e.target.value }))}
              onBlur={() => {
                if (draft.allowedUsers !== (telegram?.allowedUsers ?? "")) {
                  save({ enabled: true, botToken: telegram?.botToken, chatId: telegram?.chatId, allowedUsers: draft.allowedUsers || undefined, defaultTimeout: telegram?.defaultTimeout ?? 600 });
                }
              }}
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Response Timeout (seconds)</label>
            <input
              type="number"
              min={60}
              max={3600}
              className={fieldStyle}
              value={draft.defaultTimeout}
              onChange={(e) => setDraft((d) => ({ ...d, defaultTimeout: Math.max(60, Math.min(3600, Number(e.target.value) || 600)) }))}
              onBlur={() => {
                const clamped = Math.max(60, Math.min(3600, draft.defaultTimeout));
                if (clamped !== telegram?.defaultTimeout) {
                  save({ enabled: true, botToken: telegram?.botToken, chatId: telegram?.chatId, allowedUsers: telegram?.allowedUsers, defaultTimeout: clamped });
                }
              }}
            />
          </div>
        </div>
      )}

      {!telegramEnabled && (
        <p className="text-sm text-gray-500">Enable Telegram to allow this agent to communicate via messaging.</p>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add paperclip/ui/src/components/AgentMessagingTab.tsx
git commit -m "feat: add AgentMessagingTab component for per-agent Telegram config"
```

---

### Task 3: Add Messaging tab to AgentDetail page

**Files:**
- Modify: `paperclip/ui/src/pages/AgentDetail.tsx`

- [ ] **Step 1: Add "messaging" to the view type union**

In `AgentDetail.tsx`, find the `AgentDetailView` type (around line 226):

```ts
type AgentDetailView = "dashboard" | "instructions" | "configuration" | "skills" | "runs" | "budget";
```

Change to:

```ts
type AgentDetailView = "dashboard" | "instructions" | "configuration" | "skills" | "messaging" | "runs" | "budget";
```

- [ ] **Step 2: Add "messaging" case to `parseAgentDetailView`**

Find the function that parses the URL segment to a view (around line 228). Add `"messaging"` to the valid values.

- [ ] **Step 3: Add Messaging tab to the tab bar**

Find the `PageTabBar` component (around line 1008-1016). Add a tab item:

```tsx
{ value: "messaging", label: "Messaging" }
```

Place it between "Skills" and "Runs" tabs.

- [ ] **Step 4: Import and render `AgentMessagingTab`**

Add import at the top:

```ts
import { AgentMessagingTab } from "../components/AgentMessagingTab";
```

Find where the tab views are rendered (after the other view sections like skills, configuration). Add:

```tsx
{activeView === "messaging" && (
  <AgentMessagingTab agent={agent} onUpdated={() => queryClient.invalidateQueries({ queryKey: agentQueryKey })} />
)}
```

Use the same `queryClient.invalidateQueries` pattern used by other tabs to refresh agent data after save.

- [ ] **Step 5: Build UI in container**

Run: `docker exec -w /app/ui paperclip-server node node_modules/vite/bin/vite.js build`
Expected: Build succeeds.

- [ ] **Step 6: Commit**

```bash
git add paperclip/ui/src/pages/AgentDetail.tsx
git commit -m "feat: add Messaging tab to AgentDetail page"
```

---

### Task 4: Update AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Update the messaging section**

Replace the instance-level messaging references with per-agent messaging info. Add a section about per-agent messaging config under the Outline MCP section.

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md with per-agent messaging config"
```

---

### Task 5: Deploy and verify

- [ ] **Step 1: Set messaging config for Founding Engineer via DB**

Since the UI is just built and not deployed yet, set the initial config via SQL:

```sql
UPDATE agents
SET adapter_config = jsonb_set(
  COALESCE(adapter_config, '{}'::jsonb),
  '{messaging}',
  '{"telegram":{"enabled":true,"botToken":"8749254241:AAEzsxPdzhPQ4rUIHPQr9KWq1_kIXsemHlQ","chatId":"-3825858816","allowedUsers":"134922733","defaultTimeout":600}}'::jsonb
)
WHERE id = 'c7826470-3b08-49ad-b1d9-e73911ed64f9';
```

- [ ] **Step 2: Rebuild and deploy hermes-gateway**

```bash
docker compose up -d --force-recreate --build hermes-gateway
```

- [ ] **Step 3: Verify orchestrator picks up per-agent config**

```bash
docker logs hermes-gateway --tail 30 2>&1 | grep -E "(telegram|Starting gateway|Found)"
```

Expected: Agent starts with telegram enabled, no instance_settings query.

- [ ] **Step 4: Verify agent config has telegram from adapter_config**

```bash
docker exec hermes-gateway cat /root/.hermes/profiles/c7826470-3b08-49ad-b1d9-e73911ed64f9/config.yaml | grep -A5 telegram
```

Expected: Telegram section present with bot token from adapter_config.
