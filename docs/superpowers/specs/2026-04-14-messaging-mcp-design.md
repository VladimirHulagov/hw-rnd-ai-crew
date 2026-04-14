# Messaging MCP Server Design

## Summary

A new Docker service `messaging-mcp` that provides MCP tools for Hermes agents to communicate with users via messaging platforms (Telegram initially, Mattermost planned). Agents can ask clarifying questions and block until a reply is received, enabling interactive task execution within the Paperclip workflow.

## Motivation

Hermes agents run in `hermes chat -q` mode (single-query, per-task). They cannot receive messages — only execute tools and return results. When an agent needs clarification from a human (e.g., CEO agent asking about budget constraints), it currently has no way to communicate.

The Hermes gateway (which provides full duplex messaging) was evaluated but rejected for this iteration due to the scope of adapter changes required. The messaging-mcp server is a lightweight bridge that works within the existing architecture.

## Architecture

```
Paperclip → adapter → hermes chat -q
                         └── MCP client → messaging-mcp:8083/mcp
                                            ├── ask_user(question, chat_id, timeout)
                                            │     Sends question → waits for reply → returns text
                                            └── notify(message, chat_id)
                                                  Sends message without waiting
```

### Components

1. **messaging-mcp** — Python/uvicorn ASGI app, MCP server with StreamableHTTP + SSE transports
2. **Platform adapters** — Pluggable backends for different messaging platforms
3. **Reply watcher** — Background polling loop that collects incoming messages into per-chat queues

### Message Flow

```
1. Paperclip assigns task to CEO agent
2. Adapter launches: hermes chat -q "<task>"
3. Agent processes task, determines clarification needed
4. Calls MCP tool: ask_user(question="What is the budget for project X?", timeout_sec=86400)
5. messaging-mcp:
   a. Selects platform adapter (Telegram)
   b. Sends question to configured chat via Bot API
   c. Creates asyncio.Future for this chat_id
   d. Awaits Future with timeout
6. User replies in Telegram
7. Telegram polling receives message → resolves Future with reply text
8. messaging-mcp returns reply to Hermes
9. Hermes continues task execution with the answer
10. Task completes, Paperclip receives result
```

## Platform Abstraction

The server uses a platform adapter pattern to support swapping Telegram for Mattermost (or other platforms) without changing the MCP tool interface.

### Adapter Interface

```python
class MessagingAdapter(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_message(self, chat_id: str, text: str) -> str: ...

    @abstractmethod
    async def ask_user(self, chat_id: str, question: str, timeout_sec: int) -> str: ...

    @abstractmethod
    async def notify(self, chat_id: str, message: str) -> None: ...
```

### Telegram Adapter

- Uses `python-telegram-bot` library
- Polling mode (no webhook needed, runs inside Docker network)
- Incoming messages routed to `dict[chat_id, asyncio.Queue]`
- `ask_user`: sends question, awaits queue entry with timeout
- `notify`: fire-and-forget send

### Mattermost Adapter (Future)

- Uses `mattermostdriver` library
- WebSocket for incoming messages
- Same `ask_user`/`notify` interface
- Activated by `MESSAGING_PLATFORM=mattermost` env var

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MESSAGING_PLATFORM` | Yes | Platform to use: `telegram` (default), `mattermost` (future) |
| `MESSAGING_AUTH_TOKEN` | Yes | Bearer token for MCP client auth |
| `TELEGRAM_BOT_TOKEN` | For telegram | Bot token from @BotFather |
| `TELEGRAM_DEFAULT_CHAT_ID` | No | Default chat ID if not specified in tool call |

### Hermes config.yaml

Added to `mcp_servers` section:

```yaml
messaging:
  url: http://messaging-mcp:8083/mcp
  timeout: 60
  connect_timeout: 30
  headers:
    Authorization: Bearer ${MESSAGING_AUTH_TOKEN}
```

### docker-compose.yml

```yaml
messaging-mcp:
  build: ./messaging-mcp
  env_file: .env
  environment:
    - MESSAGING_PLATFORM=telegram
    - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
  networks:
    - internal
  restart: unless-stopped
