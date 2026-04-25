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
