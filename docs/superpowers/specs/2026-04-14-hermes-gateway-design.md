# Hermes Gateway-in-a-Box Design

## Summary

Replace the current `hermes chat -q` (single-query per-task) architecture with a persistent Hermes gateway deployment. A single Docker container runs multiple gateway processes (one per Paperclip agent) managed by Supervisor, with a Python orchestrator that dynamically provisions new gateways when agents are hired and deprovisions when removed. This unlocks memory nudges, session persistence, native Telegram integration, and full Hermes capabilities for all agents.

## Motivation

### Problems with current architecture

1. **No learning loop**: `hermes chat -q` runs one turn per task. The automatic memory nudge (background review every 10 turns) never fires. Agents only save to memory if explicitly instructed.
2. **No messaging**: Agents cannot communicate with humans mid-task (Telegram, Mattermost). Each invocation is fire-and-forget.
3. **No session continuity**: Each task spawns a new process. No prompt caching, no multi-turn conversations, no pre-reset memory flush.
4. **Process spawn overhead**: Every Paperclip task creates a new Python process, loads config, initializes MCP connections from scratch.
5. **Fragile output parsing**: Adapter parses `hermes chat -q` stdout with regex to extract response, session ID, and usage.

### What gateway mode provides

| Feature | `hermes chat -q` | `hermes gateway` |
|---------|-------------------|-------------------|
| Memory nudge (auto-review) | Never fires (1 turn) | Active (10-turn interval) |
| Pre-reset memory flush | N/A | Active (session expiry) |
| Session persistence | --resume flag | Automatic via state.db |
| Agent caching | None | Per-session (prompt cache) |
| Telegram/Mattermost | Not available | Native platform adapters |
| Multi-turn conversation | Single query | Full conversation |
| Structured API | Regex on stdout | OpenAI-compatible JSON |
| Streaming | N/A | SSE progressive updates |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  hermes-gateway container (Supervisor as PID 1)         │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  orchestrator.py (supervisor event listener)     │   │
│  │  - Polls Paperclip API every 60s                 │   │
│  │  - Creates/deletes profiles                      │   │
│  │  - Generates supervisord [program:] entries      │   │
│  │  - Manages port allocation (8642+)               │   │
│  │  - Writes port map to /run/gateway-ports.json    │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ gateway:ceo  │  │ gateway:cto  │  │ gateway:eng  │  │
│  │ :8642        │  │ :8643        │  │ :8644        │  │
│  │ profile:     │  │ profile:     │  │ profile:     │  │
│  │  ceo-agent/  │  │  cto-agent/  │  │  eng-123/    │  │
│  │  ├ config    │  │  ├ config    │  │  ├ config    │  │
│  │  ├ memories  │  │  ├ memories  │  │  ├ memories  │  │
│  │  ├ skills    │  │  ├ skills    │  │  ├ skills    │  │
│  │  └ sessions  │  │  └ sessions  │  │  └ sessions  │  │
│  │              │  │              │  │              │  │
│  │ Platforms:   │  │ Platforms:   │  │ Platforms:   │  │
│  │  api_server  │  │  api_server  │  │  api_server  │  │
│  │  telegram    │  │              │  │              │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                         │
│  Shared volumes:                                        │
│  - /opt/hermes-agent (read-only, git submodule)         │
│  - /opt/hermes-venv (pip install cache)                 │
│  - /paperclip/hermes-instances (profiles, read-write)   │
└─────────────────────────────────────────────────────────┘
         │                    │
         │ HTTP               │ Telegram polling
         │                    │
    Paperclip server     Telegram Bot API
    (adapter calls       (per-agent bots,
     /v1/chat/completions) configurable)
```

## Component Design

### 1. Hermes Gateway Container

**Dockerfile**: Based on the existing paperclip-server Python base image (or a dedicated hermes image).

**Entrypoint**: `/usr/bin/supervisord -c /etc/supervisor/supervisord.conf`

**supervisord.conf** (initial):
```ini
[supervisord]
nodaemon=true
logfile=/dev/null

[supervisorctl]

[inet_http_server]
port=127.0.0.1:9001

