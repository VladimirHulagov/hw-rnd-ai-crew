# Outline MCP Integration

## Goal

Add Outline knowledge base MCP server to all Hermes agents so they can read and create/update documents in the shared knowledge base at `outline.collaborationism.tech`.

## Context

Hermes agents already connect to two MCP servers: `rag` (RAG search) and `paperclip` (task management). Outline MCP adds a third server for knowledge base interaction — reading existing docs and creating new ones with research findings, decisions, and how-tos.

Outline is an **external service** — no new Docker container needed. Just MCP client configuration.

## Design

### MCP Server Configuration

Add `outline` MCP server to three config locations:

1. **`hermes-gateway/config-template.yaml`** — template for per-agent profiles:
   ```yaml
   outline:
     url: https://outline.collaborationism.tech/mcp
     headers:
       Authorization: "Bearer ${outline_api_key}"
     enabled: true
     timeout: 120
     connect_timeout: 60
   ```

2. **`hermes-shared-config/config.yaml`** — shared config for paperclip-server instances (same structure, using `${MCP_OUTLINE_API_KEY}`).

3. **`config_generator.py`** — add `outline_api_key` to values dict for template substitution.

### Environment Variables

- `MCP_OUTLINE_API_KEY` added to `.env`, `docker-compose.yml` (hermes-gateway + paperclip-server).
- Single shared token: `ol_api_pHi3B7bru9IJWkBIB2bfKggn0drTmKjjcEhK0l`.

### Agent Instructions (SOUL.md)

Update `_build_soul_md()` in `orchestrator.py` to include Outline usage guidance:

- **Worker agents**: create documents for research findings, decisions, and how-tos. Search before creating to avoid duplicates.
- **CEO/CTO agents**: use Outline for knowledge lookup during decision-making.

### AGENTS.md Update

Add Outline MCP section documenting the server, its tools, and usage conventions.

## Files Changed

| File | Change |
|------|--------|
| `hermes-gateway/config-template.yaml` | Add `outline` MCP server block |
| `hermes-shared-config/config.yaml` | Add `outline` MCP server block |
| `hermes-gateway/orchestrator/config_generator.py` | Add `outline_api_key` to values |
| `hermes-gateway/orchestrator/orchestrator.py` | Add Outline instructions to `_build_soul_md()` |
| `docker-compose.yml` | Add `MCP_OUTLINE_API_KEY` env var |
| `.env` | Add `MCP_OUTLINE_API_KEY` value |
| `AGENTS.md` | Add Outline MCP section |

## Deployment

```bash
docker compose up -d --force-recreate --build hermes-gateway
```

No new containers. Existing agents will pick up Outline MCP on next provisioning cycle.
