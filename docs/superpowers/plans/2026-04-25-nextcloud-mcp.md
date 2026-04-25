# Nextcloud MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a standalone MCP server container (`nextcloud-mcp`) that exposes WebDAV operations (upload, download, list, mkdir) as MCP tools for Hermes agents.

**Architecture:** Python 3.11 container with `mcp` library + `httpx` for WebDAV. StreamableHTTP transport on port 8083. Registered in hermes config alongside rag, paperclip, outline, memory MCP servers.

**Tech Stack:** Python 3.11, mcp>=1.0.0, httpx>=0.27.0, uvicorn, fastapi

---

### Task 1: WebDAV Client

**Files:**
- Create: `nextcloud-mcp/mcp_server/__init__.py`
- Create: `nextcloud-mcp/mcp_server/webdav.py`

- [ ] **Step 1: Create package init file**

```python
# nextcloud-mcp/mcp_server/__init__.py
```

Create empty `__init__.py`.

- [ ] **Step 2: Create WebDAV client**

Create `nextcloud-mcp/mcp_server/webdav.py`:

```python
import base64
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)


@dataclass
class NextcloudFile:
    path: str
    filename: str
    mimetype: str
    size: int
    modified_time: str


def _base_url() -> str:
    return os.environ.get("NEXTCLOUD_URL", "https://nextcloud.example.com")


def _auth() -> tuple:
    user = os.environ.get("NEXTCLOUD_USER", "")
    password = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
    return (user, password)


def _dav_base() -> str:
    user = os.environ.get("NEXTCLOUD_USER", "")
    return f"{_base_url()}/remote.php/dav/files/{user}"


def upload_file(path: str, content: bytes, content_type: str = "application/octet-stream") -> dict:
    url = _dav_base() + quote(path, safe="/")
    resp = httpx.put(url, auth=_auth(), content=content, headers={"Content-Type": content_type}, timeout=120)
    if resp.status_code not in (200, 201, 204):
        raise Exception(f"Upload failed: {resp.status_code} {resp.text}")
    return {"path": path, "size": len(content), "status": "uploaded"}


def download_file(path: str) -> dict:
    url = _dav_base() + quote(path, safe="/")
    resp = httpx.get(url, auth=_auth(), timeout=120)
    if resp.status_code != 200:
        raise Exception(f"Download failed: {resp.status_code} {resp.text}")
    content_b64 = base64.b64encode(resp.content).decode()
    ct = resp.headers.get("content-type", "application/octet-stream")
    return {"path": path, "content": content_b64, "size": len(resp.content), "content_type": ct}


def list_files(path: str = "/", depth: int = 1) -> List[dict]:
    url = _dav_base() + quote(path, safe="/")
    resp = httpx.request("PROPFIND", url, auth=_auth(), headers={"Depth": str(depth)}, timeout=30)
    if resp.status_code != 207:
        raise Exception(f"List failed: {resp.status_code} {resp.text}")
    return _parse_propfind(resp.text)


def _parse_propfind(xml_text: str) -> List[dict]:
    ns = {"d": "DAV:", "oc": "http://owncloud.org/ns"}
    root = ET.fromstring(xml_text)
    files = []
    for resp in root.findall("d:response", ns):
        href = resp.find("d:href", ns)
        if href is None:
            continue
        href_text = href.text

        props = resp.find(".//d:propstat/d:prop", ns)
        if props is None:
            continue

        getcontenttype = props.find("d:getcontenttype", ns)
        getcontentlength = props.find("d:getcontentlength", ns)
        getlastmodified = props.find("d:getlastmodified", ns)

        mimetype = getcontenttype.text if getcontenttype is not None else ""
        size = int(getcontentlength.text) if getcontentlength is not None else 0
        modified_time = getlastmodified.text if getlastmodified is not None else ""

        filename = href_text.rstrip("/").split("/")[-1]

        path_parts = href_text.split("/files/")
        if len(path_parts) > 1:
            sub = path_parts[-1]
            first_slash = sub.find("/")
            relative_path = sub[first_slash:] if first_slash >= 0 else "/" + sub
        else:
            relative_path = href_text

        if not mimetype and not href_text.endswith("/"):
            continue

        files.append({
            "path": relative_path,
            "filename": filename,
            "mimetype": mimetype,
            "size": size,
            "modified_time": modified_time,
        })
    return files


def mkdir(path: str) -> dict:
    url = _dav_base() + quote(path, safe="/")
    resp = httpx.request("MKCOL", url, auth=_auth(), timeout=30)
    if resp.status_code not in (200, 201, 405):
        raise Exception(f"Mkdir failed: {resp.status_code} {resp.text}")
    status = "created" if resp.status_code in (200, 201) else "already_exists"
    return {"path": path, "status": status}
```

- [ ] **Step 3: Commit**

```bash
git add nextcloud-mcp/mcp_server/__init__.py nextcloud-mcp/mcp_server/webdav.py
git commit -m "feat(nextcloud-mcp): add WebDAV client module"
```