[program:orchestrator]
command=python /opt/orchestrator/orchestrator.py
autostart=true
autorestart=true
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
redirect_stderr=true
```

Gateway processes are added dynamically by the orchestrator via supervisor XML-RPC API.

### 2. Orchestrator

**File**: `/opt/orchestrator/orchestrator.py`

**Responsibilities**:
1. Poll Paperclip API (`GET /api/agents`) every 60 seconds
2. Diff current agents vs running gateway processes
3. For new agents:
   - Create profile directory: `/paperclip/hermes-instances/<agentId>/`
   - Generate `config.yaml` from template (shared config + agent-specific overrides)
   - Allocate next available port (starting from 8642)
   - Generate supervisord `[program:gateway-<agentId>]` config
   - Add process via supervisor XML-RPC (`supervisor.addProcessGroup`)
   - Write port mapping to `/run/gateway-ports.json`
4. For removed agents:
   - Stop process via supervisor XML-RPC
   - Remove profile directory (optional, keep for data)
   - Deallocate port
   - Update port mapping
5. On startup: reconcile all existing agents with running processes

**Port mapping file** (`/run/gateway-ports.json`):
```json
{
  "52f38439-844b-4445-a139-e6c1adb06d46": 8642,
  "b01e019c-a19a-4a78-b5b8-7def363852d4": 8643,
  "c7826470-3b08-49ad-b1d9-e73911ed64f9": 8644
}
```

This file is read by the Paperclip adapter to route tasks to the correct gateway.

### 3. Per-Agent Profile

Each agent gets a Hermes profile at `/paperclip/hermes-instances/<agentId>/`:

```
<agentId>/
  config.yaml       # Generated from template
  .env              # API keys (symlink or copy from shared)
  memories/
    MEMORY.md       # Agent's personal notes
    USER.md         # User profile
  skills/
    *.md            # Paperclip-synced skills
  sessions/
    sessions.json
    *.jsonl
```

**config.yaml** is generated from a template with agent-specific overrides:

```yaml
# Shared settings (from hermes-shared-config/config.yaml)
model:
  default: glm-5.1
provider: zai
# ... terminal, compression, vision, etc. (same as current shared config)

# Agent-specific overrides
agent:
  system_prompt: "<generated from agent name/role>"

# API Server for Paperclip task submission
platforms:
  api_server:
    enabled: true
    extra:
      key: "${API_SERVER_KEY}"
      host: "0.0.0.0"
      port: <allocated_port>

# Telegram (only if bot token configured for this agent)
# platforms:
#   telegram:
#     enabled: true
#     token: "<agent-specific-bot-token>"

# MCP servers (same as current)
mcp_servers:
  rag:
    url: https://rag.collaborationism.tech/mcp
    # ... same config
  paperclip:
    url: http://paperclip-mcp:8082/mcp
    # ... same config

# Memory (full learning loop)
memory:
  memory_enabled: true
  user_profile_enabled: true
  nudge_interval: 10

# Approvals (non-interactive for Paperclip agents)
approvals:
  mode: off
