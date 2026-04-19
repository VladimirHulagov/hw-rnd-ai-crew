# Outline RAG Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Outline document indexing to rag-worker and expose semantic search via rag-mcp so hermes agents can search Outline docs without context overflow from ProseMirror JSON.

**Architecture:** rag-worker gains an Outline API client and a background polling thread that syncs Outline documents → Qdrant collection `outline_docs` as markdown chunks. rag-mcp gets a new `search_outline` tool that queries this collection. Agent instructions are updated to prefer `search_outline` over direct Outline MCP for reading.

**Tech Stack:** Python 3.11, FastAPI, Qdrant, Outline REST API, sentence-transformers, MCP protocol

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `rag-worker/rag/outline.py` | **Create** | Outline REST API client |
| `rag-worker/rag/main.py` | **Modify** | Add Outline sync background thread + `/status/outline` endpoint |
| `rag-worker/rag/qdrant_client.py` | **Modify** | Add outline collection helpers |
| `rag-worker/tests/test_outline.py` | **Create** | Tests for Outline client and sync |
| `rag-mcp/mcp_server/tools.py` | **Modify** | Add `search_outline()`, `list_outline_documents()` |
| `rag-mcp/mcp_server/main.py` | **Modify** | Register new tools |
| `hermes-gateway/orchestrator/orchestrator.py` | **Modify** | Update `_build_soul_md()` |
| `AGENTS.md` | **Modify** | Add Outline RAG section |
| `docker-compose.yml` | **Modify** | Add Outline env vars |
| `.env.example` | **Modify** | Add Outline env vars |

---

### Task 1: Outline API Client

**Files:**
- Create: `rag-worker/rag/outline.py`
- Create: `rag-worker/tests/test_outline.py`

- [ ] **Step 1: Write the failing test for Outline client**

```python
# rag-worker/tests/test_outline.py
import json
import pytest
from unittest.mock import patch, MagicMock

from rag.outline import OutlineClient, list_all_documents, get_document_markdown


class TestOutlineClient:
    def test_list_documents_parses_response(self):
        client = OutlineClient("https://outline.example.com", "ol_api_test123")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": "doc-1",
                    "title": "Test Doc",
                    "updatedAt": "2026-04-19T10:00:00.000Z",
                    "collectionId": "col-1",
                }
            ],
            "pagination": {"offset": 0, "limit": 25, "total": 1},
        }
        with patch("httpx.post", return_value=mock_resp):
            result = client.list_documents()
        assert len(result) == 1
        assert result[0]["id"] == "doc-1"
        assert result[0]["title"] == "Test Doc"
        assert result[0]["collection_id"] == "col-1"
        assert result[0]["updated_at"] > 0

    def test_list_documents_paginates(self):
        client = OutlineClient("https://outline.example.com", "ol_api_test123")
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "data": [{"id": "doc-1", "title": "A", "updatedAt": "2026-01-01T00:00:00.000Z", "collectionId": "col-1"}],
            "pagination": {"offset": 0, "limit": 1, "total": 2},
        }
        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {
            "data": [{"id": "doc-2", "title": "B", "updatedAt": "2026-01-01T00:00:00.000Z", "collectionId": "col-1"}],
            "pagination": {"offset": 1, "limit": 1, "total": 2},
        }
        with patch("httpx.post", side_effect=[page1, page2]):
            result = client.list_documents()
        assert len(result) == 2

    def test_get_document_markdown(self):
        client = OutlineClient("https://outline.example.com", "ol_api_test123")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "id": "doc-1",
                "title": "My Doc",
                "text": "# Hello\n\nWorld",
                "updatedAt": "2026-04-19T10:00:00.000Z",
                "collectionId": "col-1",
            }
        }
        with patch("httpx.post", return_value=mock_resp):
            result = client.get_document_markdown("doc-1")
        assert result["title"] == "My Doc"
        assert result["markdown"] == "# Hello\n\nWorld"
        assert result["updated_at"] > 0

    def test_get_document_markdown_raises_on_error(self):
        client = OutlineClient("https://outline.example.com", "ol_api_test123")
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not found"
        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Outline API error"):
                client.get_document_markdown("missing-doc")

    def test_list_documents_skips_archived(self):
        client = OutlineClient("https://outline.example.com", "ol_api_test123")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"id": "doc-1", "title": "Active", "updatedAt": "2026-01-01T00:00:00.000Z", "collectionId": "col-1"},
                {"id": "doc-2", "title": "Archived", "updatedAt": "2026-01-01T00:00:00.000Z", "collectionId": "col-1", "isDeleted": True},
            ],
            "pagination": {"offset": 0, "limit": 25, "total": 2},
        }
        with patch("httpx.post", return_value=mock_resp):
            result = client.list_documents()
        assert len(result) == 1
        assert result[0]["id"] == "doc-1"

    def test_list_collections(self):
        client = OutlineClient("https://outline.example.com", "ol_api_test123")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"id": "col-1", "name": "Engineering"},
                {"id": "col-2", "name": "Research"},
            ],
            "pagination": {"offset": 0, "limit": 25, "total": 2},
        }
        with patch("httpx.post", return_value=mock_resp):
            result = client.list_collections()
        assert len(result) == 2
        assert result[0]["name"] == "Engineering"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/services/hw-rnd-ai-crew/rag-worker && python -m pytest tests/test_outline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rag.outline'`

