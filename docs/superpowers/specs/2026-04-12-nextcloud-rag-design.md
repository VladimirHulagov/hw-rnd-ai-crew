# RAG for Nextcloud PDF Library — Design Spec

**Date**: 2026-04-12
**Status**: Draft
**Project**: `hw-rnd-ai-crew` (`/mnt/services/hw-rnd-ai-crew/`)

---

## Goal

Build a RAG pipeline that indexes documents from a Nextcloud instance into a vector database (Qdrant), keeps the index in sync via webhooks, and exposes a semantic search tool to Hermes agents running on remote machines via an MCP server.

**Library**: ~800 GB, ~1000 files, primarily English.
**First version scope**: PDF + plain text/Markdown. Extensible parser architecture for future types.

---

## Architecture

```
┌─ Server (hw-rnd-ai-crew) ──────────────────────────────────────┐
│                                                                 │
│  Nextcloud ──webhook──▶ rag-worker ──parse/chunk/embed──▶ Qdrant│
│  (external project)     (:8080)                          (6333) │
│                                                               │
│  rag-mcp (:8081) ──────▶ Qdrant (6333)                        │
│     │                                                          │
│     ▼                                                          │
│  Traefik (TLS)                                                 │
│  rag.example.com                                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
        ▲
        │ HTTPS + Bearer token
        │
  Hermes-agent (remote machine)
```

**Networks:**
- `local-ai-internal` — rag-worker, rag-mcp, qdrant, ollama (internal communication)
- `nextcloud-rag` — shared external network between Nextcloud and rag-worker for webhook delivery
- `traefik` (traefik-public) — rag-mcp (public MCP access via TLS) + rag-worker (WebDAV downloads from Nextcloud via `https://nextcloud.example.com`)

---

## Services

### rag-worker

**Purpose**: Accept webhooks from Nextcloud, parse documents, chunk, embed, store in Qdrant.

**Stack**: Python 3.11, FastAPI, opendataloader-pdf, sentence-transformers (BGE-large-en-v1.5), qdrant-client.

**Image**: `python:3.11-slim` + `default-jre-headless` (for opendataloader-pdf, single stage build).

**Environment**:
```
NEXTCLOUD_URL=https://nextcloud.example.com
NEXTCLOUD_USER=<username>
NEXTCLOUD_APP_PASSWORD=<app_password>
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=pdf_library
EMBEDDING_MODEL=BAAI/bge-large-en-v1.5
CHUNK_SIZE=512
CHUNK_OVERLAP=64
```

**Endpoints**:
- `POST /webhook/nextcloud` — accepts Nextcloud Flow App webhooks (file created/updated/deleted). Determines file type, invokes parser, chunks, embeds, upserts to Qdrant. On delete: removes all vectors matching the file path.
- `POST /reindex/{file_path}` — manual reindex of a specific file.
- `GET /status` — indexed file count, chunk count, last indexing timestamp.

**CLI**:
```
docker exec rag-worker python -m rag.index_all
```
Scans all files in Nextcloud via WebDAV PROPFIND, compares mtime with Qdrant payload, indexes only new/changed files. Used for initial bulk indexing of 1000 files.

**Networks**: `local-ai-internal`, `nextcloud-rag`, `traefik`.

**Parser architecture**: Registry pattern mapping file extension to handler. All parsers return `ParsedDocument(pages: List[str], metadata: dict)`.

Supported types (v1):
- `.pdf` → opendataloader-pdf (fast mode, hybrid for complex docs)
- `.txt`, `.md`, `.rst`, `.csv` → direct read, single "page"

Unsupported extensions return 200 OK with a skip log entry. Adding a new type = create a file in `parsers/`, inherit `ParserBase`.

**Error handling**:
- Parse failure: 3 retries with exponential backoff, then dead-letter log entry.
- Qdrant unavailable: return 500, rely on Nextcloud Flow App built-in retry.
- No auth on webhook endpoint (internal network, access only from Nextcloud).

---

### rag-mcp

**Purpose**: Expose semantic search as MCP tools for remote Hermes agents.

**Stack**: Python 3.11, FastAPI, qdrant-client, MCP SDK (SSE transport).

**Image**: `python:3.11-slim` + pip deps only. Lightweight — no Java, no ML models, no opendataloader-pdf.

**Environment**:
```
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=pdf_library
MCP_BEARER_TOKEN=<generated-secret>
```

**Endpoint**: `GET /sse` — SSE transport for MCP protocol.

**Tools**:

| Tool | Parameters | Description |
|---|---|---|
| `search_library` | `query: str`, `top_k: int = 5`, `filter_filename: str = None`, `filter_file_type: str = None` | Semantic search. Returns chunks with content, filename, path, page, score. |
| `list_indexed_files` | `filter_file_type: str = None` | List indexed files with metadata (chunk count, last indexed). |
| `get_file_status` | `path: str` | Status of a specific file: indexed, chunk count, mtime. |

**`search_library` response shape**:
```json
{
  "results": [
    {
      "content": "...chunk text...",
      "score": 0.87,
      "filename": "report-2025.pdf",
      "path": "/Documents/research/report-2025.pdf",
      "page": 12,
      "chunk_index": 2
    }
  ],
  "total": 42,
  "query": "..."
}
```

