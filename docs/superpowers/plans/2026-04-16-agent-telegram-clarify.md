# Agent Telegram Q&A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable agents to ask clarifying questions in Telegram chat via the `clarify` tool, with per-instance messaging config in Paperclip UI.

**Architecture:** Messaging config stored as `messaging` JSONB column in `instance_settings` table. Orchestrator reads config from DB and provisions agents with Telegram platform. A new `clarify_bridge.py` module provides synchronous blocking callback that sends questions to Telegram via Bot API and waits for replies through a global registry matched by Telegram adapter.

**Tech Stack:** TypeScript/React (Paperclip), Python (hermes-gateway, hermes-agent), PostgreSQL (JSONB), httpx (Telegram Bot API), threading.Event (sync blocking)

**Spec:** `docs/superpowers/specs/2026-04-16-agent-telegram-clarify-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `paperclip/packages/db/src/schema/instance_settings.ts:9` | Add `messaging` JSONB column |
| Modify | `paperclip/packages/shared/src/types/instance.ts` | `InstanceMessagingSettings` type |
| Modify | `paperclip/packages/shared/src/validators/instance.ts` | Zod schema for messaging |
| Modify | `paperclip/packages/shared/src/index.ts` | Export new types/schemas |
| Modify | `paperclip/server/src/services/instance-settings.ts` | Messaging CRUD methods |
| Modify | `paperclip/server/src/routes/instance-settings.ts` | PATCH messaging endpoint |
| Modify | `paperclip/server/src/__tests__/instance-settings-routes.test.ts` | Tests for messaging endpoint |
| Modify | `paperclip/ui/src/pages/InstanceGeneralSettings.tsx:281` | Messaging UI section |
| Modify | `hermes-gateway/orchestrator/orchestrator.py:61-79,165-181,195-203,215-228` | Read messaging from DB, pass to config, env vars, supervisor conf, patch list |
| Modify | `hermes-gateway/orchestrator/config_generator.py:14-62` | Accept `clarify_timeout`, include in YAML |
| Create | `hermes-gateway/orchestrator/clarify_bridge.py` | Pending registry + callback factory |
| Modify | `hermes-agent/gateway/platforms/api_server.py:1446-1451` | Wire `clarify_callback` after agent creation |
| Modify | `hermes-agent/gateway/platforms/telegram.py:1680` | Check `_pending_clarify` on incoming text |

---

### Task 1: DB Schema — Add messaging column

**Files:**
- Modify: `paperclip/packages/db/src/schema/instance_settings.ts:9`

- [ ] **Step 1: Add messaging column to schema**

Read `paperclip/packages/db/src/schema/instance_settings.ts`. After the `experimental` column (line 9), add:

```typescript
messaging: jsonb("messaging").$type<Record<string, unknown>>().notNull().default({}),
```

- [ ] **Step 2: Generate migration**

Run: `cd /mnt/services/hw-rnd-ai-crew/paperclip && pnpm db:generate`

Expected: New migration file in `packages/db/drizzle/` adding `messaging` column.

- [ ] **Step 3: Verify typecheck**

Run: `cd /mnt/services/hw-rnd-ai-crew/paperclip && pnpm -r typecheck`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add paperclip/packages/db/
git commit -m "feat(db): add messaging JSONB column to instance_settings"
```

---

### Task 2: Shared Types and Validators

**Files:**
- Modify: `paperclip/packages/shared/src/types/instance.ts`
- Modify: `paperclip/packages/shared/src/validators/instance.ts`
- Modify: `paperclip/packages/shared/src/index.ts`

- [ ] **Step 1: Add messaging types**

In `paperclip/packages/shared/src/types/instance.ts`, add before `InstanceSettings`:

```typescript
export interface InstanceMessagingTelegramSettings {
  enabled: boolean;
  botToken?: string;
  chatId?: string;
  allowedUsers?: string;
  defaultTimeout: number;
}

export interface InstanceMessagingSettings {
  telegram?: InstanceMessagingTelegramSettings;
}
```

Update `InstanceSettings` to include messaging:

```typescript
export interface InstanceSettings {
  id: string;
  general: InstanceGeneralSettings;
  experimental: InstanceExperimentalSettings;
  messaging: InstanceMessagingSettings;
  createdAt: Date;
  updatedAt: Date;
}
```

- [ ] **Step 2: Add Zod validators**

In `paperclip/packages/shared/src/validators/instance.ts`, add after `instanceExperimentalSettingsSchema`:

```typescript
export const messagingSettingsSchema = z.object({
  telegram: z.object({
    enabled: z.boolean().default(false),
    botToken: z.string().optional(),
    chatId: z.string().optional(),
    allowedUsers: z.string().optional(),
    defaultTimeout: z.number().min(60).max(3600).default(600),
  }).optional(),
}).strict();

export const patchMessagingSettingsSchema = messagingSettingsSchema;

export type MessagingSettings = z.infer<typeof messagingSettingsSchema>;
export type PatchMessagingSettings = z.infer<typeof patchMessagingSettingsSchema>;
```

Note: `patchMessagingSettingsSchema` is NOT partial — the entire `messaging` object is replaced on PATCH (same pattern as experimental settings).

- [ ] **Step 3: Export from shared index**

In `paperclip/packages/shared/src/index.ts`, add to the appropriate export blocks:

Find the existing `instanceGeneralSettingsSchema` exports and add nearby:
```typescript
messagingSettingsSchema,
patchMessagingSettingsSchema,
type MessagingSettings,
type PatchMessagingSettings,
```

Find the existing `InstanceGeneralSettings` type export and add nearby:
```typescript
InstanceMessagingSettings,
InstanceMessagingTelegramSettings,
```

- [ ] **Step 4: Verify typecheck**

Run: `cd /mnt/services/hw-rnd-ai-crew/paperclip && pnpm -r typecheck`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add paperclip/packages/shared/
git commit -m "feat(shared): add InstanceMessagingSettings types and validators"
```

---

### Task 3: Server — Messaging Service Methods

**Files:**
- Modify: `paperclip/server/src/services/instance-settings.ts`

- [ ] **Step 1: Add import**

In `paperclip/server/src/services/instance-settings.ts`, update the imports from `@paperclipai/shared` to include:

```typescript
import {
  DEFAULT_FEEDBACK_DATA_SHARING_PREFERENCE,
  instanceGeneralSettingsSchema,
  type InstanceGeneralSettings,
  instanceExperimentalSettingsSchema,
  type InstanceExperimentalSettings,
  messagingSettingsSchema,
  type InstanceMessagingSettings,
  type PatchInstanceGeneralSettings,
  type InstanceSettings,
  type PatchInstanceExperimentalSettings,
} from "@paperclipai/shared";
```

- [ ] **Step 2: Add normalizeMessagingSettings function**

After `normalizeExperimentalSettings`, add:

```typescript
function normalizeMessagingSettings(raw: unknown): InstanceMessagingSettings {
  const parsed = messagingSettingsSchema.safeParse(raw ?? {});
  if (parsed.success) {
    return parsed.data;
  }
  return {};
}
```

- [ ] **Step 3: Update toInstanceSettings**

Update `toInstanceSettings` to include messaging:

```typescript
function toInstanceSettings(row: typeof instanceSettings.$inferSelect): InstanceSettings {
  return {
    id: row.id,
    general: normalizeGeneralSettings(row.general),
    experimental: normalizeExperimentalSettings(row.experimental),
    messaging: normalizeMessagingSettings(row.messaging),
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
  };
}
```

- [ ] **Step 4: Add getMessaging and updateMessaging methods**

Add to the returned service object (after `updateExperimental`):

```typescript
getMessaging: async (): Promise<InstanceMessagingSettings> => {
  const row = await getOrCreateRow();
  return normalizeMessagingSettings(row.messaging);
},

