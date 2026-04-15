# Agent Telegram Q&A — Design Spec

Date: 2026-04-16

## Problem

Agents running via Paperclip heartbeat (api_server platform) cannot ask clarifying questions. The `clarify` tool returns "not available in this execution context" because no `clarify_callback` is wired for API server runs. Meanwhile, Telegram platform support in hermes-agent is fully-featured but only enabled for CEO/CTO roles via hardcoded env vars.

## Solution

Enable Telegram Q&A for agents configured through Paperclip UI (instance settings). Messaging config is a **singleton** — one Telegram configuration applies to all agents in the instance. Per-agent overrides are a future extension. When an agent calls `clarify(question, choices)`, the question is sent to the configured Telegram chat, the agent blocks until the user replies (or timeout), then continues with the answer.

Architecture is designed to support future messaging platforms (Mattermost) through the same `messaging` config object.

## Data Flow

```
1. Paperclip heartbeat → adapter → POST /v1/runs (agent_id, JWT, task)
2. api_server._handle_runs() creates agent with clarify_callback
3. Agent calls clarify(question, choices)
   → callback sends question to Telegram via Bot API (httpx POST)
   → registers threading.Event in _pending_clarify registry
   → blocks on event.wait(timeout)
4. User replies in Telegram
   → Telegram adapter checks _pending_clarify registry
   → if match: writes answer, event.set()
   → if no match: normal handling (interrupt / _pending_messages)
5. callback unblocks → returns answer string → agent continues
```

## Data Model

### Instance Settings — messaging column

New `messaging JSONB` column on `instance_settings` table, default `'{}'`.

```typescript
interface InstanceMessagingSettings {
  telegram?: {
    enabled: boolean;       // default false
    botToken?: string;      // from @BotFather
    chatId?: string;        // target chat/group ID
    allowedUsers?: string;  // comma-separated Telegram user IDs
    defaultTimeout?: number; // seconds, min 60, max 3600, default 600
  };
}
```

Zod schema:
```typescript
const messagingSettingsSchema = z.object({
  telegram: z.object({
    enabled: z.boolean().default(false),
    botToken: z.string().optional(),
    chatId: z.string().optional(),
    allowedUsers: z.string().optional(),
    defaultTimeout: z.number().min(60).max(3600).default(600),
  }).optional(),
}).strict();
```

### DB Migration

```sql
ALTER TABLE instance_settings ADD COLUMN messaging JSONB NOT NULL DEFAULT '{}';
```

## Components

### 1. Shared Package (`packages/shared`)

**`src/types/instance.ts`** — add:
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

export interface InstanceSettings {
  id: string;
  general: InstanceGeneralSettings;
  experimental: InstanceExperimentalSettings;
  messaging: InstanceMessagingSettings;
  createdAt: Date;
  updatedAt: Date;
}
```

**`src/validators/instance.ts`** — add `messagingSettingsSchema` and `patchMessagingSettingsSchema`.

### 2. Server (`server/`)

**`src/services/instance-settings.ts`** — add:
- `normalizeMessagingSettings(raw)` — parse with Zod, return defaults
- `getMessaging()` — return messaging settings
- `updateMessaging(patch)` — merge and save

**`src/routes/`** — add PATCH endpoint for messaging settings (same pattern as general/experimental).

### 3. UI (`ui/`)

**`src/pages/InstanceGeneralSettings.tsx`** — new "Messaging" section with:
- Enable Telegram Q&A checkbox
- Bot Token input (masked)
- Chat ID input
- Allowed Users input
- Response Timeout number input (60–3600 seconds)

### 4. Orchestrator (`hermes-gateway/orchestrator/`)

**`orchestrator.py`** changes:
- Add `_fetch_messaging_config()` — reads `messaging` JSON from `instance_settings` table
- `provision_agent()` reads messaging config instead of env vars for Telegram
- Passes `clarify_timeout` to `config_generator`
- Adds env vars to supervisor conf: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_CLARIFY_TIMEOUT`
- Adds `telegram.py` and `clarify_bridge.py` to `_patch_installed_agent()` list (clarify_bridge is copied to site-packages for import by api_server)

**`config_generator.py`** changes:
- New parameter `telegram_clarify_timeout: int | None = None`
- Adds `clarify_timeout` to telegram extra in generated YAML

### 5. Clarify Bridge (`hermes-gateway/orchestrator/clarify_bridge.py`)

New file. Provides:

