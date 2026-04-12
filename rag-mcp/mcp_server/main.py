import logging
import os

from fastapi import FastAPI
from mcp.server.sse import SseServerTransport
from mcp.server import Server
from starlette.routing import Mount, Route

from .auth import auth_middleware
from .tools import search_library, list_indexed_files, get_file_status

log = logging.getLogger(__name__)

server = Server("rag-mcp")
sse = SseServerTransport("/messages/")


@server.list_tools()
async def list_tools():
    return [
        {
            "name": "search_library",
            "description": "Search the indexed document library using semantic similarity. Returns matching text chunks with source file, page, and relevance score.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"},
                    "top_k": {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                    "filter_filename": {"type": "string", "description": "Filter by filename (optional)", "default": None},
                    "filter_file_type": {"type": "string", "description": "Filter by file type: pdf, md, txt, csv (optional)", "default": None},
                },
                "required": ["query"],
            },
        },
        {
            "name": "list_indexed_files",
            "description": "List all indexed files in the library with their metadata and chunk counts.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "filter_file_type": {"type": "string", "description": "Filter by file type (optional)", "default": None},
                },
            },
        },
        {
            "name": "get_file_status",
            "description": "Get indexing status of a specific file: whether it's indexed, how many chunks, last modification time.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path in Nextcloud (e.g. /Documents/report.pdf)"},
                },
                "required": ["path"],
            },
        },
    ]


def _get_embedder():
    from sentence_transformers import SentenceTransformer
    model_name = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5")
    model = SentenceTransformer(model_name)
    return model


_embedder = None


def _embed_query(text: str):
    global _embedder
    if _embedder is None:
        _embedder = _get_embedder()
    vector = _embedder.encode([text], normalize_embeddings=True)
    return vector[0].tolist()


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "search_library":
        query = arguments["query"]
        top_k = arguments.get("top_k", 5)
        filter_filename = arguments.get("filter_filename")
        filter_file_type = arguments.get("filter_file_type")
        query_vector = _embed_query(query)
        result = search_library(query_vector, top_k, filter_filename, filter_file_type)
        result["query"] = query
        return {"type": "text", "text": str(result)}
    elif name == "list_indexed_files":
        filter_file_type = arguments.get("filter_file_type")
        result = list_indexed_files(filter_file_type)
        return {"type": "text", "text": str(result)}
    elif name == "get_file_status":
        path = arguments["path"]
        result = get_file_status(path)
        return {"type": "text", "text": str(result)}
    else:
        return {"type": "text", "text": f"Unknown tool: {name}"}


async def handle_sse(request):
    async with sse.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(
            streams[0], streams[1], server.create_initialization_options()
        )


app = FastAPI(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
    ],
)

app.middleware("http")(auth_middleware)