---

### Task 2: MCP Server (main.py + auth)

**Files:**
- Create: `nextcloud-mcp/mcp_server/auth.py`
- Create: `nextcloud-mcp/mcp_server/main.py`

- [ ] **Step 1: Create auth module**

Create `nextcloud-mcp/mcp_server/auth.py` (same pattern as rag-mcp):

```python
import os

_BEARER_PREFIX = "Bearer "


def check_auth(scope) -> bool:
    token = os.environ.get("NEXTCLOUD_MCP_API_KEY", "")
    if not token:
        return True
    headers = {}
    for key, value in scope.get("headers", []):
        headers[key.decode()] = value.decode()
    auth_header = headers.get("authorization", "")
    if not auth_header.startswith(_BEARER_PREFIX):
        return False
    return auth_header[len(_BEARER_PREFIX):] == token
```

- [ ] **Step 2: Create MCP server**

Create `nextcloud-mcp/mcp_server/main.py`:

```python
import asyncio
import base64
import json
import logging

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server import Server
from mcp import types

from .auth import check_auth
from .webdav import upload_file, download_file, list_files, mkdir

log = logging.getLogger(__name__)

server = Server("nextcloud-mcp")
sse = SseServerTransport("/messages/")


@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="nextcloud_upload",
            description="Upload a file to Nextcloud via WebDAV. Content must be base64-encoded.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target path in Nextcloud (e.g. /Documents/report.pdf)"},
                    "content": {"type": "string", "description": "File content, base64-encoded"},
                    "content_type": {"type": "string", "description": "MIME type (default application/octet-stream)", "default": "application/octet-stream"},
                },
                "required": ["path", "content"],
            },
        ),
        types.Tool(
            name="nextcloud_download",
            description="Download a file from Nextcloud via WebDAV. Returns base64-encoded content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path in Nextcloud (e.g. /Documents/report.pdf)"},
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="nextcloud_list",
            description="List files and folders in a Nextcloud directory via WebDAV PROPFIND.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default /)", "default": "/"},
                    "depth": {"type": "integer", "description": "Depth: 1 for immediate children (default)", "default": 1},
                },
            },
        ),
        types.Tool(
            name="nextcloud_mkdir",
            description="Create a folder in Nextcloud via WebDAV MKCOL.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder path to create (e.g. /Documents/New Folder)"},
                },
                "required": ["path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "nextcloud_upload":
            raw = base64.b64decode(arguments["content"])
            result = upload_file(
                path=arguments["path"],
                content=raw,
                content_type=arguments.get("content_type", "application/octet-stream"),
            )
        elif name == "nextcloud_download":
            result = download_file(arguments["path"])
        elif name == "nextcloud_list":
            result = list_files(
                path=arguments.get("path", "/"),
                depth=arguments.get("depth", 1),
            )
        elif name == "nextcloud_mkdir":
            result = mkdir(arguments["path"])
        else:
            result = {"error": f"Unknown tool: {name}"}
        text = json.dumps(result, ensure_ascii=False, default=str)
        return [types.TextContent(type="text", text=text)]
    except Exception as e:
        log.exception("tool %s failed", name)
        return [types.TextContent(type="text", text=f"Error: {e}")]


async def _send_unauthorized(scope, receive, send):
    body = b'{"error":"unauthorized"}'
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [[b"content-type", b"application/json"], [b"content-length", str(len(body)).encode()]],
    })
    await send({"type": "http.response.body", "body": body})


_http_transport = StreamableHTTPServerTransport(
    mcp_session_id=None,
    is_json_response_enabled=True,
)


async def _run_http_server():
    async with _http_transport.connect() as streams:
        await server.run(
            streams[0], streams[1], server.create_initialization_options()
        )


_http_task = None


async def _ensure_http_server():
    global _http_task
    if _http_task is None:
        _http_task = asyncio.ensure_future(_run_http_server())
        await asyncio.sleep(0.1)


async def app(scope, receive, send):
    if scope["type"] != "http":
        return

    path = scope.get("path", "")

    if path == "/sse":
        if not check_auth(scope):
            await _send_unauthorized(scope, receive, send)
            return
        async with sse.connect_sse(scope, receive, send) as streams:
            await server.run(
                streams[0], streams[1], server.create_initialization_options()
            )
    elif path.startswith("/messages/"):
        if not check_auth(scope):
            await _send_unauthorized(scope, receive, send)
            return
        await sse.handle_post_message(scope, receive, send)
    elif path == "/mcp":
        if not check_auth(scope):
            await _send_unauthorized(scope, receive, send)
            return
        await _ensure_http_server()
        await _http_transport.handle_request(scope, receive, send)
    else:
        body = b"Not Found"
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [[b"content-type", b"text/plain"], [b"content-length", str(len(body)).encode()]],
        })
        await send({"type": "http.response.body", "body": body})
```

- [ ] **Step 3: Commit**

