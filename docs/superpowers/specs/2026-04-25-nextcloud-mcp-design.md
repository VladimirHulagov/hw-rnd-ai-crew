# Nextcloud MCP Server — Design Spec

## Summary

MCP сервер для загрузки, скачивания и просмотра файлов в Nextcloud через WebDAV. Используется Hermes-агентами через Paperclip heartbeat.

## Architecture

```
hermes-agent (gateway process)
  → MCP StreamableHTTP
nextcloud-mcp container (Python, httpx)
  → WebDAV (PROPFIND, GET, PUT, MKCOL)
Nextcloud instance
```

## Container

- Name: `nextcloud-mcp`
- Image: Python 3.11 slim
- Port: 8083
- Transport: StreamableHTTP (`/mcp` endpoint)
- Auth: Bearer token (`NEXTCLOUD_MCP_API_KEY` env var)
- Networks: `nextcloud-rag` (access to Nextcloud), `local-ai-internal` (access from hermes-gateway)

## MCP Tools

### `nextcloud_upload`

Upload a file to Nextcloud via WebDAV PUT.

- **Parameters:**
  - `path` (string, required) — target path in Nextcloud, e.g. `/Documents/report.pdf`
  - `content` (string, required) — file content, base64-encoded
  - `content_type` (string, optional) — MIME type, default `application/octet-stream`
- **Returns:** JSON with `path`, `size` (bytes uploaded), `status`

### `nextcloud_download`

Download a file from Nextcloud via WebDAV GET.

- **Parameters:**
  - `path` (string, required) — file path in Nextcloud
- **Returns:** JSON with `path`, `content` (base64-encoded), `size`, `content_type`

### `nextcloud_list`

List files and folders via WebDAV PROPFIND.

- **Parameters:**
  - `path` (string, optional) — directory path, default `/`
  - `depth` (integer, optional) — 1 (immediate children) or 0 (current only), default 1
- **Returns:** JSON array of `{path, filename, mimetype, size, modified_time}`

### `nextcloud_mkdir`

Create a folder via WebDAV MKCOL.

- **Parameters:**
  - `path` (string, required) — folder path to create, e.g. `/Documents/New Folder`
- **Returns:** JSON with `path`, `status`

## File Structure

```
nextcloud-mcp/
├── Dockerfile
├── pyproject.toml
└── mcp_server/
    ├── __init__.py
    ├── main.py       # StreamableHTTP server, tool registration, dispatch
    ├── auth.py        # Bearer token validation
    └── webdav.py      # WebDAV client (httpx)
```

## Environment Variables

| Variable | Description | Source |
|----------|-------------|--------|
| `NEXTCLOUD_URL` | Nextcloud base URL | `.env` |
| `NEXTCLOUD_USER` | WebDAV username | `.env` |
| `NEXTCLOUD_APP_PASSWORD` | WebDAV app password | `.env` |
| `NEXTCLOUD_MCP_API_KEY` | Bearer token for MCP auth | `.env` |

## Hermes Config Integration

Add to `hermes-gateway/orchestrator/config-template.yaml` and `hermes-shared-config/config.yaml`:

```yaml
mcp_servers:
  nextcloud:
    url: http://nextcloud-mcp:8083/mcp
    headers:
      Authorization: Bearer ${NEXTCLOUD_MCP_API_KEY}
    enabled: true
    timeout: 60
    connect_timeout: 30
```

Hermes-agent prepends `mcp_` prefix to MCP server name and tools. Tools will appear as `mcp_nextcloud_nextcloud_upload`, `mcp_nextcloud_nextcloud_download`, etc.

## Docker Compose Changes

Add service `nextcloud-mcp` to `docker-compose.yml`:

```yaml
nextcloud-mcp:
  build: ./nextcloud-mcp
  container_name: nextcloud-mcp
  restart: unless-stopped
  env_file: .env
  environment:
    NEXTCLOUD_URL: "${NEXTCLOUD_URL:-}"
    NEXTCLOUD_USER: "${NEXTCLOUD_USER:-}"
    NEXTCLOUD_APP_PASSWORD: "${NEXTCLOUD_APP_PASSWORD:-}"
    NEXTCLOUD_MCP_API_KEY: "${NEXTCLOUD_MCP_API_KEY:-}"
  networks:
    - local-ai-internal
    - nextcloud-rag
```

## WebDAV Client Details

Reuses patterns from `rag-worker/rag/nextcloud.py`:

- Base URL: `${NEXTCLOUD_URL}/remote.php/dav/files/${NEXTCLOUD_USER}`
- Auth: HTTP Basic (`NEXTCLOUD_USER`, `NEXTCLOUD_APP_PASSWORD`)
- PUT: upload file content (binary body)
- GET: download file content
- PROPFIND: XML request/response parsing for listing
- MKCOL: create collection (folder)
- Timeout: 120s for uploads/downloads, 30s for list/mkdir
- Error handling: raise on HTTP errors, return structured JSON error for MCP layer