- [ ] **Step 3: Write the Outline client implementation**

```python
# rag-worker/rag/outline.py
import logging
import os
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)


class OutlineClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _parse_updated_at(self, value: Optional[str]) -> int:
        if not value:
            return 0
        try:
            dt = parsedate_to_datetime(value)
            return int(dt.timestamp())
        except Exception:
            return 0

    def list_documents(self, limit: int = 25) -> List[Dict[str, Any]]:
        all_docs: List[Dict[str, Any]] = []
        offset = 0
        while True:
            resp = httpx.post(
                f"{self.base_url}/api/documents.list",
                headers=self._headers(),
                json={"offset": offset, "limit": limit},
                timeout=30,
            )
            if resp.status_code != 200:
                log.error("Outline list_documents failed: %d %s", resp.status_code, resp.text[:200])
                break
            body = resp.json()
            data = body.get("data", [])
            for doc in data:
                if doc.get("isDeleted") or doc.get("archivedAt"):
                    continue
                all_docs.append({
                    "id": doc["id"],
                    "title": doc.get("title", ""),
                    "updated_at": self._parse_updated_at(doc.get("updatedAt")),
                    "collection_id": doc.get("collectionId", ""),
                })
            pagination = body.get("pagination", {})
            total = pagination.get("total", 0)
            offset += limit
            if offset >= total or not data:
                break
        return all_docs

    def get_document_markdown(self, doc_id: str) -> Dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/api/documents.info",
            headers=self._headers(),
            json={"id": doc_id},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Outline API error {resp.status_code}: {resp.text[:200]}")
        doc = resp.json()["data"]
        return {
            "id": doc["id"],
            "title": doc.get("title", ""),
            "markdown": doc.get("text", ""),
            "updated_at": self._parse_updated_at(doc.get("updatedAt")),
            "collection_id": doc.get("collectionId", ""),
        }

    def list_collections(self) -> List[Dict[str, Any]]:
        resp = httpx.post(
            f"{self.base_url}/api/collections.list",
            headers=self._headers(),
            json={"limit": 100},
            timeout=30,
        )
        if resp.status_code != 200:
            log.error("Outline list_collections failed: %d", resp.status_code)
            return []
        return [{"id": c["id"], "name": c.get("name", "")} for c in resp.json().get("data", [])]


def get_client() -> OutlineClient:
    return OutlineClient(
        base_url=os.environ.get("OUTLINE_URL", "https://outline.collaborationism.tech"),
        api_key=os.environ.get("OUTLINE_API_KEY", ""),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/services/hw-rnd-ai-crew/rag-worker && python -m pytest tests/test_outline.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /mnt/services/hw-rnd-ai-crew
git add rag-worker/rag/outline.py rag-worker/tests/test_outline.py
git commit -m "feat(rag-worker): add Outline REST API client"
```

---

### Task 2: Qdrant outline collection helpers

**Files:**
- Modify: `rag-worker/rag/qdrant_client.py`

- [ ] **Step 1: Add outline collection constants and helpers to qdrant_client.py**

Add at the end of `rag-worker/rag/qdrant_client.py`:

```python
def _outline_collection_name() -> str:
    return os.environ.get("OUTLINE_QDRANT_COLLECTION", "outline_docs")


def ensure_outline_collection(vector_size: int) -> None:
    client = _get_client()
    name = _outline_collection_name()
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        log.info("Created outline collection %s (dim=%d)", name, vector_size)
    else:
        log.info("Outline collection %s already exists", name)


def upsert_outline_chunks(points: List[Dict[str, Any]]) -> None:
    if not points:
        return
    client = _get_client()
    name = _outline_collection_name()
    qdrant_points = [
        PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
        for p in points
    ]
    client.upsert(collection_name=name, points=qdrant_points)
    log.info("Upserted %d outline points to %s", len(qdrant_points), name)


def delete_outline_by_doc_id(outline_id: str) -> int:
    client = _get_client()
    name = _outline_collection_name()
    client.delete(
        collection_name=name,
        points_selector=FilterSelector(
            filter=Filter(
                must=[FieldCondition(key="outline_id", match=MatchValue(value=outline_id))]
            )
        ),
    )
    log.info("Deleted outline points for outline_id=%s", outline_id)
    return 0


def get_outline_indexed_state() -> Dict[str, int]:
    client = _get_client()
    name = _outline_collection_name()
    if not client.collection_exists(name):
        return {}
    state: Dict[str, int] = {}
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=name,
            limit=100,
            offset=offset,
            with_payload=["outline_id", "updated_at"],
        )
        for r in records:
            p = r.payload or {}
            oid = p.get("outline_id", "")
            stored = p.get("updated_at", 0)
            if oid and (oid not in state or stored > state[oid]):
                state[oid] = stored
        if offset is None:
            break
    return state


def get_outline_all_doc_ids() -> set:
    client = _get_client()
    name = _outline_collection_name()
    if not client.collection_exists(name):
        return set()
    ids: set = set()
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=name,
            limit=100,
            offset=offset,
            with_payload=["outline_id"],
        )
        for r in records:
            p = r.payload or {}
            if p.get("outline_id"):
                ids.add(p["outline_id"])
        if offset is None:
            break
    return ids


def get_outline_status() -> Dict[str, Any]:
    client = _get_client()
    name = _outline_collection_name()
    if not client.collection_exists(name):
        return {"documents": 0, "chunks": 0, "collection_exists": False}
    info = client.get_collection(name)
    state = get_outline_indexed_state()
    return {
        "documents": len(state),
        "chunks": info.points_count,
        "collection_exists": True,
    }
```

- [ ] **Step 2: Verify no syntax errors**

Run: `cd /mnt/services/hw-rnd-ai-crew/rag-worker && python -c "from rag.qdrant_client import ensure_outline_collection, upsert_outline_chunks, delete_outline_by_doc_id, get_outline_indexed_state, get_outline_all_doc_ids, get_outline_status; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /mnt/services/hw-rnd-ai-crew
git add rag-worker/rag/qdrant_client.py
git commit -m "feat(rag-worker): add outline Qdrant collection helpers"
```

---

### Task 3: Outline sync function

**Files:**
- Modify: `rag-worker/rag/main.py`

- [ ] **Step 1: Add the sync_outline function to rag-worker/rag/main.py**

Add these imports at the top of `rag-worker/rag/main.py` (after existing imports):

```python
import threading
import time
```

Add after the existing imports from rag modules:

```python
from rag.outline import get_client as get_outline_client
from rag.qdrant_client import (
    ensure_outline_collection, upsert_outline_chunks,
    delete_outline_by_doc_id, get_outline_indexed_state,
    get_outline_all_doc_ids, get_outline_status,
)
```

Add the sync function after the `index_file` function:

```python
_outline_sync_initialized = False


def _init_outline_collection():
    global _outline_sync_initialized
    if not _outline_sync_initialized:
        dim = embedding_dim()
        ensure_outline_collection(dim)
        _outline_sync_initialized = True


def sync_outline() -> Dict[str, Any]:
    _init_outline_collection()
    client = get_outline_client()
    chunk_size = int(os.environ.get("OUTLINE_CHUNK_SIZE", "512"))
    chunk_overlap = int(os.environ.get("OUTLINE_CHUNK_OVERLAP", "64"))

    indexed_state = get_outline_indexed_state()
    remote_docs = client.list_documents()
    remote_ids = {d["id"] for d in remote_docs}

    indexed_count = 0
    skipped_count = 0

    for doc_meta in remote_docs:
        doc_id = doc_meta["id"]
        remote_updated = doc_meta["updated_at"]
        stored_updated = indexed_state.get(doc_id, 0)

        if stored_updated >= remote_updated and remote_updated > 0:
            skipped_count += 1
            continue

        try:
            doc = client.get_document_markdown(doc_id)
        except Exception as e:
            log.error("Failed to fetch outline doc %s: %s", doc_id, e)
            continue

        markdown = doc["markdown"]
        if not markdown.strip():
            continue

        chunks = chunk_pages(
            [markdown],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        if not chunks:
            continue

        texts = [c.text for c in chunks]
        vectors = embed(texts)

        points = []
        for chunk, vector in zip(chunks, vectors):
            point_id = hashlib.md5(f"{doc_id}:{chunk.chunk_index}".encode()).hexdigest()
            points.append({
                "id": point_id,
                "vector": vector,
                "payload": {
                    "outline_id": doc_id,
                    "title": doc["title"],
                    "collection_id": doc["collection_id"],
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.text,
                    "updated_at": doc["updated_at"],
                    "source": "outline",
                },
            })

        upsert_outline_chunks(points)
        indexed_count += 1
        log.info("Indexed Outline doc '%s': %d chunks", doc["title"], len(points))

    indexed_ids = get_outline_all_doc_ids()
    deleted_ids = indexed_ids - remote_ids
    for did in deleted_ids:
        delete_outline_by_doc_id(did)
        log.info("Deleted stale outline doc: %s", did)

    return {
        "indexed": indexed_count,
        "skipped": skipped_count,
        "deleted": len(deleted_ids),
        "total_remote": len(remote_docs),
    }


def _outline_sync_loop():
    interval = int(os.environ.get("OUTLINE_SYNC_INTERVAL", "300"))
    if interval <= 0:
        log.info("Outline sync disabled (OUTLINE_SYNC_INTERVAL=%d)", interval)
        return
    log.info("Outline sync thread started (interval=%ds)", interval)
    time.sleep(10)
    while True:
        try:
            result = sync_outline()
            log.info("Outline sync complete: %s", result)
        except Exception as e:
            log.error("Outline sync failed: %s", e)
        time.sleep(interval)
```

Add the startup event to launch the background thread. Add after the `status` endpoint:

```python
@app.get("/status/outline")
async def outline_status():
    return get_outline_status()


@app.on_event("startup")
async def _start_outline_sync():
    if os.environ.get("OUTLINE_API_KEY"):
        t = threading.Thread(target=_outline_sync_loop, daemon=True)
        t.start()
    else:
        log.info("OUTLINE_API_KEY not set, skipping Outline sync")
```

- [ ] **Step 2: Verify no syntax errors**

Run: `cd /mnt/services/hw-rnd-ai-crew/rag-worker && python -c "from rag.main import sync_outline; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /mnt/services/hw-rnd-ai-crew
git add rag-worker/rag/main.py
git commit -m "feat(rag-worker): add Outline periodic sync with background thread"
```

---

### Task 4: Add `search_outline` and `list_outline_documents` to rag-mcp

**Files:**
- Modify: `rag-mcp/mcp_server/tools.py`
- Modify: `rag-mcp/mcp_server/main.py`

- [ ] **Step 1: Add outline search functions to tools.py**

Add at the end of `rag-mcp/mcp_server/tools.py`:

```python
def _outline_collection() -> str:
    return os.environ.get("OUTLINE_QDRANT_COLLECTION", "outline_docs")


def search_outline(
    query_vector: List[float],
    top_k: int = 5,
    filter_collection_id: Optional[str] = None,
) -> Dict[str, Any]:
    client = _get_client()
    name = _outline_collection()

    if not client.collection_exists(name):
        return {"results": [], "total": 0, "query_vector_dim": len(query_vector)}

    must = []
    if filter_collection_id:
        must.append(FieldCondition(key="collection_id", match=MatchValue(value=filter_collection_id)))

    search_filter = Filter(must=must) if must else None

    response = client.query_points(
        collection_name=name,
        query=query_vector,
        limit=top_k,
        query_filter=search_filter,
        with_payload=True,
    )

    hits = []
    for r in response.points:
        p = r.payload or {}
        hits.append({
            "title": p.get("title", ""),
            "outline_id": p.get("outline_id", ""),
            "content": p.get("content", ""),
            "score": r.score,
            "chunk_index": p.get("chunk_index", 0),
        })

    return {"results": hits, "total": len(hits), "query_vector_dim": len(query_vector)}


def list_outline_documents(
    filter_collection_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    client = _get_client()
    name = _outline_collection()

    if not client.collection_exists(name):
        return []

    must = []
    if filter_collection_id:
        must.append(FieldCondition(key="collection_id", match=MatchValue(value=filter_collection_id)))

    seen: Dict[str, Dict[str, Any]] = {}
    offset = None
    while True:
        scroll_filter = Filter(must=must) if must else None
        records, offset = client.scroll(
            collection_name=name,
            limit=100,
            offset=offset,
            scroll_filter=scroll_filter,
            with_payload=["outline_id", "title", "collection_id", "updated_at"],
        )
        for r in records:
            p = r.payload or {}
            oid = p.get("outline_id", "")
            if oid not in seen:
                seen[oid] = {
                    "outline_id": oid,
                    "title": p.get("title", ""),
                    "collection_id": p.get("collection_id", ""),
                    "updated_at": p.get("updated_at", 0),
                    "chunk_count": 0,
                }
            seen[oid]["chunk_count"] += 1
        if offset is None:
            break
    return list(seen.values())
```

- [ ] **Step 2: Register new tools in main.py**

In `rag-mcp/mcp_server/main.py`, update the import line:

```python
from .tools import search_library, list_indexed_files, get_file_status, search_outline, list_outline_documents
```

Add two new tool definitions inside `list_tools()` (before the closing `]`):

```python
        types.Tool(
            name="search_outline",
            description="Search Outline knowledge base documents using semantic similarity. Returns matching Markdown text chunks with document title and relevance score. Use this instead of mcp_outline_* for reading documents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"},
                    "top_k": {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                    "filter_collection_id": {"type": "string", "description": "Filter by Outline collection ID (optional)", "default": None},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="list_outline_documents",
            description="List all indexed Outline documents with their titles, collection IDs, and chunk counts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter_collection_id": {"type": "string", "description": "Filter by Outline collection ID (optional)", "default": None},
                },
            },
        ),
```

Add two new handlers inside `call_tool()` (before the `else` clause):

```python
    elif name == "search_outline":
        query = arguments["query"]
        top_k = arguments.get("top_k", 5)
        filter_collection_id = arguments.get("filter_collection_id")
        query_vector = _embed_query(query)
        result = search_outline(query_vector, top_k, filter_collection_id)
        result["query"] = query
        return [types.TextContent(type="text", text=str(result))]
    elif name == "list_outline_documents":
        filter_collection_id = arguments.get("filter_collection_id")
        result = list_outline_documents(filter_collection_id)
        return [types.TextContent(type="text", text=str(result))]
```

- [ ] **Step 3: Verify no syntax errors**

Run: `cd /mnt/services/hw-rnd-ai-crew/rag-mcp && python -c "from mcp_server.tools import search_outline, list_outline_documents; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
cd /mnt/services/hw-rnd-ai-crew
git add rag-mcp/mcp_server/tools.py rag-mcp/mcp_server/main.py
git commit -m "feat(rag-mcp): add search_outline and list_outline_documents tools"
```

---

### Task 5: Update agent instructions

**Files:**
- Modify: `hermes-gateway/orchestrator/orchestrator.py`

- [ ] **Step 1: Update `_build_soul_md()` in orchestrator.py**

Replace the `_build_soul_md` function:

```python
def _build_soul_md(role: str, name: str) -> str:
    outline_guidance = (
        "\n## Outline (knowledge base)\n"
        "- Для поиска и чтения документов Outline используй `search_outline` (rag-mcp) — он возвращает компактные Markdown-фрагменты.\n"
        "- Для создания и обновления документов используй `mcp_outline_*` (Outline MCP).\n"
        "- НЕ читай полные документы через `mcp_outline_*` — это вызывает context overflow из-за ProseMirror JSON.\n"
    )
    if role in ("ceo", "cto"):
        return (
            f"Ты — {name}, руководящий агент в системе управления задачами Paperclip.\n"
            "Твоя задача — стратегия, приоритизация, координация и делегирование.\n"
            "Все документы и тексты создавай на русском языке.\n"
            + outline_guidance
        )
    return (
        f"Ты — {name}, рабочий агент в системе управления задачами Paperclip.\n"
        "Твоя задача — выполнять задания: исследование, кодирование, тестирование, документирование, анализ.\n"
        "Все документы и тексты создавай на русском языке.\n"
        + outline_guidance
    )
```