```

## MCP Tools

### `ask_user`

Ask a question and wait for a human reply. Blocks until reply received or timeout.

**Parameters:**
- `question` (string, required) — Question text to send
- `chat_id` (string, optional) — Target chat ID. Defaults to `TELEGRAM_DEFAULT_CHAT_ID`
- `timeout_sec` (integer, optional) — Max wait time in seconds. Default: 3000. Capped at 3000 in Phase 1 (see Timeout Handling)

**Returns:** Reply text from user

**Errors:**
- Timeout expired → returns error message "No reply received within {timeout_sec} seconds"
- Send failed → returns error with details

### `notify`

Send a notification without waiting for reply.

**Parameters:**
- `message` (string, required) — Message text to send
- `chat_id` (string, optional) — Target chat ID. Defaults to `TELEGRAM_DEFAULT_CHAT_ID`

**Returns:** Confirmation of delivery

## Per-Agent Bot Tokens

Initially, one bot token shared via env var. For multi-agent setups where each agent has its own bot:

- Bot token passed via `X-Messaging-Bot-Token` header in MCP connection
- Per-agent config in Hermes `config.yaml` with different headers
- Server creates a separate Bot instance per token

Phase 1: Single bot (CEO agent). Phase 2: Per-agent tokens via header.

## Reply Routing

When multiple agents share one bot and one chat:

1. Agent A calls `ask_user("Question A")` → sends message, awaits reply
2. Agent B calls `ask_user("Question B")` → sends message, awaits reply
3. User replies to Question A → polling receives message, needs to route to correct waiter

**Routing strategy:** Last-question matching. The server tracks which chat_id has an active `ask_user` call. Replies to that chat go to the most recent waiter. For single-agent Phase 1, this is trivial. For multi-agent, we can use Telegram reply_to_message threading to match questions to answers.

## Timeout Handling

The Hermes adapter kills the `hermes chat` process after `DEFAULT_TIMEOUT_SEC` (3600s). If an MCP tool blocks longer than this, the process is killed mid-wait. Two options:

**Option A (Phase 1): Cap ask_user at 3000s.** The tool rejects timeout_sec > 3000. Simple, safe, works within current adapter limits. Agent gets the timeout error and can decide to retry or fail.

**Option B (Future): Increase adapter timeout per agent.** The adapter reads a per-agent `timeoutSec` from Paperclip config. CEO agent gets 86400 (24h). Other agents keep 3600s. This requires a Paperclip schema change.

**Decision:** Start with Option A (3000s cap). Upgrade to Option B when 24h wait is needed. The `ask_user` tool parameter still accepts any timeout value, but logs a warning and caps at 3000s in Phase 1.

When timeout expires, the MCP tool returns an error string. Hermes agent receives it and can retry, skip, or fail the task.

## Error Handling

- Telegram API errors → logged, returned as tool error
- Connection lost → exponential backoff retry for polling
- Multiple waiters on same chat → reply_to_message threading or FIFO queue
- Bot token invalid → startup validation, fail fast

## File Structure

```
messaging-mcp/
  Dockerfile
  requirements.txt
  mcp_server/
    __init__.py
    main.py          # ASGI app, MCP server setup
    tools.py          # ask_user, notify implementations
    adapters/
      __init__.py
      base.py         # MessagingAdapter ABC
      telegram.py     # TelegramAdapter
      # mattermost.py  # Future
    config.py         # Settings from env vars
```

## Docker Integration

- Built from `messaging-mcp/Dockerfile`
- Runs on internal Docker network (no Traefik labels)
- Shares `internal` network with `paperclip-server` and `paperclip-mcp`
- Health check: HTTP GET `/health`
- Restart policy: `unless-stopped`

## Open Questions

1. **24h timeout vs adapter timeout** — The Hermes adapter timeout is 3600s (1h). A 24h wait requires either increasing the adapter timeout or accepting that tasks waiting > 1h will be killed. Recommend starting with max ask_user timeout of 3000s within current adapter limits.

2. **Multi-agent reply routing** — Phase 1 is single agent, so no routing needed. Phase 2 needs reply_to_message or conversation threading.

3. **Mattermost migration path** — Add `MESSAGING_PLATFORM` env var now, implement adapter interface, add Mattermost adapter when needed. No changes to MCP tools or Hermes config.