updateMessaging: async (patch: InstanceMessagingSettings): Promise<InstanceSettings> => {
  const current = await getOrCreateRow();
  const now = new Date();
  const [updated] = await db
    .update(instanceSettings)
    .set({
      messaging: { ...patch },
      updatedAt: now,
    })
    .where(eq(instanceSettings.id, current.id))
    .returning();
  return toInstanceSettings(updated ?? current);
},
```

- [ ] **Step 5: Verify typecheck**

Run: `cd /mnt/services/hw-rnd-ai-crew/paperclip && pnpm -r typecheck`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add paperclip/server/src/services/instance-settings.ts
git commit -m "feat(server): add messaging CRUD to instance settings service"
```

---

### Task 4: Server — PATCH Messaging Route

**Files:**
- Modify: `paperclip/server/src/routes/instance-settings.ts:71`

- [ ] **Step 1: Add import**

In `paperclip/server/src/routes/instance-settings.ts`, find the imports from `@paperclipai/shared` and add:

```typescript
messagingSettingsSchema,
```

- [ ] **Step 2: Add PATCH messaging route**

After the PATCH experimental route (ends around line 99), add:

```typescript
router.patch("/instance/settings/messaging", requireBoard(), async (req, res) => {
  const patch = messagingSettingsSchema.parse(req.body);
  const settings = await instanceSettingsService(db).updateMessaging(patch);
  res.json({ settings });
});
```

Follow the exact same pattern as the existing PATCH general and PATCH experimental routes in the file. Use `requireBoard()` for auth (same as general/experimental).

- [ ] **Step 3: Verify typecheck**