- `_pending_clarify: dict[tuple[str, str], dict]` — registry keyed by `(bot_token, chat_id)`
- `register_pending_clarify(bot_token, chat_id) -> dict` — creates entry with `threading.Event`
- `resolve_clarify_reply(bot_token, chat_id, reply_text) -> bool` — signals waiting agent
- `make_clarify_callback(bot_token, chat_id, timeout) -> Callable` — creates callback that:
  1. Formats question with numbered choices
  2. Sends to Telegram via `httpx.post` to Bot API
  3. Registers in `_pending_clarify`
  4. Blocks on `event.wait(timeout)`
  5. Returns answer or timeout message

Sends use Markdown parse_mode. Format:
```
❓ *Agent asks:*

{question}

1. {choice_1}
2. {choice_2}
3. Other (type your answer)

_Reply to this message with your answer._
```

Timeout response: `"[No response received within timeout. Proceeding without clarification.]"`

### 6. API Server Patch (`hermes-agent/gateway/platforms/api_server.py`)

In `_handle_runs()`, after agent creation, wire clarify_callback:

```python
telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
clarify_timeout = int(os.environ.get("TELEGRAM_CLARIFY_TIMEOUT", "600"))

if telegram_token and telegram_chat_id:
    from clarify_bridge import make_clarify_callback
    agent.clarify_callback = make_clarify_callback(
        bot_token=telegram_token,
        chat_id=telegram_chat_id,
        timeout=clarify_timeout,
    )
```

### 7. Telegram Adapter Patch (`hermes-agent/gateway/platforms/telegram.py`)

In `_handle_text_message`, before standard processing:

```python
from clarify_bridge import resolve_clarify_reply

if resolve_clarify_reply(self._bot_token, str(chat_id), text):
    await self.send(chat_id, "✅ Ответ передан агенту.")
    return
```

### 8. Patching (`hermes-gateway/orchestrator/orchestrator.py`)

Add to `_patch_installed_agent()` patch list:
- `gateway/platforms/telegram.py`
- `orchestrator/clarify_bridge.py` (copy to site-packages, new file)

## Scope Note

Messaging settings are **instance-wide** (singleton pattern, same as `general` and `experimental` settings). All agents share the same Telegram bot token and chat ID. This matches the current architecture where `instance_settings` has one row with `singleton_key = 'default'`.

**Future extension:** Per-agent messaging config would require either a separate `agent_messaging` table or an `agents.messaging_overrides` JSON column. This is intentionally out of scope for the initial implementation.

## Backwards Compatibility

- Empty/missing `messaging` config → agents work as before, no Telegram Q&A
- Existing env vars (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) work as fallback for CEO/CTO during migration
- Removing or disabling messaging in UI → orchestrator regenerates config without Telegram on next poll (60s)
- The `_pending_messages` mechanism is untouched — clarify uses a separate `_pending_clarify` dict

## Config Reload Flow

1. Admin enables Telegram Q&A in Paperclip UI (Instance Settings → Messaging)
2. Orchestrator polls DB (every 60s), detects `messaging.telegram.enabled = true`
3. Regenerates `config.yaml` for all agents with Telegram platform
4. Adds `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_CLARIFY_TIMEOUT` to supervisor environment
5. Restarts gateway processes via supervisor

## Files Changed

| Component | File | Change |
|-----------|------|--------|
| DB schema | `packages/db/src/schema/` | Add `messaging` column to `instance_settings` |
| Shared types | `packages/shared/src/types/instance.ts` | `InstanceMessagingSettings` interface |
| Shared validators | `packages/shared/src/validators/instance.ts` | `messagingSettingsSchema` Zod |
| Server service | `server/src/services/instance-settings.ts` | `normalizeMessagingSettings()`, `getMessaging()`, `updateMessaging()` |
| Server routes | `server/src/routes/` | PATCH endpoint for messaging |
| UI | `ui/src/pages/InstanceGeneralSettings.tsx` | Messaging section with form |
| Orchestrator | `hermes-gateway/orchestrator/orchestrator.py` | `_fetch_messaging_config()`, per-agent Telegram from DB |
| Config gen | `hermes-gateway/orchestrator/config_generator.py` | `clarify_timeout` parameter |
| Clarify bridge | `hermes-gateway/orchestrator/clarify_bridge.py` | **New file** — pending registry + callback factory |
| API server | `hermes-agent/gateway/platforms/api_server.py` | Wire `clarify_callback` in `_handle_runs` |
| Telegram | `hermes-agent/gateway/platforms/telegram.py` | Check `_pending_clarify` on incoming messages |

## Not Changed

- `send_message_tool.py` — already works for outbound messages
- `clarify_tool.py` — already supports callback injection
- `config-template.yaml` — only `clarify_timeout` field added under telegram extra
- `_pending_messages` mechanism — untouched