```bash
git add nextcloud-mcp/mcp_server/auth.py nextcloud-mcp/mcp_server/main.py
git commit -m "feat(nextcloud-mcp): add MCP server with StreamableHTTP transport"
```

---

### Task 3: Container Files (Dockerfile, requirements.txt)

**Files:**
- Create: `nextcloud-mcp/Dockerfile`
- Create: `nextcloud-mcp/requirements.txt`

- [ ] **Step 1: Create requirements.txt**

Create `nextcloud-mcp/requirements.txt`:

```
mcp>=1.0.0
httpx>=0.27.0
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sse-starlette>=2.0.0
```

- [ ] **Step 2: Create Dockerfile**

Create `nextcloud-mcp/Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "mcp_server.main:app", "--host", "0.0.0.0", "--port", "8083"]
```

- [ ] **Step 3: Commit**

```bash
git add nextcloud-mcp/Dockerfile nextcloud-mcp/requirements.txt
git commit -m "feat(nextcloud-mcp): add Dockerfile and requirements"
```

---

### Task 4: Docker Compose Integration

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add nextcloud-mcp service to docker-compose.yml**

Insert after the `paperclip-mcp` service block (after line 71):

```yaml
  nextcloud-mcp:
    build: ./nextcloud-mcp
    container_name: nextcloud-mcp
    restart: unless-stopped
    environment:
      NEXTCLOUD_URL: "${NEXTCLOUD_URL:-}"
      NEXTCLOUD_USER: "${NEXTCLOUD_USER:-}"
      NEXTCLOUD_APP_PASSWORD: "${NEXTCLOUD_APP_PASSWORD:-}"
      NEXTCLOUD_MCP_API_KEY: "${NEXTCLOUD_MCP_API_KEY:-}"
    networks:
      - local-ai-internal
      - nextcloud-rag
```

- [ ] **Step 2: Add NEXTCLOUD_MCP_API_KEY to .env.example**

Append after the `NEXTCLOUD_APP_PASSWORD` line:

```
NEXTCLOUD_MCP_API_KEY=generate_with_python3_-c_"import_secrets;_print(secrets.token_urlsafe(32))"
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: add nextcloud-mcp service to docker-compose"
```

---

### Task 5: Hermes Config Integration

**Files:**
- Modify: `hermes-gateway/config-template.yaml`
- Modify: `hermes-shared-config/config.yaml`
- Modify: `hermes-gateway/orchestrator/config_generator.py`

- [ ] **Step 1: Add nextcloud MCP server to config-template.yaml**

After the `memory:` mcp_server block (after line 77), add:

```yaml
  nextcloud:
    url: http://nextcloud-mcp:8083/mcp
    headers:
      Authorization: "Bearer ${nextcloud_mcp_api_key}"
    enabled: true
    timeout: 60
    connect_timeout: 30
```

- [ ] **Step 2: Bump _config_version in config-template.yaml**

Change `_config_version: 8` to `_config_version: 9`.

- [ ] **Step 3: Add nextcloud_mcp_api_key to config_generator.py values dict**

In `config_generator.py`, add to the `values` dict (after `memory_api_key`):

```python
"nextcloud_mcp_api_key": os.environ.get("NEXTCLOUD_MCP_API_KEY", ""),
```

- [ ] **Step 4: Add nextcloud MCP server to hermes-shared-config/config.yaml**

After the `memory:` mcp_server block (after line 69), add:

```yaml
  nextcloud:
    url: http://nextcloud-mcp:8083/mcp
    headers:
      Authorization: Bearer ${NEXTCLOUD_MCP_API_KEY}
    enabled: true
    timeout: 60
    connect_timeout: 30
```

Bump `_config_version: 7` to `_config_version: 8`.

- [ ] **Step 5: Commit**

```bash
git add hermes-gateway/config-template.yaml hermes-shared-config/config.yaml hermes-gateway/orchestrator/config_generator.py
git commit -m "feat: register nextcloud MCP server in hermes config"
```

---

### Task 6: Build and Verify

**No files to create.**

- [ ] **Step 1: Build the container**

```bash
docker build -t nextcloud-mcp:latest ./nextcloud-mcp
```

Expected: successful build

- [ ] **Step 2: Start the container**

```bash
docker compose up -d nextcloud-mcp
```

- [ ] **Step 3: Verify container is running**

```bash
docker ps --filter name=nextcloud-mcp
```

Expected: container status `Up`

- [ ] **Step 4: Test MCP endpoint**

```bash
docker exec nextcloud-mcp python -c "from mcp_server.webdav import list_files; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 5: Test StreamableHTTP endpoint**

```bash
curl -X POST http://localhost:8083/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1.0"}}}'
```

Expected: JSON response with `result.capabilities`

- [ ] **Step 6: Rebuild hermes-gateway with new config**

```bash
docker compose up -d --force-recreate --build hermes-gateway
```

- [ ] **Step 7: Verify hermes-gateway sees nextcloud MCP**

```bash
docker logs hermes-gateway 2>&1 | tail -30
```

Expected: no errors about nextcloud MCP connection