Run: `cd /mnt/services/hw-rnd-ai-crew/paperclip && pnpm -r typecheck`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add paperclip/server/src/routes/instance-settings.ts
git commit -m "feat(server): add PATCH /instance/settings/messaging endpoint"
```

---

### Task 5: Server — Route Tests

**Files:**
- Modify: `paperclip/server/src/__tests__/instance-settings-routes.test.ts`

- [ ] **Step 1: Add test for GET messaging**

Read the existing test file to understand the test patterns (mock setup, request helpers, auth checks). Add a new test block following the same pattern:

```typescript
describe("GET /instance/settings", () => {
  // Find the existing GET test and verify messaging is included in the response
  it("includes messaging settings in response", async () => {
    // Follow existing GET test pattern — the toInstanceSettings now includes messaging
    const res = await request(app).get("/api/instance/settings").set("Authorization", boardAuth);
    expect(res.status).toBe(200);
    expect(res.body.settings).toHaveProperty("messaging");
  });
});
```

- [ ] **Step 2: Add tests for PATCH messaging**

```typescript
describe("PATCH /instance/settings/messaging", () => {
  it("updates messaging settings", async () => {
    const patch = {
      telegram: {
        enabled: true,
        botToken: "123456:ABC",
        chatId: "-1001234567890",
        allowedUsers: "user1",
        defaultTimeout: 600,
      },
    };
    const res = await request(app)
      .patch("/api/instance/settings/messaging")
      .set("Authorization", boardAuth)
      .send(patch);
    expect(res.status).toBe(200);
    expect(res.body.settings.messaging.telegram.enabled).toBe(true);
    expect(res.body.settings.messaging.telegram.botToken).toBe("123456:ABC");
  });

  it("rejects invalid timeout values", async () => {
    const res = await request(app)
      .patch("/api/instance/settings/messaging")
      .set("Authorization", boardAuth)
      .send({ telegram: { enabled: true, defaultTimeout: 10 } });
    expect(res.status).toBe(400);
  });

  it("requires board auth", async () => {
    const res = await request(app)
      .patch("/api/instance/settings/messaging")
      .send({ telegram: { enabled: false } });
    expect(res.status).toBe(401);
  });

  it("clears messaging settings", async () => {
    const res = await request(app)
      .patch("/api/instance/settings/messaging")
      .set("Authorization", boardAuth)
      .send({});
    expect(res.status).toBe(200);
    expect(res.body.settings.messaging).toEqual({});
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd /mnt/services/hw-rnd-ai-crew/paperclip && pnpm test:run`

Expected: All tests pass (existing + new).

- [ ] **Step 4: Commit**

```bash
git add paperclip/server/src/__tests__/instance-settings-routes.test.ts
git commit -m "test(server): add messaging route tests"
```

---

### Task 6: UI — Messaging Settings Section

**Files:**
- Modify: `paperclip/ui/src/pages/InstanceGeneralSettings.tsx`

- [ ] **Step 1: Add state for messaging settings**

Read the file to understand how `generalSettings` and `experimentalSettings` state is managed. Add a similar `messagingSettings` state. Find the existing fetch/save pattern and add messaging fetch/save following the same pattern.

In the component, add state:

```typescript
const [messagingSettings, setMessagingSettings] = useState<any>(null);
```

Add fetch in the existing `useEffect` or data loading:

```typescript
fetch("/api/instance/settings", { headers: { Authorization: `Bearer ${token}` } })
  .then((r) => r.json())
  .then((data) => {
    // existing general/experimental loading...
    setMessagingSettings(data.settings?.messaging || {});
  });
```

Add save handler:

```typescript
const handleSaveMessaging = async (patch: any) => {
  const res = await fetch("/api/instance/settings/messaging", {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(patch),
  });
  const data = await res.json();
  setMessagingSettings(data.settings?.messaging || {});
};
```

- [ ] **Step 2: Add Messaging section JSX**

Insert a new `<section>` between the Regional section (ends ~line 281) and the "Sign out" section (~line 283). Follow the exact same Tailwind classes and layout pattern as the Regional section:

```tsx
<section className="rounded-lg border border-gray-200 p-6">
  <h2 className="text-lg font-semibold mb-4">Messaging</h2>
  <p className="text-sm text-gray-500 mb-4">
    Configure messaging platforms for agent Q&A. Agents can ask clarifying questions and receive replies.
  </p>

  <div className="space-y-4">
    <div className="flex items-center justify-between">
      <div>
        <p className="font-medium">Enable Telegram Q&A</p>
        <p className="text-sm text-gray-500">Allow agents to ask questions via Telegram</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={messagingSettings?.telegram?.enabled ?? false}
        onClick={() => handleSaveMessaging({
          ...messagingSettings,
          telegram: { ...messagingSettings?.telegram, enabled: !(messagingSettings?.telegram?.enabled ?? false) },
        })}
        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
          messagingSettings?.telegram?.enabled ? "bg-blue-600" : "bg-gray-200"
        }`}
      >
        <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
          messagingSettings?.telegram?.enabled ? "translate-x-6" : "translate-x-1"
        }`} />
      </button>
    </div>

    {messagingSettings?.telegram?.enabled && (
      <div className="space-y-3 pl-0 pt-2">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Bot Token</label>
          <input
            type="password"
            value={messagingSettings?.telegram?.botToken ?? ""}
            onChange={(e) => setMessagingSettings({
              ...messagingSettings,
              telegram: { ...messagingSettings?.telegram, botToken: e.target.value },
            })}
            onBlur={() => handleSaveMessaging(messagingSettings)}
            placeholder="From @BotFather"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Chat ID</label>
          <input
            type="text"
            value={messagingSettings?.telegram?.chatId ?? ""}
            onChange={(e) => setMessagingSettings({
              ...messagingSettings,
              telegram: { ...messagingSettings?.telegram, chatId: e.target.value },
            })}
            onBlur={() => handleSaveMessaging(messagingSettings)}
            placeholder="-1001234567890"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Allowed Users</label>
          <input
            type="text"
            value={messagingSettings?.telegram?.allowedUsers ?? ""}
            onChange={(e) => setMessagingSettings({
              ...messagingSettings,
              telegram: { ...messagingSettings?.telegram, allowedUsers: e.target.value },
            })}
            onBlur={() => handleSaveMessaging(messagingSettings)}
            placeholder="Comma-separated Telegram user IDs"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Response Timeout (seconds)
          </label>
          <input
            type="number"
            min={60}
            max={3600}
            value={messagingSettings?.telegram?.defaultTimeout ?? 600}
            onChange={(e) => setMessagingSettings({
              ...messagingSettings,
              telegram: { ...messagingSettings?.telegram, defaultTimeout: parseInt(e.target.value) || 600 },
            })}
            onBlur={() => handleSaveMessaging(messagingSettings)}
            className="w-48 rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
          <p className="text-xs text-gray-500 mt-1">How long agents wait for a reply (60–3600)</p>
        </div>
      </div>
    )}
  </div>
</section>
```

Note: Follow the existing UI patterns in the file exactly. The code above is a guide — adapt the class names, component patterns, and state management to match what already exists. Check how other sections handle save (onBlur vs explicit save button) and replicate.

- [ ] **Step 3: Build UI**

Run: `docker exec -w /app/ui paperclip-server node node_modules/vite/bin/vite.js build`

Expected: Build succeeds.

- [ ] **Step 4: Commit**

```bash
git add paperclip/ui/src/pages/InstanceGeneralSettings.tsx
git commit -m "feat(ui): add Messaging section to instance settings"
```

---

### Task 7: Clarify Bridge Module

**Files:**
- Create: `hermes-gateway/orchestrator/clarify_bridge.py`

- [ ] **Step 1: Create clarify_bridge.py**

Write `hermes-gateway/orchestrator/clarify_bridge.py`:

```python
import logging
import threading

import httpx

logger = logging.getLogger("clarify-bridge")

_pending_clarify: dict[tuple[str, str], dict] = {}
_lock = threading.Lock()


def register_pending_clarify(bot_token: str, chat_id: str) -> dict:
    key = (bot_token, chat_id)
    entry = {"event": threading.Event(), "answer": None, "question_msg_id": None}
    with _lock:
        _pending_clarify[key] = entry
    return entry


def resolve_clarify_reply(bot_token: str, chat_id: str, reply_text: str) -> bool:
    key = (bot_token, chat_id)
    with _lock:
        entry = _pending_clarify.get(key)
        if entry and not entry["event"].is_set():
            entry["answer"] = reply_text
            entry["event"].set()
            _pending_clarify.pop(key, None)
            return True
    return False


def make_clarify_callback(bot_token: str, chat_id: str, timeout: int = 600):
    def callback(question: str, choices: list[str] | None) -> str:
        text = f"\u2753 *Agent asks:*\n\n{question}"
        if choices:
            lines = [f"{i + 1}. {c}" for i, c in enumerate(choices)]
            lines.append(f"{len(choices) + 1}. Other (type your answer)")
            text += "\n\n" + "\n".join(lines)
        text += "\n\n_Reply to this message with your answer._"

        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=30,
            )
            result = resp.json()
            if not result.get("ok"):
                logger.error("Telegram sendMessage failed: %s", result)
                return f"[Failed to send question to Telegram: {result.get('description', 'unknown error')}]"
        except Exception as exc:
            logger.error("Telegram sendMessage error: %s", exc)
            return f"[Failed to send question to Telegram: {exc}]"

        entry = register_pending_clarify(bot_token, chat_id)

        if entry["event"].wait(timeout=timeout):
            return entry["answer"]
        else:
            with _lock:
                _pending_clarify.pop((bot_token, chat_id), None)
            return "[No response received within timeout. Proceeding without clarification.]"

    return callback
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/orchestrator/clarify_bridge.py
git commit -m "feat(gateway): add clarify_bridge for Telegram Q&A blocking callback"
```

---

### Task 8: Orchestrator — Read Messaging Config from DB

**Files:**
- Modify: `hermes-gateway/orchestrator/orchestrator.py:88-108,155-239`

- [ ] **Step 1: Add _fetch_messaging_config function**

After `fetch_agents_from_db()` (ends at line 108), add:

```python
def _fetch_messaging_config() -> dict:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.set_session(autocommit=True, readonly=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT messaging FROM instance_settings WHERE singleton_key = 'default'")
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row["messaging"]:
            return row["messaging"]
        return {}
    except Exception as e:
        logger.error("Failed to fetch messaging config: %s", e)
        return {}
```

- [ ] **Step 2: Update provision_agent to use messaging config**

In `provision_agent()` (starts line 155), replace the telegram config block (lines 165-172). Change from:

```python
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        allowed_users = os.environ.get("TELEGRAM_ALLOWED_USERS", "")

        role = agent.get("role", "")
        name = agent.get("name", "Agent")
        company_id = agent.get("companyId", agent.get("company_id", ""))
        enable_telegram = bool(telegram_token and telegram_chat_id and role in ("ceo", "cto"))
```

To:

```python
        messaging_config = _fetch_messaging_config()
        agent_telegram = messaging_config.get("telegram", {})
        enable_telegram = (
            agent_telegram.get("enabled", False)
            and bool(agent_telegram.get("botToken"))
            and bool(agent_telegram.get("chatId"))
        )

        name = agent.get("name", "Agent")
        company_id = agent.get("companyId", agent.get("company_id", ""))
```

Then update the `generate_profile_config` call. Change from:

```python
            telegram_bot_token=telegram_token if enable_telegram else None,
            telegram_chat_id=telegram_chat_id if enable_telegram else None,
            telegram_allowed_users=allowed_users if enable_telegram else None,
```

To:

```python
            telegram_bot_token=agent_telegram.get("botToken") if enable_telegram else None,
            telegram_chat_id=agent_telegram.get("chatId") if enable_telegram else None,
            telegram_allowed_users=agent_telegram.get("allowedUsers") if enable_telegram else None,
            telegram_clarify_timeout=agent_telegram.get("defaultTimeout", 600) if enable_telegram else None,
```

- [ ] **Step 3: Add messaging env vars to .env and supervisor conf**

In the `.env` writing block (line 195), add after `FAL_KEY`:

```python
            f"TELEGRAM_BOT_TOKEN={agent_telegram.get('botToken', '') if enable_telegram else ''}",
            f"TELEGRAM_CHAT_ID={agent_telegram.get('chatId', '') if enable_telegram else ''}",
            f"TELEGRAM_CLARIFY_TIMEOUT={agent_telegram.get('defaultTimeout', 600) if enable_telegram else '600'}",
```

In the `program_conf` block (line 215), update the `environment=` line. Change from:

```python
            f"environment=HERMES_HOME=\"{profile_dir}\",PAPERCLIP_RUN_API_KEY=\"{agent_jwt}\"\n"
```

To:

```python
            f"environment=HERMES_HOME=\"{profile_dir}\",PAPERCLIP_RUN_API_KEY=\"{agent_jwt}\",TELEGRAM_BOT_TOKEN=\"{agent_telegram.get('botToken', '') if enable_telegram else ''}\",TELEGRAM_CHAT_ID=\"{agent_telegram.get('chatId', '') if enable_telegram else ''}\",TELEGRAM_CLARIFY_TIMEOUT=\"{agent_telegram.get('defaultTimeout', 600) if enable_telegram else '600'}\"\n"
```

- [ ] **Step 4: Update _patch_installed_agent patch list**

In `_patch_installed_agent()` (line 66), add `gateway/platforms/telegram.py` to the patch list:

```python
    for rel in [
        "gateway/platforms/api_server.py",
        "gateway/platforms/telegram.py",
        "model_tools.py",
        "agent/display.py",
    ]:
```

Also add logic to copy `clarify_bridge.py` into site-packages. After the existing patch loop, add:

```python
    bridge_src = Path(__file__).parent / "clarify_bridge.py"
    bridge_dst = site / "clarify_bridge.py"
    if bridge_src.exists():
        if not bridge_dst.exists() or hashlib.md5(bridge_dst.read_bytes()).hexdigest() != hashlib.md5(bridge_src.read_bytes()).hexdigest():
            shutil.copy2(bridge_src, bridge_dst)
            _patched.append("clarify_bridge.py")
```

- [ ] **Step 5: Commit**

```bash
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "feat(orchestrator): read messaging config from DB, provision Telegram per-agent"
```

---

### Task 9: Config Generator — Add clarify_timeout

**Files:**
- Modify: `hermes-gateway/orchestrator/config_generator.py:14-62`

- [ ] **Step 1: Add parameter and YAML generation**

In `generate_profile_config()`, add new parameter after `telegram_allowed_users`:

```python
    telegram_clarify_timeout: int | None = None,
```

In the telegram YAML generation block (after `allowed_users`), add:

```python
        if telegram_clarify_timeout:
            platforms_lines.append(f"      clarify_timeout: {telegram_clarify_timeout}")
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/orchestrator/config_generator.py
git commit -m "feat(config): add clarify_timeout to Telegram config generation"
```

---

### Task 10: API Server — Wire clarify_callback

**Files:**
- Modify: `hermes-agent/gateway/platforms/api_server.py:1446-1455`

- [ ] **Step 1: Add clarify_callback wiring in _handle_runs**

In `_handle_runs()`, after the agent is created (line 1451) and before `def _run_sync():` (line 1452), insert:

```python
                telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
                clarify_timeout = int(os.environ.get("TELEGRAM_CLARIFY_TIMEOUT", "600"))

                if telegram_token and telegram_chat_id:
                    try:
                        from clarify_bridge import make_clarify_callback
                        agent.clarify_callback = make_clarify_callback(
                            bot_token=telegram_token,
                            chat_id=telegram_chat_id,
                            timeout=clarify_timeout,
                        )
                    except Exception as exc:
                        logger.warning("[api_server] Failed to wire clarify callback: %s", exc)
```

- [ ] **Step 2: Commit**

```bash
git add hermes-agent/gateway/platforms/api_server.py
git commit -m "feat(api_server): wire clarify_callback for Telegram Q&A"
```

---

### Task 11: Telegram Adapter — Check _pending_clarify

**Files:**
- Modify: `hermes-agent/gateway/platforms/telegram.py:1680`

- [ ] **Step 1: Add clarify reply check**

In `_handle_text_message()`, at the very start of the method body (after the docstring if any), add:

```python
        try:
            from clarify_bridge import resolve_clarify_reply
            if resolve_clarify_reply(self._bot.token, str(update.effective_chat.id), update.message.text):
                await update.message.reply_text("\u2705 \u041e\u0442\u0432\u0435\u0442 \u043f\u0435\u0440\u0435\u0434\u0430\u043d \u0430\u0433\u0435\u043d\u0442\u0443.")
                return
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("clarify_bridge check error: %s", exc)
```

Note: `self._bot.token` provides the bot token. If the attribute name differs (check the TelegramAdapter class), use the correct one — it might be `self._token`, `self.token`, or accessed through the context.

- [ ] **Step 2: Commit**

```bash
git add hermes-agent/gateway/platforms/telegram.py
git commit -m "feat(telegram): check _pending_clarify on incoming text messages"
```

---

### Task 12: Integration Verification

- [ ] **Step 1: Run Paperclip typecheck + tests**

Run: `cd /mnt/services/hw-rnd-ai-crew/paperclip && pnpm -r typecheck && pnpm test:run`

Expected: All pass.

- [ ] **Step 2: Build Paperclip UI**

Run: `docker exec -w /app/ui paperclip-server node node_modules/vite/bin/vite.js build`

Expected: Build succeeds.

- [ ] **Step 3: Verify Docker Compose config**

Run: `docker compose config --quiet`

Expected: No errors.

- [ ] **Step 4: Final commit (if any fixes needed)**

If any fixes were applied during verification, commit them.

---

### Task 13: Deploy and Smoke Test

- [ ] **Step 1: Rebuild hermes-gateway image**

Run: `docker compose up -d --force-recreate --build hermes-gateway`

- [ ] **Step 2: Verify orchestrator starts**

Run: `docker logs hermes-gateway --tail 20`

Expected: Logs show "Orchestrator starting...", agents provisioned.

- [ ] **Step 3: Configure Telegram in Paperclip UI**

Navigate to Instance Settings → Messaging → Enable Telegram Q&A → fill Bot Token, Chat ID → save.

- [ ] **Step 4: Verify agent config updated**

Wait 60s for orchestrator poll, then check:

Run: `docker exec hermes-gateway cat /root/.hermes/profiles/<agent_id>/config.yaml | grep -A 5 telegram`

Expected: Telegram platform config present with token and chat_id.

- [ ] **Step 5: Trigger a test run**

Create a task in Paperclip that requires clarification. Verify question appears in Telegram chat.

- [ ] **Step 6: Reply in Telegram**

Reply to the question. Verify the agent receives the answer and continues.
