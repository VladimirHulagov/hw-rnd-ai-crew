# Per-Agent Outline Authentication

## Problem

All Hermes agents use a single shared Outline API key (`ol_api_...`), so all actions in Outline are attributed to the admin user (Vladimir). Agents cannot be distinguished in Outline audit logs, document history, or access control.

## Solution

Automatically create an Outline user and API key for each agent during provisioning. The per-agent key is stored in Paperclip DB and injected into the agent's config.yaml, so each agent authenticates as itself in Outline.

## Architecture

### Components

1. **`outline_user.py`** (new) — Outline user/key management
   - `ensure_outline_user(agent_name, db_conn) -> str | None` — idempotent
   - Returns the API key for the agent

2. **`config_generator.py`** (modify) — accept per-agent Outline key parameter
   - `generate_profile_config()` gets `outline_api_key` parameter
   - Falls back to `MCP_OUTLINE_API_KEY` env var if not provided

3. **`orchestrator.py`** (modify) — call `ensure_outline_user()` during provisioning
   - In `provision_agent()`, before generating config
   - Pass the returned key to `generate_profile_config()`

4. **`docker-compose.yml`** (modify) — connect hermes-gateway to `outline_internal` network

### Data Flow

```
provision_agent(agent)
  ├─ ensure_outline_user(name, db)
  │   ├─ Check adapter_config.outline.apiKey in Paperclip DB
  │   ├─ If not found:
  │   │   ├─ POST /api/users.list → find user by name
  │   │   ├─ If not found → POST /api/users.invite (role=member)
  │   │   ├─ Generate key: ol_api_ + base64(32 bytes)
  │   │   ├─ INSERT into outline.apiKeys (hash=sha256(key), last4, userId)
  │   │   └─ UPDATE agents SET adapter_config...outline.apiKey = key
  │   └─ Return key
  ├─ generate_profile_config(outline_api_key=key or fallback)
  └─ Write config.yaml with per-agent key
```

### Outline User Provisioning

**User creation** via REST API:
- `POST /api/users.invite` with `{invites: [{email, name, role: "member"}]}`
- Email format: `<agent-name>@bots.collaborationism.tech`
- Role: `member` (can read/write documents)

**API key creation** via direct DB INSERT:
- Key format: `ol_api_` + base64url(32 random bytes)
- Insert into `apiKeys` table: `id`, `name` ("hermes"), `hash` (SHA-256 of key), `last4`, `userId`, timestamps
- The actual key is stored only in Paperclip DB, never in Outline DB (same as UI behavior)

### Key Storage

- **Paperclip DB** (`agents.adapter_config`): `{"outline": {"apiKey": "ol_api_..."}}`
- **Agent config.yaml**: `Authorization: Bearer ol_api_...` in outline MCP server block
- **Not stored** in Outline DB (only SHA-256 hash, matching Outline's security model)

### Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `OUTLINE_URL` | Outline instance URL | `https://outline.collaborationism.tech` |
| `OUTLINE_API_KEY` | Admin API key for users.invite | `ol_api_pHi3B7...` |
| `OUTLINE_DB_URL` | Outline PostgreSQL connection | `postgres://outline:...@outline-postgres:5432/outline` |

### Docker Networking

- hermes-gateway joins `outline_internal` external network (already exists)
- This gives access to both `outline` (REST API) and `outline-postgres` (DB INSERT)

### Error Handling

- All Outline operations wrapped in try/except
- If user creation or key generation fails, fall back to shared `MCP_OUTLINE_API_KEY`
- Agent provisioning never blocked by Outline errors
- Logged via `gateway-orchestrator.outline` logger

### What We Do NOT Do

- Modify Outline source code
- Create OIDC clients for agents
- Delete Outline users when agents are removed
- Revoke API keys when agents are paused/terminated
- Add Outline user management UI in Paperclip
