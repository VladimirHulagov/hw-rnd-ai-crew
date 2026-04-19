# Outline RAG Integration

## Goal

Add Outline knowledge base to the RAG pipeline so hermes agents can semantically search Outline documents and receive compact Markdown snippets instead of bloated ProseMirror JSON that causes context overflow.

## Context

Hermes agents connect to Outline via MCP (`outline.collaborationism.tech/mcp`) for reading and writing documents. Reading full documents returns ProseMirror JSON — a verbose rich-text format that overflows agent context windows for documents longer than a few paragraphs.

The existing RAG pipeline (rag-worker + rag-mcp + Qdrant) currently indexes only Nextcloud files. Outline will be added as a second source. Agents will use the new `search_outline` MCP tool for reading/searching and keep `mcp_outline_*` for writing.

## Design

### 1. Outline API Client (`rag/outline.py`)

New module in rag-worker providing Outline REST API access.

```
Env vars:
  OUTLINE_URL=https://outline.collaborationism.tech
  OUTLINE_API_KEY=ol_api_...
```

Functions:

| Function | Endpoint | Returns |
|----------|----------|---------|
| `list_documents(offset, limit)` | `POST /api/documents.list` | `[{id, title, updatedAt, collectionId}]` |
| `get_document_markdown(doc_id)` | `POST /api/documents.info` | `{title, markdown: str, updated_at: int}` |
| `list_collections()` | `POST /api/collections.list` | `[{id, name}]` |

Outline returns document text as Markdown by default via the `text` field in `/api/documents.info`. No format parameter or ProseMirror conversion needed.

### 2. Polling Indexer

Background thread inside rag-worker that periodically syncs Outline documents to Qdrant.

```
Env vars:
  OUTLINE_SYNC_INTERVAL=300  (seconds, default 5 min)
  OUTLINE_CHUNK_SIZE=512     (words per chunk, default same as Nextcloud)
  OUTLINE_CHUNK_OVERLAP=64   (word overlap, default same as Nextcloud)
```

Logic per sync cycle:

1. Call `list_documents()` with pagination to get all document metadata
2. For each document:
   - Query Qdrant for existing point with this `outline_id` and check `updated_at`
   - If `updatedAt` from Outline matches stored `updated_at` → skip
   - Otherwise: download markdown via `get_document_markdown()`, chunk, embed, upsert into `outline_docs` collection
3. Detect deletions: compare all `outline_id` values in Qdrant vs Outline response. Remove orphaned points.

State tracking uses Qdrant payload field `updated_at` — no separate state file needed.

### 3. Qdrant Collection `outline_docs`

```
Collection: outline_docs
Vectors: same embedding model as pdf_library (BAAI/bge-large-en-v1.5, 1024d, cosine)
```

Payload schema per point:

| Field | Type | Description |
|-------|------|-------------|
| `outline_id` | str | Document UUID from Outline |
| `title` | str | Document title |
| `collection_id` | str | Outline collection UUID |
| `chunk_index` | int | Chunk ordinal within document |
| `content` | str | Markdown text chunk |
| `updated_at` | int | Unix timestamp of last document update |
| `source` | str | Always `"outline"` |

Point ID: `md5("{outline_id}:{chunk_index}")` — deterministic, matches existing Nextcloud pattern.

### 4. MCP Tool `search_outline`

New tool added to rag-mcp alongside existing `search_library`.

```python
search_outline(query: str, top_k: int = 5, collection_id: str | None = None)
```

Returns:

```json
{
  "results": [
    {
      "title": "PSU Methodology",
      "outline_id": "uuid",
      "content": "## Overview\n...",
      "score": 0.87,
      "chunk_index": 0
    }
  ],
  "total": 3,
  "query": "PSU testing procedure"
}
```

Implementation:
- Embed query with same model as rag-mcp already uses
- Vector search against `outline_docs` collection
- Optional filter by `collection_id`
- Return chunks with title and content (Markdown)

Also add `list_outline_documents()` tool for browsing indexed Outline docs (similar to existing `list_indexed_files`).

### 5. Agent Instructions Update

**`hermes-gateway/orchestrator/orchestrator.py`** — update `_build_soul_md()`:

Add guidance for both roles:
- Use `search_outline` (rag-mcp) for reading/searching existing Outline documents
- Use `mcp_outline_*` (Outline MCP) only for creating and updating documents
- Never read full Outline documents via `mcp_outline_*` — always use `search_outline` to get relevant fragments

**`AGENTS.md`** — add Outline RAG section documenting the new tool and usage conventions.

## Files Changed

| File | Change |
|------|--------|
| `rag-worker/rag/outline.py` | **New** — Outline API client |
| `rag-worker/rag/main.py` | Add Outline sync background thread + startup |
| `rag-worker/rag/qdrant_client.py` | Add `ensure_outline_collection()`, `upsert_outline_chunks()`, `delete_outline_by_id()`, `get_outline_indexed_state()` |
| `rag-mcp/mcp_server/tools.py` | Add `search_outline()`, `list_outline_documents()` |
| `rag-mcp/mcp_server/main.py` | Register new tools, add `outline_docs` collection access |
| `hermes-gateway/orchestrator/orchestrator.py` | Update `_build_soul_md()` with Outline RAG guidance |
| `AGENTS.md` | Add Outline RAG section |
| `docker-compose.yml` | Add `OUTLINE_URL`, `OUTLINE_API_KEY`, `OUTLINE_SYNC_INTERVAL` env vars to rag-worker and rag-mcp |
| `.env` / `.env.example` | Add Outline env vars |

## Environment Variables

| Variable | Service | Description |
|----------|---------|-------------|
| `OUTLINE_URL` | rag-worker, rag-mcp | Outline base URL |
| `OUTLINE_API_KEY` | rag-worker | Outline API token for reading documents |
| `OUTLINE_SYNC_INTERVAL` | rag-worker | Polling interval in seconds (default 300) |
| `OUTLINE_CHUNK_SIZE` | rag-worker | Chunk size in words (default 512) |
| `OUTLINE_CHUNK_OVERLAP` | rag-worker | Chunk overlap in words (default 64) |

## Deployment

```bash
# Rebuild rag-worker with new Outline sync code
cd /mnt/services/hw-rnd-ai-crew
docker compose up -d --force-recreate --build rag-worker

# Rebuild rag-mcp with new search_outline tool
docker compose up -d --force-recreate --build rag-mcp

# No new containers needed
```

First sync will index all existing Outline documents. Subsequent syncs will be incremental (only changed documents).