tirith_enabled: false
```

### 4. Adapter Rewrite

**Current** (`hermes-paperclip-adapter/src/server/execute.ts`):
```typescript
// Spawns child process
const child = spawn("hermes", ["chat", "-q", prompt, "-Q", ...]);
// Parse stdout with regex
```

**New** (HTTP to gateway API_SERVER):
```typescript
async function execute(ctx: AdapterContext): Promise<AdapterResult> {
  const port = await lookupGatewayPort(ctx.agentId);
  const sessionId = ctx.sessionParams?.sessionId;

  const response = await fetch(
    `http://localhost:${port}/v1/chat/completions`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${API_SERVER_KEY}`,
        "X-Hermes-Session-Id": sessionId || `paperclip-${ctx.agentId}`,
      },
      body: JSON.stringify({
        model: "hermes-agent",
        stream: false,
        messages: [
          { role: "system", content: buildPrompt(ctx) },
          { role: "user", content: "Work on the assigned task" },
        ],
      }),
      signal: AbortSignal.timeout(ctx.config.timeoutSec * 1000),
    }
  );

  const data = await response.json();
  return {
    exitCode: 0,
    summary: data.choices[0].message.content,
    sessionParams: {
      sessionId: response.headers.get("X-Hermes-Session-Id"),
    },
    usage: {
      inputTokens: data.usage.prompt_tokens,
      outputTokens: data.usage.completion_tokens,
    },
  };
}
```

**Port lookup**: Read `/run/gateway-ports.json` (bind-mounted from gateway container) or call a lightweight HTTP API on the orchestrator.

### 5. Telegram Integration

Telegram is configured per-agent in the profile's `config.yaml`. The orchestrator enables it only for agents that have a bot token configured.

**Bot token storage**: Two options:
- **Phase 1**: Single bot token in root `.env` as `TELEGRAM_BOT_TOKEN`, configured for CEO agent only
- **Phase 2**: Per-agent tokens stored in Paperclip DB (new `telegram_bot_token` field on agents table) or in a separate config

**Telegram configuration in profile config.yaml**:
```yaml
platforms:
  telegram:
    enabled: true
    token: "8674012815:AAFeQs2ZjNC5l7kmNz92vH7MSX6tK_SHBlE"
    extra:
      home_channel: "<chat_id>"
      allowed_users: "<comma-separated-user-ids>"
```

**How agents ask questions via Telegram**: Hermes has a built-in `send_message` tool with Telegram support. When the agent determines it needs clarification, it calls `send_message(target="telegram:<chat_id>", message="Question...")`. The Telegram adapter receives the reply and routes it back to the agent's session via the `_pending_messages` interrupt mechanism. No custom MCP server needed.

### 6. Platform Abstraction (Mattermost Migration)

Hermes already has built-in Mattermost support (`gateway/platforms/mattermost.py`). Migration path:

1. Add `mattermost` platform to profile config.yaml
2. Remove or disable `telegram` platform
3. Same `send_message` tool works — agent code doesn't change

No adapter or orchestrator changes needed.

## Docker Compose Changes

```yaml
services:
  # NEW: replaces direct hermes invocation in paperclip-server
  hermes-gateway:
    build:
      context: .
      dockerfile: hermes-gateway/Dockerfile
    env_file: .env
    environment:
      - PAPERCLIP_API_URL=http://paperclip-server:3100/api
      - API_SERVER_KEY=${HERMES_API_SERVER_KEY}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
    volumes:
      - hermes_instances:/paperclip/hermes-instances
      - hermes_venv:/opt/hermes-venv
      - hermes_src:/opt/hermes-agent-build
      - ./hermes-agent:/opt/hermes-agent:ro
      - ./hermes-shared-config:/opt/hermes-shared-config:ro
      - gateway_ports:/run/gateway-ports
    networks:
      - internal
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 4G

  # MODIFIED: adapter now calls gateway HTTP API instead of spawning hermes
  paperclip-server:
    # ... existing config ...
    volumes:
      # ... existing volumes ...
      - gateway_ports:/run/gateway-ports:ro  # Read port mapping

volumes:
  gateway_ports:  # Shared port mapping file
```

## Adapter Migration Path

The adapter (`hermes-paperclip-adapter`) needs modification but the Paperclip server itself doesn't change. Paperclip still calls `adapter.execute(ctx)` — only the adapter internals change.

### What stays the same
- Adapter interface: `execute()`, `listSkills()`, `syncSkills()`
- Paperclip context: `ctx.agentId`, `ctx.authToken`, `ctx.context`, `ctx.config`
- MCP tools (paperclip, rag) — still configured in Hermes config.yaml

### What changes
- `execute()`: HTTP POST instead of spawn
- `listSkills()`: Read from profile directory instead of subprocess
- `syncSkills()`: Write to profile directory instead of subprocess
- `detectModel()`: Removed — model is in profile config.yaml
- No more `DEFAULT_TIMEOUT_SEC` in adapter — HTTP client timeout

### What's removed
- `hermes chat -q` subprocess spawning
- Stdout regex parsing
- `--resume` flag handling (session continuity via API)
- Model/provider extraction (configured in profile)

## Sequence Diagrams

### Agent Hiring (New Gateway Provisioning)

```
Paperclip UI → POST /api/agents → DB: agent created
    ↓
Orchestrator (60s poll) detects new agent
    ↓
1. mkdir /paperclip/hermes-instances/<agentId>/{memories,skills,sessions}
2. Generate config.yaml from template
3. Allocate port 8642+N
4. supervisor.addProcessGroup("gateway-<agentId>")
5. Write to /run/gateway-ports.json
    ↓
Gateway process starts: hermes gateway -p <agentId>
    ↓
API_SERVER listening on allocated port
```

### Task Execution

```
Paperclip heartbeat → adapter.execute(ctx)
    ↓
1. Read /run/gateway-ports.json → get port for agentId
2. POST http://localhost:<port>/v1/chat/completions
   - system message: agent role + task context
   - X-Hermes-Session-Id: paperclip-<agentId> (or resume ID)
    ↓
GatewayRunner._handle_message_with_agent()
    ↓
AIAgent.run_conversation() (multi-turn, with tools)
    ↓
Agent calls: send_message("telegram:<chat_id>", "Question?")
    ↓ (waits for reply via Telegram polling)
User replies in Telegram
    ↓
Agent continues with answer
    ↓
HTTP response: JSON with result + usage
    ↓
Adapter returns result to Paperclip
```

### Memory Nudge (Automatic Learning)

```
Agent processes 10th message without saving to memory
    ↓
Memory nudge fires (background, after response delivered)
    ↓
Lightweight agent fork reviews conversation
    ↓
Decides to save: "User prefers brief reports" → MEMORY.md
Decides to save: "RAG search pattern for legal docs" → skills/
    ↓
💾 Memory updated (visible to user via gateway notification)
```

## Configuration

### New Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HERMES_API_SERVER_KEY` | Yes | Shared bearer token for gateway API_SERVER auth |
| `TELEGRAM_BOT_TOKEN` | No | Default Telegram bot token (for CEO agent) |
| `TELEGRAM_CHAT_ID` | No | Default Telegram chat ID |
| `PAPERCLIP_API_URL` | Yes | Paperclip API URL for orchestrator polling |

### New Files

| File | Location | Purpose |
|------|----------|---------|
| `hermes-gateway/Dockerfile` | Project root | Gateway container image |
| `hermes-gateway/supervisord.conf` | Project root | Supervisor base config |
| `hermes-gateway/orchestrator.py` | Project root | Dynamic gateway provisioning |
| `hermes-gateway/config-template.yaml` | Project root | Template for per-agent configs |

## File Structure

```
hermes-gateway/
  Dockerfile
  supervisord.conf
  orchestrator/
    __init__.py
    orchestrator.py     # Main orchestrator loop
    config_generator.py # Per-agent config.yaml generation
    port_manager.py     # Port allocation/deallocation
    supervisor_client.py # XML-RPC wrapper for supervisor
  config-template.yaml  # Base config with placeholders
  requirements.txt
```

## Risks and Mitigations

### Single point of failure
**Risk**: One container hosts all gateways. Crash = all agents down.
**Mitigation**: Supervisor auto-restarts crashed processes. Docker `restart: unless-stopped`. Profiles on persistent volumes.

### Resource limits
**Risk**: 32 gateway processes in one container = high memory/CPU.
**Mitigation**: `deploy.resources.limits.memory: 4G`. Hermes gateways are mostly idle (polling). Active processing only during task execution. Monitor and adjust.

### Port exhaustion
**Risk**: Too many agents → no ports available.
**Mitigation**: 32 agents × 1 port each = ports 8642-8673. Well within ephemeral range. Track allocations in file.

### Orchestrator lag
**Risk**: New agent created → up to 60s before gateway starts.
**Mitigation**: Acceptable for hiring flow. Could reduce poll interval or use PostgreSQL LISTEN/NOTIFY for instant provisioning.

### Telegram bot token conflict
**Risk**: Two gateway processes using same bot token → polling conflict.
**Mitigation**: Hermes already handles this with `acquire_scoped_lock()` (file-based lock). Second process will fail to start polling. Orchestrator should validate token uniqueness.

## Migration Steps

1. **Build hermes-gateway image** (Dockerfile + Supervisor + orchestrator)
2. **Write orchestrator** (polling, profile generation, supervisor management)
3. **Update docker-compose.yml** (new service, shared volumes)
4. **Rewrite adapter execute()** (HTTP instead of spawn)
5. **Configure Telegram** (bot token in .env, profile template)
6. **Test with CEO agent** (single agent, verify task execution + Telegram)
7. **Enable for all agents** (orchestrator auto-provisions)
8. **Decommission chat -q path** (remove old spawn code)

## Out of Scope

- Gateway high availability / clustering
- Per-agent resource limits (CPU/memory per process)
- Webhook mode for Telegram (polling is sufficient inside Docker)
- External memory providers (Honcho, etc.)
- Voice/STT/TTS (available but not configured initially)
