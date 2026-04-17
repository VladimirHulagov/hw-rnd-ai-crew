import asyncio
import logging
import os
from typing import Any

import httpx
from aiohttp import web
from mcp.server import Server
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp import types
from qdrant_client import QdrantClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [memory-mcp] %(levelname)s: %(message)s",
    datefmt="[%H:%M:%S]",
)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("MEMORY_MCP_PORT", "8680"))
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
COLLECTION_NAME = "agent_memory"
API_KEY = os.environ.get("MEMORY_API_KEY", "")

mcp_server = Server("agent-memory")


def _qdrant() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, timeout=30)


def _check_auth(request: web.Request) -> bool:
    if not API_KEY:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == API_KEY
    return False


@mcp_server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="search_memory",
            description="Search agent memory — vectorized session history and MEMORY.md across all agents. "
                        "Returns relevant passages with agent name, timestamp, and similarity score. "
                        "Use this to recall past decisions, research results, or technical details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query — natural language question or topic"},
                    "limit": {"type": "integer", "description": "Max results (default 5, max 20)", "default": 5},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_agent_context",
            description="Get recent memory entries for a specific agent. Returns the latest passages "
                        "from their session history, useful for understanding what another agent has been working on.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "description": "Agent name (e.g. 'CEO', 'Founding Engineer')"},
                    "limit": {"type": "integer", "description": "Max results (default 10, max 50)", "default": 10},
                },
                "required": ["agent_name"],
            },
        ),
    ]


def _embed_sync(query: str) -> list[float]:
    resp = httpx.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": [query]},
        timeout=30.0,
    ).raise_for_status().json()
    return resp["embeddings"][0]


def _format_hit(idx: int, payload: dict, score: float | None = None) -> str:
    ts = payload.get("timestamp", "")
    ts_short = ts[:16] if ts else "unknown date"
    agent = payload.get("agent_name", "unknown")
    session = payload.get("session_id", "?")[:16]
    source = payload.get("source", "session")
    text = payload.get("text", "")
    if len(text) > 500:
        text = text[:500] + "..."
    score_str = f", score {score:.2f}" if score is not None else ""
    return f'[{idx}] {agent} — {ts_short} ({source}, session {session}..{score_str})\n    "{text}"'


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    arguments = arguments or {}
    qd = _qdrant()

    try:
        if name == "search_memory":
            query = arguments.get("query", "")
            limit = min(int(arguments.get("limit", 5)), 20)
            if not query:
                return [types.TextContent(type="text", text="Error: query is required")]
            vector = await asyncio.to_thread(_embed_sync, query)
            results = qd.search(
                collection_name=COLLECTION_NAME,
                query_vector=vector,
                limit=limit,
            )
            if not results:
                return [types.TextContent(type="text", text="No matching memories found.")]
            lines = [f"Found {len(results)} relevant memories:\n"]
            for i, hit in enumerate(results, 1):
                lines.append(_format_hit(i, hit.payload or {}, hit.score))
                lines.append("")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "get_agent_context":
            agent_name = arguments.get("agent_name", "")
            limit = min(int(arguments.get("limit", 10)), 50)
            if not agent_name:
                return [types.TextContent(type="text", text="Error: agent_name is required")]
            results, _ = qd.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter={
                    "must": [{"key": "agent_name", "match": {"value": agent_name}}]
                },
                limit=limit,
                with_payload=True,
            )
            if not results:
                return [types.TextContent(type="text", text=f"No memories found for agent '{agent_name}'")]
            results.sort(key=lambda p: (p.payload or {}).get("timestamp", ""), reverse=True)
            lines = [f"Recent context for {agent_name} ({len(results)} entries):\n"]
            for i, p in enumerate(results, 1):
                lines.append(_format_hit(i, p.payload or {}))
                lines.append("")
            return [types.TextContent(type="text", text="\n".join(lines))]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return [types.TextContent(type="text", text=f"Error: {e}")]


class MCPApp:
    def __init__(self):
        self._transport = None
        self._server_task = None

    async def start(self):
        self._transport = StreamableHTTPServerTransport(mcp_session_id=None)
        self._server_task = asyncio.create_task(self._run_server())

    async def _run_server(self):
        async with self._transport.connect() as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if not _check_auth(request):
            return web.Response(status=401, text="Unauthorized")

        scope = {
            "type": "http",
            "method": request.method,
            "path": request.path,
            "query_string": request.query_string.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in request.headers.items()],
            "server": ("0.0.0.0", PORT),
        }

        body = await request.read()
        body_sent = False

        async def receive():
            nonlocal body_sent
            if body_sent:
                return {"type": "http.disconnect"}
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        response_started = False
        status_code = 200
        headers_list = []

        async def send(message):
            nonlocal response_started, status_code, headers_list
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers_list = [(h[0].decode(), h[1].decode()) for h in message.get("headers", [])]
                response_started = True
            elif message["type"] == "http.response.body":
                pass

        await self._transport.handle_request(scope, receive, send)

        resp_body = b""
        if response_started:
            async def receive2():
                return {"type": "http.disconnect"}
            chunks = []

            async def send2(msg):
                if msg["type"] == "http.response.body":
                    chunks.append(msg.get("body", b""))

            await self._transport.handle_request(scope, receive, send2)
            resp_body = b"".join(chunks) if chunks else b""

        return web.Response(
            status=status_code,
            headers=dict(headers_list),
            body=resp_body,
        )


async def main():
    logger.info("Memory MCP server starting on port %d (qdrant=%s, model=%s)",
                PORT, QDRANT_URL, EMBED_MODEL)

    app = MCPApp()
    await app.start()

    web_app = web.Application()
    web_app.router.add_route("*", "/mcp", app.handle)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Listening on 0.0.0.0:%d", PORT)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