**Auth**: Bearer token. `Authorization: Bearer <token>` header on every request. Middleware validates, returns 401 on mismatch. Token stored in `.env` as `MCP_BEARER_TOKEN`.

**Traefik labels**:
```yaml
traefik.enable: true
traefik.http.routers.rag-mcp.rule: Host(`rag.${SERVER_DOMAIN}`)
traefik.http.routers.rag-mcp.entrypoints: websecure
traefik.http.routers.rag-mcp.tls: true
traefik.http.services.rag-mcp.loadbalancer.server.port: 8081
```

**Networks**: `local-ai-internal`, `traefik`.

---

### Existing services (no changes)

- **Qdrant** — already running in hw-rnd-ai-crew, empty collections.
- **Ollama** — already running, `nomic-embed-text` available (not used for RAG, reserved for future tasks).

---

## Qdrant Collection Schema

```
Collection: pdf_library
  vectors: 1024 dim (BGE-large-en-v1.5), cosine similarity
  payload:
    filename: str          # "report-2025.pdf"
    path: str              # "/Documents/research/report-2025.pdf"
    page: int              # page number (0-based)
    chunk_index: int       # chunk sequence number within page
    modified_time: int     # file mtime as unix timestamp
    file_type: str         # "pdf", "md", "txt", "rst", "csv"
    content: str           # chunk text (for context window in search results)
```

---

## Nextcloud Integration

**Prerequisites**: Nextcloud 31 with `workflowengine` and `webhook_listeners` apps (both installed and enabled).

**Network**: Shared external Docker network `nextcloud-rag` connecting Nextcloud and rag-worker. Created once: `docker network create nextcloud-rag`.

**Flow App configuration** (Settings → Workflow engine in Nextcloud UI):

1. **File created/updated**:
   - Condition: File type matches PDF, TXT, MD, RST, CSV
   - Action: HTTP POST to `http://rag-worker:8080/webhook/nextcloud`

2. **File deleted**:
   - Condition: File type matches PDF, TXT, MD, RST, CSV
   - Action: HTTP POST to `http://rag-worker:8080/webhook/nextcloud`

**Expected webhook payload** (Nextcloud Flow App):
```json
{
  "object": {
    "name": "report-2025.pdf",
    "path": "/Documents/research/report-2025.pdf",
    "fileId": 12345,
    "mimetype": "application/pdf",
    "size": 2048000
  },
  "signal": "FileCreated",
  "actor": {
    "id": "admin"
  }
}
```

**Auth**: Worker authenticates to Nextcloud via WebDAV using an app password (not admin credentials). Created in Nextcloud Settings → Security → Devices & sessions → Create new app password.

---

## Project Structure

```
hw-rnd-ai-crew/
├── docker-compose.yml          # qdrant, ollama, rag-worker, rag-mcp
├── .env
├── rag-worker/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI app, webhook endpoints
│   │   ├── index_all.py        # CLI: bulk/re-indexing
│   │   ├── chunker.py          # semantic chunking 512/64
│   │   ├── embedder.py         # BGE-large-en-v1.5, singleton model
│   │   ├── qdrant_client.py    # collection init, upsert, delete, search
│   │   ├── nextcloud.py        # WebDAV client: download, PROPFIND scan
│   │   └── parsers/
│   │       ├── __init__.py
│   │       ├── base.py         # ParsedDocument, ParserBase ABC
│   │       ├── registry.py     # ext → parser
│   │       ├── pdf.py          # opendataloader-pdf
│   │       └── plaintext.py    # .txt, .md, .rst, .csv
│   └── tests/
│       ├── test_chunker.py
│       ├── test_parsers.py
│       └── test_webhook.py
├── rag-mcp/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── mcp_server/
│       ├── __init__.py
│       ├── main.py             # SSE MCP server
│       ├── auth.py             # Bearer token middleware
│       └── tools.py            # search_library, list_indexed_files, get_file_status
├── hermes-agent/               # submodule (existing)
├── hermes-paperclip-adapter/   # submodule (existing)
└── paperclip/                  # submodule (existing)
```

---

## Resource Estimates

**Full indexing (1000 PDF, ~800 GB):**

| Stage | Time | Resources |
|---|---|---|
| Parse PDF (fast mode) | ~3 hours | CPU, ~2 GB RAM |
| Chunk + embed | ~2 hours | CPU, ~1.5 GB RAM |
| Upsert to Qdrant | ~30 min | CPU, ~1 GB RAM |
| **Total** | **~5-6 hours** | |

**Qdrant storage:**
- Vectors: ~800K chunks × 1024 dim × 4 bytes ≈ 3.2 GB
- Payload (content + metadata): ~2-3 GB
- **Total: ~6 GB**

**Runtime (after indexing):**
- rag-worker: ~1.5 GB RAM (embedding model in memory)
- rag-mcp: ~200 MB RAM
- Qdrant: ~2 GB RAM (warm cache)

---

## Out of Scope (v1)

- Image OCR / vision model parsing
- Word (.docx), Excel (.xlsx), PowerPoint (.pptx) parsers
- Incremental page-level updates on file change (full file re-index only)
- Full-text search fallback (Qdrant vector search only)
- User-level access control (single-user assumption)
- Web UI for search / admin