- [ ] **Step 2: Verify no syntax errors**

Run: `cd /mnt/services/hw-rnd-ai-crew && python -c "from hermes_gateway.orchestrator.orchestrator import _build_soul_md; print(_build_soul_md('worker', 'Test'))[:50]"`
Expected: prints first 50 chars of the soul text

- [ ] **Step 3: Commit**

```bash
cd /mnt/services/hw-rnd-ai-crew
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "feat(hermes-gateway): add Outline RAG guidance to agent SOUL.md"
```

---

### Task 6: Update AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add Outline RAG section to AGENTS.md**

Add after the existing `### Outline MCP (knowledge base)` section (after line 45):

```markdown
### Outline RAG (search)

- rag-worker индексирует документы Outline → Qdrant коллекция `outline_docs` (markdown chunks)
- rag-mcp предоставляет tool `search_outline` для семантического поиска
- `list_outline_documents` — просмотр проиндексированных документов
- Агенты используют `search_outline` для чтения/поиска документов Outline (вместо `mcp_outline_*`)
- `mcp_outline_*` используется только для создания и обновления документов
- Env vars: `OUTLINE_URL`, `OUTLINE_API_KEY`, `OUTLINE_SYNC_INTERVAL` (default 300s), `OUTLINE_QDRANT_COLLECTION` (default `outline_docs`)
```

- [ ] **Step 2: Commit**

```bash
cd /mnt/services/hw-rnd-ai-crew
git add AGENTS.md
git commit -m "docs: add Outline RAG section to AGENTS.md"
```

---

### Task 7: Update docker-compose.yml and .env.example

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Add Outline env vars to rag-worker in docker-compose.yml**

Add to the `rag-worker` service (after `env_file: .env`):

```yaml
    environment:
      OUTLINE_URL: "${OUTLINE_URL:-}"
      OUTLINE_API_KEY: "${OUTLINE_API_KEY:-}"
      OUTLINE_SYNC_INTERVAL: "${OUTLINE_SYNC_INTERVAL:-300}"
      OUTLINE_QDRANT_COLLECTION: "${OUTLINE_QDRANT_COLLECTION:-outline_docs}"
```

- [ ] **Step 2: Add Outline env vars to .env.example**

Add at the end of `.env.example`:

```
# =============================================================================
# OUTLINE
# =============================================================================
OUTLINE_URL=https://outline.collaborationism.tech
OUTLINE_API_KEY=ol_api_your_key_here
OUTLINE_SYNC_INTERVAL=300
OUTLINE_QDRANT_COLLECTION=outline_docs
```

- [ ] **Step 3: Commit**

```bash
cd /mnt/services/hw-rnd-ai-crew
git add docker-compose.yml .env.example
git commit -m "infra: add Outline env vars to docker-compose and .env.example"
```

---

### Task 8: Build and deploy

- [ ] **Step 1: Rebuild rag-worker**

Run: `cd /mnt/services/hw-rnd-ai-crew && docker compose up -d --force-recreate --build rag-worker`

- [ ] **Step 2: Rebuild rag-mcp**

Run: `cd /mnt/services/hw-rnd-ai-crew && docker compose up -d --force-recreate --build rag-mcp`

- [ ] **Step 3: Verify rag-worker started Outline sync**

Run: `docker logs rag-worker --tail 20`
Expected: see "Outline sync thread started" in logs

- [ ] **Step 4: Verify rag-mcp registered new tools**

Run: `docker logs rag-mcp --tail 10`
Expected: no errors, tool registration successful

- [ ] **Step 5: Check Outline sync status**

Run: `docker exec rag-worker python -c "import requests; r = requests.get('http://localhost:8080/status/outline'); print(r.json())"`
Expected: `{"documents": N, "chunks": M, "collection_exists": true}` where N > 0 if Outline has documents
