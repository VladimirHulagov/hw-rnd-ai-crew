# Agent Memory Service — Design Spec

**Date:** 2026-04-17
**Status:** Approved

## Problem

Hermes agent profiles (`~/.hermes/profiles/`) live in the container writable layer — `docker compose down` destroys all MEMORY.md, session history (~50 JSONL files, 14MB+), state.db (19MB SQLite), skills, and cron state. Even with persistence, agents have no way to search their accumulated knowledge across sessions.

## Goals

1. **Persist** Hermes profiles across container recreations via Docker volume
2. **Vectorize** all assistant messages from session JSONL files + MEMORY.md into Qdrant
3. **Expose** a search tool via MCP for agents to query their accumulated memory
4. **Incremental** indexing: only process new/changed session files on each cron tick

## Architecture

All components run inside the existing `hermes-gateway` container. Two new Supervisor processes alongside the orchestrator.

```
┌─ hermes-gateway container ─────────────────────────────┐
│                                                         │
│  [orchestrator]   [session-indexer]   [memory-mcp]      │
│       │                  │                │              │
│       │           cron (10min)     port 8680/MCP        │
│       │                  │                │              │
│       │           reads sessions/   serves tools:       │
│       │           ───────────────   search_memory()     │
│       │           Ollama embed       get_agent_ctx()    │
│       │                  │                │              │
│       └──────────────────┼────────────────┘              │
│                          │                               │
│                    Qdrant:6333                           │
│                    (external)                            │
└─────────────────────────────────────────────────────────┘
```

Agents connect to `memory-mcp` via `mcp_servers` in config-template.yaml — same pattern as paperclip/rag/outline MCPs.

## Component Details

### A. Profile Persistence (prerequisite)

New Docker volume `hermes_profiles` mounted at `/root/.hermes/profiles`.

```yaml
# docker-compose.yml — hermes-gateway service
volumes:
  - hermes_profiles:/root/.hermes/profiles
```

This preserves MEMORY.md, sessions/, state.db, skills/, cron/ across `docker compose down/up`.

### B. session-indexer.py

Supervisor process. Runs a loop with configurable interval (default 10 min).

**Scan phase:**
- Glob `/root/.hermes/profiles/*/sessions/*.jsonl` and `*/memories/MEMORY.md`
- For each profile, resolve agent_name from the profile's `config.yaml` or `SOUL.md`
- Track indexed files in `/root/.hermes/indexer-state.json`: `{filename: {hash: sha256(mtime+size), chunk_count: N}}`
- Skip files whose hash hasn't changed

**Extract phase:**
- Parse JSONL: each line is a conversation message `{role, content, timestamp, ...}`
- Filter `role == "assistant"` messages
- Each assistant message becomes one chunk
- For MEMORY.md: split by `§` delimiter (Hermes memory paragraph separator), each paragraph is a chunk

**Embed phase:**
- Call Ollama `POST http://ollama:11434/api/embed` with model `nomic-embed-text`
- Batch up to 20 chunks per request for efficiency
- Retry on failure with exponential backoff (3 attempts)

**Upsert phase:**
- Qdrant collection `agent_memory` (create if not exists)
- Vector config: 768d, cosine distance
- Upsert points with payload:
  ```json
  {
    "agent_id": "uuid",
    "agent_name": "CEO",
    "session_id": "20260417_140942_73c71600",
    "timestamp": "2026-04-17T14:09:42Z",
    "text": "full message text...",
    "source": "session",
    "tool_calls": ["search_web", "paperclip_create_comment"]
  }
  ```
- Use deterministic point IDs: `hash(agent_id + session_id + chunk_index)` for idempotent upsert

**State file** (`/root/.hermes/profiles/indexer-state.json` — inside the persistent volume):
```json
{
  "last_run": "2026-04-17T14:30:00Z",
  "files": {
    "/root/.hermes/profiles/26fc.../sessions/20260417_140942_73c71600.jsonl": {
      "hash": "abc123",
      "chunks": 45
    }
  }
}
```

### C. memory-mcp-server.py

MCP server on port 8680 (StreamableHTTP transport).

**Tools:**

1. `search_memory(query: str, limit: int = 5) -> str`
   - Embed query via Ollama
   - Search Qdrant `agent_memory` collection (cosine similarity)
   - Return formatted results:
     ```
     Found 5 relevant memories:

     [1] CEO — 2026-04-17 14:09 (session 73c71600)
     Text: "BBU IQC methodology requires electrical tests for OCV, ESR, capacitance..."
     Relevance: 0.87

     [2] CEO — 2026-04-16 22:30 (session 4adff159)
     Text: "Industry standards for server verification include SPEC CPU 2017, TPC..."
     Relevance: 0.82
     ```

2. `get_agent_context(agent_name: str, limit: int = 10) -> str`
   - Filter by `agent_name` payload, sort by timestamp desc
   - Return recent context entries (useful for understanding another agent's work)

**Auth:** `Authorization: Bearer ${MEMORY_API_KEY}` header. Env var set in `.env`, passed to hermes-gateway container.

### D. Hermes Config

Add to `config-template.yaml` under `mcp_servers`:

```yaml
  memory:
    url: http://localhost:8680/mcp
    headers:
      Authorization: "Bearer ${memory_api_key}"
    enabled: true
    timeout: 30
    connect_timeout: 10
```

Same entry in `hermes-shared-config/config.yaml` for the running config.

### E. Supervisor Config

```ini
[program:session-indexer]
command=python -u /opt/orchestrator/session_indexer.py
autostart=true
autorestart=true
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
redirect_stderr=true
priority=5

[program:memory-mcp]
command=python -u /opt/orchestrator/memory_mcp_server.py
autostart=true
autorestart=true
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
redirect_stderr=true
priority=5
```

### F. Dependencies

Add to `hermes-gateway/requirements.txt`:
```
qdrant-client>=1.9.0
```

Ollama embeddings via raw `httpx` (already installed) — no extra package needed.

### G. Docker Compose Changes

```yaml
# hermes-gateway service
environment:
  MEMORY_API_KEY: "${MEMORY_API_KEY:-}"
volumes:
  - hermes_profiles:/root/.hermes/profiles   # NEW: persistence

# New volume definition
volumes:
  hermes_profiles:
```

## Files to Create/Modify

| File | Action |
|------|--------|
| `hermes-gateway/orchestrator/session_indexer.py` | Create — cron indexer |
| `hermes-gateway/orchestrator/memory_mcp_server.py` | Create — MCP server |
| `hermes-gateway/requirements.txt` | Modify — add `qdrant-client` |
| `hermes-gateway/supervisord.conf` | Modify — add 2 programs |
| `hermes-gateway/config-template.yaml` | Modify — add `memory` mcp_server |
| `hermes-shared-config/config.yaml` | Modify — add `memory` mcp_server |
| `docker-compose.yml` | Modify — add volume + env var |

## Error Handling

- Ollama unreachable: log warning, skip this cycle, retry next
- Qdrant unreachable: log warning, skip this cycle
- Corrupt JSONL: log error for specific file, skip it, continue with others
- Large messages (>8000 chars): truncate to 8000 chars before embedding (nomic-embed-text has 8192 token limit)

## First Run

On first deployment, the indexer will process all existing sessions (~50 files, ~14MB). With Ollama embedding at ~100 chunks/request, this should take 2-5 minutes. Subsequent runs are incremental (only new files).
