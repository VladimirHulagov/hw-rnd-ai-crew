# Per-Agent Messaging Config

## Problem

Messaging (Telegram) is stored as a singleton in `instance_settings.messaging`. All agents get the same bot token, causing conflicts — only one agent can poll a Telegram bot at a time. The "clarify" tool (asking humans questions via Telegram) only works for one agent.

## Solution

Move messaging config from `instance_settings.messaging` (global) to `agents.adapter_config.messaging` (per-agent). Each agent gets its own Telegram bot token. The instance-level messaging settings page is replaced by per-agent messaging on the Agent Detail page.

## Design

### Data model

Store messaging inside the existing `adapter_config` jsonb column on `agents`:

```json
{
  "adapter_config": {
    "messaging": {
      "telegram": {
        "enabled": true,
        "botToken": "123456:ABC-...",
        "chatId": "-1001234567890",
        "allowedUsers": "12345,67890",
        "defaultTimeout": 600
      }
    }
  }
}
```

No DB migration needed — `adapter_config` is already jsonb.

### Orchestrator (hermes-gateway)

**`fetch_agents_from_db()`** — add `adapter_config` to SELECT:
```sql
SELECT a.id, a.name, a.role, a.company_id, a.adapter_config
```

**Remove `_fetch_messaging_config()`** — no longer reads `instance_settings`.

**`provision_agent()`** — extract telegram config from `agent["adapter_config"]["messaging"]["telegram"]` instead of `_fetch_messaging_config()`.

**`_agent_data_changed()`** — also compare `adapter_config` hash to detect messaging changes.

### Server API

No changes. `PATCH /api/companies/:companyId/agents/:agentId` already accepts `adapter_config` updates. The `messaging` key passes through as generic JSON.

### UI

**Agent Detail page** — add "Messaging" collapsible section (same form fields as current InstanceMessagingSettings):
- Telegram enabled toggle
- Bot Token (password field)
- Chat ID
- Allowed Users
- Response Timeout

Form saves via existing agent update mutation with `adapter_config.messaging`.

**Instance Messaging Settings** — keep page but show a notice: "Messaging is now configured per-agent. Go to Agent → Settings → Messaging." Alternatively remove the page.

### Files changed

| File | Change |
|------|--------|
| `hermes-gateway/orchestrator/orchestrator.py` | `fetch_agents_from_db()` add adapter_config, remove `_fetch_messaging_config()`, update `provision_agent()` |
| `hermes-gateway/orchestrator/config_generator.py` | No change (already accepts telegram params) |
| `paperclip/ui/src/pages/AgentDetail.tsx` | Add Messaging section |
| `paperclip/ui/src/pages/InstanceMessagingSettings.tsx` | Deprecation notice or remove |
| `AGENTS.md` | Update messaging section |

### Deployment

1. Add messaging to each agent's `adapter_config` via API or DB
2. Deploy hermes-gateway (reads from new location)
3. Deploy paperclip-server (UI changes)
