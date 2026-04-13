# Nextcloud RAG Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a RAG pipeline that indexes documents from Nextcloud into Qdrant, syncs via webhooks, and exposes semantic search via an MCP server.

**Architecture:** Two Docker services in `hw-rnd-ai-crew` — `rag-worker` (FastAPI, parses/chunks/embeds, writes to Qdrant) and `rag-mcp` (MCP server over SSE, reads from Qdrant, exposed via Traefik with Bearer auth). Worker accesses Nextcloud files via WebDAV HTTP through Traefik. Nextcloud sends file change webhooks to worker via shared `nextcloud-rag` network.

**Tech Stack:** Python 3.11, FastAPI, opendataloader-pdf, sentence-transformers (BGE-large-en-v1.5), qdrant-client, MCP SDK (SSE transport)

---

## File Map

### rag-worker (new)

| File | Responsibility |
|---|---|
| `rag-worker/Dockerfile` | Python 3.11 + Java JRE + pip deps |
| `rag-worker/requirements.txt` | Python dependencies |
| `rag-worker/rag/__init__.py` | Package init |
| `rag-worker/rag/main.py` | FastAPI app, `/webhook/nextcloud`, `/reindex/{path}`, `/status` |
| `rag-worker/rag/chunker.py` | Text chunking with configurable size/overlap (token-based) |
| `rag-worker/rag/embedder.py` | BGE-large-en-v1.5 singleton, embed(list[str]) → list[list[float]] |
| `rag-worker/rag/qdrant_client.py` | Collection init, upsert points, delete by path filter, scroll for status |
| `rag-worker/rag/nextcloud.py` | WebDAV client: PROPFIND list, GET download |
| `rag-worker/rag/index_all.py` | CLI entrypoint: scan all files, compare mtime, index delta |
| `rag-worker/rag/parsers/__init__.py` | Package init |
| `rag-worker/rag/parsers/base.py` | `ParsedDocument` dataclass, `ParserBase` ABC |
| `rag-worker/rag/parsers/registry.py` | Extension → parser class mapping, `get_parser(ext)` |
| `rag-worker/rag/parsers/pdf.py` | opendataloader-pdf wrapper |
| `rag-worker/rag/parsers/plaintext.py` | .txt/.md/.rst/.csv reader |
| `rag-worker/tests/test_chunker.py` | Chunker unit tests |
| `rag-worker/tests/test_parsers.py` | Parser registry + individual parser tests |
| `rag-worker/tests/test_webhook.py` | Webhook endpoint tests |

### rag-mcp (new)

| File | Responsibility |
|---|---|
| `rag-mcp/Dockerfile` | Python 3.11 slim + pip deps (lightweight) |
| `rag-mcp/requirements.txt` | Python dependencies |
| `rag-mcp/mcp_server/__init__.py` | Package init |
| `rag-mcp/mcp_server/main.py` | SSE MCP server, FastAPI app |
| `rag-mcp/mcp_server/auth.py` | Bearer token middleware |
| `rag-mcp/mcp_server/tools.py` | `search_library`, `list_indexed_files`, `get_file_status` |

### Infrastructure (modified)

| File | Change |
|---|---|
| `docker-compose.yml` | Add rag-worker, rag-mcp services |
| `.env` | Add all new env vars |

---

## Task 1: Parser Base + Registry + Plaintext Parser

**Files:**
- Create: `rag-worker/rag/__init__.py`
- Create: `rag-worker/rag/parsers/__init__.py`
- Create: `rag-worker/rag/parsers/base.py`
- Create: `rag-worker/rag/parsers/registry.py`
- Create: `rag-worker/rag/parsers/plaintext.py`
- Create: `rag-worker/tests/__init__.py`
- Create: `rag-worker/tests/test_parsers.py`

- [ ] **Step 1: Write parser base and registry**

Create `rag-worker/rag/__init__.py`:
```python
```

Create `rag-worker/rag/parsers/__init__.py`:
```python
```

Create `rag-worker/rag/parsers/base.py`:
```python
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Any


@dataclass
class ParsedDocument:
    pages: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


class ParserBase(ABC):
    extensions: List[str] = []

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument:
        ...
```

Create `rag-worker/rag/parsers/registry.py`:
```python
from pathlib import Path
from typing import Dict, Optional, Type

from .base import ParserBase
from .plaintext import PlaintextParser

_PARSERS: Dict[str, Type[ParserBase]] = {}


def _register(parser_cls: Type[ParserBase]) -> None:
    for ext in parser_cls.extensions:
        _PARSERS[ext.lower()] = parser_cls


_register(PlaintextParser)


def get_parser(ext: str) -> Optional[ParserBase]:
    cls = _PARSERS.get(ext.lower())
    return cls() if cls else None


def supported_extensions() -> list[str]:
    return list(_PARSERS.keys())
```

Create `rag-worker/rag/parsers/plaintext.py`:
```python
from pathlib import Path
from typing import List

from .base import ParsedDocument, ParserBase


class PlaintextParser(ParserBase):
    extensions: List[str] = [".txt", ".md", ".rst", ".csv"]

    def parse(self, file_path: Path) -> ParsedDocument:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return ParsedDocument(
            pages=[content],
            metadata={"file_type": file_path.suffix.lstrip(".")},
        )
```

- [ ] **Step 2: Write parser tests**

Create `rag-worker/tests/__init__.py`:
```python
```

Create `rag-worker/tests/test_parsers.py`:
```python
import pytest
from pathlib import Path
import tempfile

from rag.parsers.registry import get_parser, supported_extensions
from rag.parsers.base import ParsedDocument


class TestPlaintextParser:
    def test_parse_txt(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("hello world", encoding="utf-8")
        parser = get_parser(".txt")
        assert parser is not None
        doc = parser.parse(f)
        assert isinstance(doc, ParsedDocument)
        assert len(doc.pages) == 1
        assert doc.pages[0] == "hello world"
        assert doc.metadata["file_type"] == "txt"

    def test_parse_md(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Title\n\nSome text", encoding="utf-8")
        parser = get_parser(".md")
        assert parser is not None
        doc = parser.parse(f)
        assert "# Title" in doc.pages[0]

    def test_parse_csv(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c\n1,2,3", encoding="utf-8")
        parser = get_parser(".csv")
        assert parser is not None
        doc = parser.parse(f)
        assert "a,b,c" in doc.pages[0]

    def test_unsupported_extension_returns_none(self):
        parser = get_parser(".xyz")
        assert parser is None

    def test_supported_extensions_includes_txt_md(self):
        exts = supported_extensions()
        assert ".txt" in exts
        assert ".md" in exts
        assert ".csv" in exts
```

- [ ] **Step 3: Install test deps and run tests**

Run:
```bash
cd /mnt/services/hw-rnd-ai-crew/rag-worker && pip install pytest
```
```bash
cd /mnt/services/hw-rnd-ai-crew/rag-worker && PYTHONPATH=. pytest tests/test_parsers.py -v
```
Expected: All 5 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add rag-worker/rag/ rag-worker/tests/
git commit -m "feat(rag-worker): add parser base, registry, and plaintext parser"
```

---

## Task 2: PDF Parser

**Files:**
- Create: `rag-worker/rag/parsers/pdf.py`
- Modify: `rag-worker/rag/parsers/registry.py` (register PDF parser)
- Modify: `rag-worker/tests/test_parsers.py` (add PDF tests)

- [ ] **Step 1: Write PDF parser**

Create `rag-worker/rag/parsers/pdf.py`:
```python
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List

from .base import ParsedDocument, ParserBase

log = logging.getLogger(__name__)


class PdfParser(ParserBase):
    extensions: List[str] = [".pdf"]

    def parse(self, file_path: Path) -> ParsedDocument:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            cmd = [
                "python", "-m", "opendataloader_pdf",
                "--input", str(file_path),
                "--output-dir", str(out_dir),
                "--format", "markdown",
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                log.error("opendataloader-pdf failed: %s", result.stderr[:500])
                raise RuntimeError(f"PDF parsing failed: {result.stderr[:200]}")

            pages = self._collect_pages(out_dir)

        return ParsedDocument(
            pages=pages,
            metadata={"file_type": "pdf"},
        )

    @staticmethod
    def _collect_pages(output_dir: Path) -> List[str]:
        md_files = sorted(output_dir.glob("*.md"))
        if not md_files:
            return []
        pages = []
        for md_file in md_files:
            content = md_file.read_text(encoding="utf-8", errors="replace")
            if content.strip():
                pages.append(content)
        return pages
```

- [ ] **Step 2: Register PDF parser in registry**

Update `rag-worker/rag/parsers/registry.py` — add import and register:
```python
from pathlib import Path
from typing import Dict, Optional, Type

from .base import ParserBase
from .plaintext import PlaintextParser
from .pdf import PdfParser

_PARSERS: Dict[str, Type[ParserBase]] = {}


def _register(parser_cls: Type[ParserBase]) -> None:
    for ext in parser_cls.extensions:
        _PARSERS[ext.lower()] = parser_cls


_register(PlaintextParser)
_register(PdfParser)


def get_parser(ext: str) -> Optional[ParserBase]:
    cls = _PARSERS.get(ext.lower())
    return cls() if cls else None


def supported_extensions() -> list[str]:
    return list(_PARSERS.keys())
```

- [ ] **Step 3: Add PDF parser unit test (mocked)**

Append to `rag-worker/tests/test_parsers.py`:
```python
from unittest.mock import patch, MagicMock
from rag.parsers.pdf import PdfParser


class TestPdfParser:
    def test_pdf_registered(self):
        parser = get_parser(".pdf")
        assert parser is not None
        assert isinstance(parser, PdfParser)

    @patch("rag.parsers.pdf.subprocess.run")
    def test_parse_pdf_success(self, mock_run, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        out_dir = tmp_path / "output"
        out_dir.mkdir()
        (out_dir / "test.md").write_text("Page 1 content")

        mock_run.return_value = MagicMock(returncode=0, stderr="")

        parser = PdfParser()
        doc = parser.parse(pdf_file)
        assert len(doc.pages) == 1
        assert doc.pages[0] == "Page 1 content"
        assert doc.metadata["file_type"] == "pdf"

    @patch("rag.parsers.pdf.subprocess.run")
    def test_parse_pdf_failure_raises(self, mock_run, tmp_path):
        pdf_file = tmp_path / "bad.pdf"
        pdf_file.write_bytes(b"not a pdf")

        mock_run.return_value = MagicMock(returncode=1, stderr="error details")

        parser = PdfParser()
        with pytest.raises(RuntimeError, match="PDF parsing failed"):
            parser.parse(pdf_file)
```

- [ ] **Step 4: Run tests**

Run:
```bash
cd /mnt/services/hw-rnd-ai-crew/rag-worker && PYTHONPATH=. pytest tests/test_parsers.py -v
```
Expected: All tests PASS (7 total: 5 plaintext + 2 PDF).

- [ ] **Step 5: Commit**

```bash
git add rag-worker/rag/parsers/pdf.py rag-worker/rag/parsers/registry.py rag-worker/tests/test_parsers.py
git commit -m "feat(rag-worker): add PDF parser with opendataloader-pdf"
```

---

## Task 3: Chunker

**Files:**
- Create: `rag-worker/rag/chunker.py`
- Create: `rag-worker/tests/test_chunker.py`

- [ ] **Step 1: Write chunker**

Create `rag-worker/rag/chunker.py`:
```python
import re
from dataclasses import dataclass
from typing import List


@dataclass
class Chunk:
    text: str
    page: int
    chunk_index: int


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def chunk_pages(
    pages: List[str],
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> List[Chunk]:
    chunks = []
    for page_idx, page_text in enumerate(pages):
        if not page_text.strip():
            continue

        words = page_text.split()
        if not words:
            continue

        step = max(1, chunk_size - chunk_overlap)
        chunk_idx = 0

        start = 0
        while start < len(words):
            end = start + chunk_size
            segment = " ".join(words[start:end])
            if segment.strip():
                chunks.append(Chunk(
                    text=segment,
                    page=page_idx,
                    chunk_index=chunk_idx,
                ))
                chunk_idx += 1
            start += step

    return chunks
```

- [ ] **Step 2: Write chunker tests**

Create `rag-worker/tests/test_chunker.py`:
```python
from rag.chunker import chunk_pages, Chunk


class TestChunker:
    def test_single_page_single_chunk(self):
        pages = ["word " * 100]
        chunks = chunk_pages(pages, chunk_size=512, chunk_overlap=64)
        assert len(chunks) == 1
        assert chunks[0].page == 0
        assert chunks[0].chunk_index == 0

    def test_multiple_chunks_per_page(self):
        text = "word " * 600
        pages = [text]
        chunks = chunk_pages(pages, chunk_size=100, chunk_overlap=10)
        assert len(chunks) > 1
        assert chunks[0].page == 0
        assert chunks[1].page == 0
        assert chunks[1].chunk_index == 1

    def test_multiple_pages(self):
        pages = ["page zero text", "page one text", "page two text"]
        chunks = chunk_pages(pages, chunk_size=512, chunk_overlap=64)
        assert len(chunks) == 3
        assert chunks[0].page == 0
        assert chunks[1].page == 1
        assert chunks[2].page == 2

    def test_empty_page_skipped(self):
        pages = ["", "content here"]
        chunks = chunk_pages(pages, chunk_size=512, chunk_overlap=64)
        assert len(chunks) == 1
        assert chunks[0].page == 1

    def test_overlap_produces_shared_words(self):
        text = " ".join(f"w{i}" for i in range(200))
        pages = [text]
        chunks = chunk_pages(pages, chunk_size=50, chunk_overlap=10)
        if len(chunks) > 1:
            first_words = chunks[0].text.split()
            second_words = chunks[1].text.split()
            overlap = set(first_words) & set(second_words)
            assert len(overlap) > 0

    def test_empty_input(self):
        chunks = chunk_pages([], chunk_size=512, chunk_overlap=64)
        assert chunks == []
```

- [ ] **Step 3: Run tests**

Run:
```bash
cd /mnt/services/hw-rnd-ai-crew/rag-worker && PYTHONPATH=. pytest tests/test_chunker.py -v
```
Expected: All 6 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add rag-worker/rag/chunker.py rag-worker/tests/test_chunker.py
git commit -m "feat(rag-worker): add text chunker with configurable size/overlap"
```

---

## Task 4: Embedder

**Files:**
- Create: `rag-worker/rag/embedder.py`

- [ ] **Step 1: Write embedder**

Create `rag-worker/rag/embedder.py`:
```python
import logging
import os
from typing import List, Optional

log = logging.getLogger(__name__)

_model = None
_model_name: Optional[str] = None


def get_model():
    global _model, _model_name
    name = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5")
    if _model is None or _model_name != name:
        from sentence_transformers import SentenceTransformer
        log.info("Loading embedding model: %s", name)
        _model = SentenceTransformer(name)
        _model_name = name
    return _model


def embed(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    model = get_model()
    embeddings = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    return [e.tolist() for e in embeddings]


def embedding_dim() -> int:
    model = get_model()
    return model.get_sentence_embedding_dimension()
```

- [ ] **Step 2: Commit**

```bash
git add rag-worker/rag/embedder.py
git commit -m "feat(rag-worker): add BGE-large-en-v1.5 embedder with singleton loading"
```

---

## Task 5: Qdrant Client

**Files:**
- Create: `rag-worker/rag/qdrant_client.py`

- [ ] **Step 1: Write Qdrant client**

Create `rag-worker/rag/qdrant_client.py`:
```python
import logging
import os
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
    PointsList,
)

log = logging.getLogger(__name__)


def _get_client() -> QdrantClient:
    url = os.environ.get("QDRANT_URL", "http://qdrant:6333")
    return QdrantClient(url=url)


def _collection_name() -> str:
    return os.environ.get("QDRANT_COLLECTION", "pdf_library")


def ensure_collection(vector_size: int) -> None:
    client = _get_client()
    name = _collection_name()
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        log.info("Created collection %s (dim=%d)", name, vector_size)
    else:
        log.info("Collection %s already exists", name)


def upsert_chunks(
    points: List[Dict[str, Any]],
) -> None:
    if not points:
        return
    client = _get_client()
    name = _collection_name()
    qdrant_points = []
    for i, p in enumerate(points):
        qdrant_points.append(PointStruct(
            id=p["id"],
            vector=p["vector"],
            payload=p["payload"],
        ))
    client.upsert(collection_name=name, points=qdrant_points)
    log.info("Upserted %d points to %s", len(qdrant_points), name)


def delete_by_path(file_path: str) -> int:
    client = _get_client()
    name = _collection_name()
    client.delete(
        collection_name=name,
        points_filter=Filter(
            must=[FieldCondition(key="path", match=MatchValue(value=file_path))]
        ),
    )
    count_before = _count_by_path(client, name, file_path)
    log.info("Deleted points for path=%s", file_path)
    return count_before


def _count_by_path(client: QdrantClient, collection: str, file_path: str) -> int:
    result, _ = client.scroll(
        collection_name=collection,
        scroll_filter=Filter(
            must=[FieldCondition(key="path", match=MatchValue(value=file_path))]
        ),
        limit=0,
        with_payload=False,
    )
    return len(result)


def get_indexed_files() -> List[Dict[str, Any]]:
    client = _get_client()
    name = _collection_name()
    seen = {}
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=name,
            limit=100,
            offset=offset,
            with_payload=["path", "filename", "file_type", "modified_time"],
        )
        for r in records:
            p = r.payload
            path = p.get("path", "")
            if path not in seen:
                seen[path] = {
                    "path": path,
                    "filename": p.get("filename", ""),
                    "file_type": p.get("file_type", ""),
                    "modified_time": p.get("modified_time", 0),
                    "chunk_count": 0,
                }
            seen[path]["chunk_count"] += 1
        if offset is None:
            break
    return list(seen.values())


def get_status() -> Dict[str, Any]:
    client = _get_client()
    name = _collection_name()
    if not client.collection_exists(name):
        return {"files": 0, "chunks": 0, "collection_exists": False}
    info = client.get_collection(name)
    files = get_indexed_files()
    return {
        "files": len(files),
        "chunks": info.points_count,
        "collection_exists": True,
    }
```

- [ ] **Step 2: Commit**

```bash
git add rag-worker/rag/qdrant_client.py
git commit -m "feat(rag-worker): add Qdrant client for collection management"
```

---

## Task 6: Nextcloud WebDAV Client

**Files:**
- Create: `rag-worker/rag/nextcloud.py`

- [ ] **Step 1: Write WebDAV client**

Create `rag-worker/rag/nextcloud.py`:
```python
import io
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)


@dataclass
class NextcloudFile:
    path: str
    filename: str
    mimetype: str
    size: int
    modified_time: int
    file_id: Optional[int] = None


def _base_url() -> str:
    return os.environ.get("NEXTCLOUD_URL", "https://nextcloud.example.com")


def _auth() -> tuple:
    user = os.environ.get("NEXTCLOUD_USER", "")
    password = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
    return (user, password)


def _dav_base() -> str:
    user = os.environ.get("NEXTCLOUD_USER", "")
    return f"{_base_url()}/remote.php/dav/files/{user}"


def list_files(directory: str = "/") -> List[NextcloudFile]:
    url = _dav_base() + quote(directory, safe="/")
    headers = {"Depth": "1"}
    resp = requests.request("PROPFIND", url, auth=_auth(), headers=headers, timeout=30)
    resp.raise_for_status()
    return _parse_propfind(resp.text)


def _parse_propfind(xml_text: str) -> List[NextcloudFile]:
    ns = {"d": "DAV:"}
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
        file_id_el = props.find("{http://owncloud.org/ns}fileid", ns)

        mimetype = getcontenttype.text if getcontenttype is not None else ""
        size = int(getcontentlength.text) if getcontentlength is not None else 0

        modified_time = 0
        if getlastmodified is not None and getlastmodified.text:
            from email.utils import parsedate_to_datetime
            try:
                dt = parsedate_to_datetime(getlastmodified.text)
                modified_time = int(dt.timestamp())
            except Exception:
                pass

        file_id = int(file_id_el.text) if file_id_el is not None else None

        filename = href_text.rstrip("/").split("/")[-1]

        path_parts = href_text.split("/files/")
        relative_path = "/" + path_parts[-1] if len(path_parts) > 1 else href_text

        if not mimetype and not href_text.endswith("/"):
            continue

        if mimetype:
            files.append(NextcloudFile(
                path=relative_path,
                filename=filename,
                mimetype=mimetype,
                size=size,
                modified_time=modified_time,
                file_id=file_id,
            ))

    return files


def list_all_files(directory: str = "/") -> List[NextcloudFile]:
    all_files = []
    queue = [directory]
    while queue:
        current = queue.pop(0)
        try:
            items = list_files(current)
        except Exception as e:
            log.error("Failed to list %s: %s", current, e)
            continue
        for item in items:
            if item.mimetype in ("httpd/unix-directory", "inode/directory"):
                subpath = item.path if item.path.endswith("/") else item.path + "/"
                if subpath != current:
                    queue.append(subpath)
            else:
                all_files.append(item)
    return all_files


def download_file(file_path: str) -> bytes:
    url = _dav_base() + quote(file_path, safe="/")
    resp = requests.get(url, auth=_auth(), timeout=120)
    resp.raise_for_status()
    return resp.content
```

- [ ] **Step 2: Commit**

```bash
git add rag-worker/rag/nextcloud.py
git commit -m "feat(rag-worker): add Nextcloud WebDAV client"
```

---

## Task 7: FastAPI Webhook App + index_all CLI

**Files:**
- Create: `rag-worker/rag/main.py`
- Create: `rag-worker/rag/index_all.py`
- Create: `rag-worker/tests/test_webhook.py`

- [ ] **Step 1: Write main FastAPI app**

Create `rag-worker/rag/main.py`:
```python
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rag.chunker import chunk_pages, Chunk
from rag.embedder import embed, embedding_dim
from rag.parsers.registry import get_parser, supported_extensions
from rag.qdrant_client import (
    ensure_collection, upsert_chunks, delete_by_path,
    get_status as qdrant_status,
)

log = logging.getLogger(__name__)

app = FastAPI(title="RAG Worker")

_collection_initialized = False


def _init_collection():
    global _collection_initialized
    if not _collection_initialized:
        dim = embedding_dim()
        ensure_collection(dim)
        _collection_initialized = True


def _ext_from_path(path: str) -> str:
    return Path(path).suffix.lower()


def index_file(file_path: str, file_content: bytes, modified_time: int = 0) -> int:
    _init_collection()
    ext = _ext_from_path(file_path)
    parser = get_parser(ext)
    if parser is None:
        log.info("Skipping unsupported file: %s (ext=%s)", file_path, ext)
        return 0

    delete_by_path(file_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_file = Path(tmp_dir) / Path(file_path).name
        tmp_file.write_bytes(file_content)
        doc = parser.parse(tmp_file)

    chunks = chunk_pages(
        doc.pages,
        chunk_size=int(os.environ.get("CHUNK_SIZE", "512")),
        chunk_overlap=int(os.environ.get("CHUNK_OVERLAP", "64")),
    )

    if not chunks:
        return 0

    texts = [c.text for c in chunks]
    vectors = embed(texts)

    points = []
    for chunk, vector in zip(chunks, vectors):
        point_id = hashlib.md5(
            f"{file_path}:{chunk.page}:{chunk.chunk_index}".encode()
        ).hexdigest()
        points.append({
            "id": point_id,
            "vector": vector,
            "payload": {
                "filename": Path(file_path).name,
                "path": file_path,
                "page": chunk.page,
                "chunk_index": chunk.chunk_index,
                "modified_time": modified_time,
                "file_type": doc.metadata.get("file_type", ext.lstrip(".")),
                "content": chunk.text,
            },
        })

    upsert_chunks(points)
    log.info("Indexed %s: %d chunks", file_path, len(points))
    return len(points)


@app.post("/webhook/nextcloud")
async def webhook_nextcloud(request: Request):
    body = await request.json()
    log.info("Webhook received: %s", body)

    obj = body.get("object", {})
    file_name = obj.get("name", "")
    file_path = obj.get("path", "")
    signal = body.get("signal", "")
    mimetype = obj.get("mimetype", "")

    if not file_path:
        return JSONResponse({"status": "ignored", "reason": "no path"})

    ext = _ext_from_path(file_path)
    if ext not in supported_extensions():
        return JSONResponse({"status": "skipped", "reason": f"unsupported type: {ext}"})

    if signal in ("FileDeleted", "file_deleted"):
        delete_by_path(file_path)
        return JSONResponse({"status": "deleted", "path": file_path})

    from rag.nextcloud import download_file
    try:
        content = download_file(file_path)
    except Exception as e:
        log.error("Failed to download %s: %s", file_path, e)
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)

    size = obj.get("size", 0)
    count = index_file(file_path, content, modified_time=size)
    return JSONResponse({"status": "indexed", "path": file_path, "chunks": count})


@app.post("/reindex/{file_path:path}")
async def reindex_file(file_path: str):
    ext = _ext_from_path(file_path)
    if ext not in supported_extensions():
        return JSONResponse({"status": "skipped", "reason": f"unsupported type: {ext}"})

    from rag.nextcloud import download_file
    try:
        content = download_file(file_path)
    except Exception as e:
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)

    count = index_file(file_path, content)
    return JSONResponse({"status": "indexed", "path": file_path, "chunks": count})


@app.get("/status")
async def status():
    return qdrant_status()
```

- [ ] **Step 2: Write index_all CLI**

Create `rag-worker/rag/index_all.py`:
```python
import logging
import os
import sys

from rag.nextcloud import list_all_files, download_file
from rag.parsers.registry import supported_extensions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SUPPORTED_MIME_MAP = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
}


def main():
    from rag.main import index_file
    from rag.qdrant_client import get_indexed_files

    log.info("Scanning Nextcloud for files...")
    files = list_all_files("/")
    log.info("Found %d files total", len(files))

    indexed = {f["path"]: f["modified_time"] for f in get_indexed_files()}
    log.info("Already indexed: %d files", len(indexed))

    supported_exts = set(supported_extensions())
    to_index = []
    for f in files:
        ext = ""
        for mime, e in SUPPORTED_MIME_MAP.items():
            if f.mimetype.startswith(mime.split("/")[0]):
                ext = e
        if not ext:
            name = f.filename
            dot = name.rfind(".")
            if dot >= 0:
                ext = name[dot:].lower()

        if ext not in supported_exts:
            continue

        if f.path in indexed and indexed[f.path] >= f.modified_time:
            continue

        to_index.append(f)

    log.info("Files to index: %d", len(to_index))

    for i, f in enumerate(to_index, 1):
        log.info("[%d/%d] Indexing %s", i, len(to_index), f.path)
        try:
            content = download_file(f.path)
            count = index_file(f.path, content, modified_time=f.modified_time)
            log.info("[%d/%d] %s → %d chunks", i, len(to_index), f.path, count)
        except Exception as e:
            log.error("[%d/%d] FAILED %s: %s", i, len(to_index), f.path, e)

    log.info("Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write webhook tests**

Create `rag-worker/tests/test_webhook.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from rag.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


class TestWebhook:
    @patch("rag.main.index_file")
    @patch("rag.main.download_file")
    @patch("rag.main.delete_by_path")
    def test_webhook_file_created(self, mock_delete, mock_download, mock_index):
        mock_download.return_value = b"fake pdf content"
        mock_index.return_value = 3
        resp = client.post("/webhook/nextcloud", json={
            "object": {
                "name": "test.pdf",
                "path": "/Documents/test.pdf",
                "mimetype": "application/pdf",
                "size": 100,
            },
            "signal": "FileCreated",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "indexed"
        assert data["chunks"] == 3

    @patch("rag.main.delete_by_path")
    def test_webhook_file_deleted(self, mock_delete):
        resp = client.post("/webhook/nextcloud", json={
            "object": {
                "name": "test.pdf",
                "path": "/Documents/test.pdf",
                "mimetype": "application/pdf",
            },
            "signal": "FileDeleted",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_webhook_unsupported_type(self):
        resp = client.post("/webhook/nextcloud", json={
            "object": {
                "name": "photo.png",
                "path": "/Photos/photo.png",
                "mimetype": "image/png",
            },
            "signal": "FileCreated",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_webhook_no_path(self):
        resp = client.post("/webhook/nextcloud", json={
            "object": {},
            "signal": "FileCreated",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
```

- [ ] **Step 4: Run tests**

Run:
```bash
cd /mnt/services/hw-rnd-ai-crew/rag-worker && pip install fastapi httpx && PYTHONPATH=. pytest tests/test_webhook.py -v
```
Expected: All 4 webhook tests PASS.

- [ ] **Step 5: Commit**

```bash
git add rag-worker/rag/main.py rag-worker/rag/index_all.py rag-worker/tests/test_webhook.py
git commit -m "feat(rag-worker): add FastAPI webhook app and bulk index CLI"
```

---

## Task 8: rag-worker Dockerfile + requirements.txt

**Files:**
- Create: `rag-worker/requirements.txt`
- Create: `rag-worker/Dockerfile`

- [ ] **Step 1: Write requirements.txt**

Create `rag-worker/requirements.txt`:
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
qdrant-client>=1.12.0
sentence-transformers>=3.3.0
opendataloader-pdf>=2.2.0
httpx>=0.27.0
requests>=2.31.0
pytest>=8.0.0
```

- [ ] **Step 2: Write Dockerfile**

Create `rag-worker/Dockerfile`:
```dockerfile
FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends default-jre-headless && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "rag.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 3: Commit**

```bash
git add rag-worker/Dockerfile rag-worker/requirements.txt
git commit -m "feat(rag-worker): add Dockerfile with Python + Java JRE"
```

---

## Task 9: MCP Server (rag-mcp)

**Files:**
- Create: `rag-mcp/requirements.txt`
- Create: `rag-mcp/Dockerfile`
- Create: `rag-mcp/mcp_server/__init__.py`
- Create: `rag-mcp/mcp_server/auth.py`
- Create: `rag-mcp/mcp_server/tools.py`
- Create: `rag-mcp/mcp_server/main.py`

- [ ] **Step 1: Write requirements.txt**

Create `rag-mcp/requirements.txt`:
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
qdrant-client>=1.12.0
sentence-transformers>=3.3.0
mcp>=1.0.0
sse-starlette>=2.0.0
```

- [ ] **Step 2: Write Dockerfile**

Create `rag-mcp/Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "mcp_server.main:app", "--host", "0.0.0.0", "--port", "8081"]
```

- [ ] **Step 3: Write auth middleware**

Create `rag-mcp/mcp_server/__init__.py`:
```python
```

Create `rag-mcp/mcp_server/auth.py`:
```python
import os
from fastapi import Request
from fastapi.responses import JSONResponse


_BEARER_PREFIX = "Bearer "


async def auth_middleware(request: Request, call_next):
    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if not token:
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith(_BEARER_PREFIX):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    provided = auth_header[len(_BEARER_PREFIX):]
    if provided != token:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    return await call_next(request)
```

- [ ] **Step 4: Write tools**

Create `rag-mcp/mcp_server/tools.py`:
```python
import logging
import os
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

log = logging.getLogger(__name__)


def _get_client() -> QdrantClient:
    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))


def _collection() -> str:
    return os.environ.get("QDRANT_COLLECTION", "pdf_library")


def search_library(
    query_vector: List[float],
    top_k: int = 5,
    filter_filename: Optional[str] = None,
    filter_file_type: Optional[str] = None,
) -> Dict[str, Any]:
    client = _get_client()
    name = _collection()

    must = []
    if filter_filename:
        must.append(FieldCondition(key="filename", match=MatchValue(value=filter_filename)))
    if filter_file_type:
        must.append(FieldCondition(key="file_type", match=MatchValue(value=filter_file_type)))

    search_filter = Filter(must=must) if must else None

    results = client.search(
        collection_name=name,
        query_vector=query_vector,
        limit=top_k,
        query_filter=search_filter,
        with_payload=True,
    )

    hits = []
    for r in results:
        p = r.payload or {}
        hits.append({
            "content": p.get("content", ""),
            "score": r.score,
            "filename": p.get("filename", ""),
            "path": p.get("path", ""),
            "page": p.get("page", 0),
            "chunk_index": p.get("chunk_index", 0),
        })

    return {"results": hits, "total": len(hits), "query_vector_dim": len(query_vector)}


def list_indexed_files(
    filter_file_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    client = _get_client()
    name = _collection()

    must = []
    if filter_file_type:
        must.append(FieldCondition(key="file_type", match=MatchValue(value=filter_file_type)))

    seen = {}
    offset = None
    while True:
        scroll_filter = Filter(must=must) if must else None
        records, offset = client.scroll(
            collection_name=name,
            limit=100,
            offset=offset,
            scroll_filter=scroll_filter,
            with_payload=["path", "filename", "file_type", "modified_time"],
        )
        for r in records:
            p = r.payload
            path = p.get("path", "")
            if path not in seen:
                seen[path] = {
                    "path": path,
                    "filename": p.get("filename", ""),
                    "file_type": p.get("file_type", ""),
                    "modified_time": p.get("modified_time", 0),
                    "chunk_count": 0,
                }
            seen[path]["chunk_count"] += 1
        if offset is None:
            break
    return list(seen.values())


def get_file_status(path: str) -> Dict[str, Any]:
    client = _get_client()
    name = _collection()
    records, _ = client.scroll(
        collection_name=name,
        scroll_filter=Filter(
            must=[FieldCondition(key="path", match=MatchValue(value=path))]
        ),
        limit=100,
        with_payload=["filename", "modified_time", "file_type", "page"],
    )
    if not records:
        return {"indexed": False, "path": path}
    p = records[0].payload
    return {
        "indexed": True,
        "path": path,
        "filename": p.get("filename", ""),
        "chunk_count": len(records),
        "modified_time": p.get("modified_time", 0),
        "file_type": p.get("file_type", ""),
    }
```

- [ ] **Step 5: Write main MCP server app**

Create `rag-mcp/mcp_server/main.py`:
```python
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
```

- [ ] **Step 6: Commit**

```bash
git add rag-mcp/
git commit -m "feat(rag-mcp): add MCP server with search_library, list, and status tools"
```

---

## Task 10: Docker Compose + .env

**Files:**
- Modify: `docker-compose.yml`
- Create: `.env` (if not exists) or append env vars

- [ ] **Step 1: Create .env**

Check if `/mnt/services/hw-rnd-ai-crew/.env` exists. Create/append:
```bash
# Nextcloud
NEXTCLOUD_URL=https://nextcloud.example.com
NEXTCLOUD_USER=admin
NEXTCLOUD_APP_PASSWORD=TODO_GENERATE

# Qdrant
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=pdf_library

# Embeddings
EMBEDDING_MODEL=BAAI/bge-large-en-v1.5
CHUNK_SIZE=512
CHUNK_OVERLAP=64

# MCP
MCP_BEARER_TOKEN=TODO_GENERATE

# Traefik
SUB_DOMAIN=rag
SERVER_DOMAIN=example.com
```

- [ ] **Step 2: Update docker-compose.yml**

Read current `/mnt/services/hw-rnd-ai-crew/docker-compose.yml` and append `rag-worker` and `rag-mcp` services. The `nextcloud-rag` external network must also be added.

The final compose should include:
```yaml
services:
  # ... existing qdrant, ollama ...

  rag-worker:
    build: ./rag-worker
    container_name: rag-worker
    restart: unless-stopped
    env_file: .env
    networks:
      - local-ai-internal
      - nextcloud-rag
      - traefik

  rag-mcp:
    build: ./rag-mcp
    container_name: rag-mcp
    restart: unless-stopped
    env_file: .env
    networks:
      - local-ai-internal
      - traefik
    labels:
      traefik.enable: true
      traefik.http.routers.rag-mcp.rule: Host(`${SUB_DOMAIN}.${SERVER_DOMAIN}`)
      traefik.http.routers.rag-mcp.entrypoints: websecure
      traefik.http.routers.rag-mcp.tls: true
      traefik.http.services.rag-mcp.loadbalancer.server.port: 8081

networks:
  # ... existing local-ai-internal ...
  nextcloud-rag:
    name: nextcloud-rag
    external: true
  traefik:
    name: traefik-public
    external: true
```

- [ ] **Step 3: Create nextcloud-rag network**

Run:
```bash
docker network create nextcloud-rag
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env
git commit -m "infra: add rag-worker and rag-mcp to docker-compose with network config"
```

---

## Task 11: Update Nextcloud compose for nextcloud-rag network

**Files:**
- Modify: `/mnt/services/nextcloud/docker-compose.yml`

- [ ] **Step 1: Add nextcloud-rag network to Nextcloud**

Add `nextcloud-rag` network to the `nextcloud` service and `web` service in `/mnt/services/nextcloud/docker-compose.yml`, and add the network definition.

Under `services.nextcloud.networks`, add `- nextcloud-rag`.
Under `services.web.networks`, add `- nextcloud-rag`.
Under `networks:`, add:
```yaml
  nextcloud-rag:
    name: nextcloud-rag
    external: true
```

- [ ] **Step 2: Restart Nextcloud to pick up new network**

Run:
```bash
cd /mnt/services/nextcloud && docker compose up -d
```

- [ ] **Step 3: Verify connectivity**

Run:
```bash
docker exec nextcloud-nextcloud-1 wget -qO- http://rag-worker:8080/status 2>/dev/null
```
Expected: JSON response (may be empty collection status initially).

- [ ] **Step 4: Commit**

```bash
git add /mnt/services/nextcloud/docker-compose.yml
git commit -m "infra(nextcloud): add nextcloud-rag network for webhook delivery"
```

---

## Task 12: Build and smoke test

**Files:** None (validation only)

- [ ] **Step 1: Build rag-worker image**

Run:
```bash
cd /mnt/services/hw-rnd-ai-crew && docker compose build rag-worker
```
Expected: Build succeeds.

- [ ] **Step 2: Build rag-mcp image**

Run:
```bash
cd /mnt/services/hw-rnd-ai-crew && docker compose build rag-mcp
```
Expected: Build succeeds.

- [ ] **Step 3: Start all services**

Run:
```bash
cd /mnt/services/hw-rnd-ai-crew && docker compose up -d
```

- [ ] **Step 4: Verify rag-worker is healthy**

Run:
```bash
docker exec rag-worker curl -s http://localhost:8080/status
```
Expected: `{"files":0,"chunks":0,"collection_exists":false}` or similar.

- [ ] **Step 5: Verify rag-mcp is accessible through Traefik**

Run:
```bash
curl -s https://rag.example.com/sse -H "Authorization: Bearer $MCP_BEARER_TOKEN" --max-time 5
```
Expected: SSE connection attempt (may time out, but should not 404/401).

- [ ] **Step 6: Verify Nextcloud → rag-worker webhook path**

Run:
```bash
docker exec nextcloud-nextcloud-1 wget -qO- http://rag-worker:8080/status 2>/dev/null
```
Expected: JSON status response.

---

## Task 13: Generate secrets and configure Nextcloud Flow App

**Files:** None (manual configuration)

- [ ] **Step 1: Generate app password in Nextcloud**

In Nextcloud UI: Settings → Security → Devices & sessions → Create new app password.
Copy the password to `NEXTCLOUD_APP_PASSWORD` in `/mnt/services/hw-rnd-ai-crew/.env`.

- [ ] **Step 2: Generate MCP bearer token**

Run:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```
Copy the output to `MCP_BEARER_TOKEN` in `/mnt/services/hw-rnd-ai-crew/.env`.

- [ ] **Step 3: Configure Nextcloud Flow App**

In Nextcloud UI: Settings → Workflow engine:
1. Create flow: **When a file is created or updated**
   - Condition: File type matches `application/pdf`, `text/plain`, `text/markdown`, `text/csv`
   - Action: Send HTTP POST to `http://rag-worker:8080/webhook/nextcloud`
2. Create flow: **When a file is deleted**
   - Same conditions and action URL.

- [ ] **Step 4: Test webhook end-to-end**

Upload a small `.txt` file to Nextcloud. Check rag-worker logs:
```bash
docker logs rag-worker --tail 20
```
Expected: Log entry showing the file was received and indexed.

---

## Task 14: Initial bulk indexing

**Files:** None (CLI execution)

- [ ] **Step 1: Run bulk index**

Run:
```bash
docker exec rag-worker python -m rag.index_all
```
Expected: Logs showing files being scanned, parsed, and indexed. This may take several hours for 1000 PDFs.

- [ ] **Step 2: Verify indexed count**

Run:
```bash
docker exec rag-worker curl -s http://localhost:8080/status
```
Expected: `files` and `chunks` counts reflecting the indexed library.

- [ ] **Step 3: Test search via MCP**

Use `curl` or MCP client to call `search_library` through `https://rag.example.com`:
```bash
curl -X POST https://rag.example.com/messages/ \
  -H "Authorization: Bearer $MCP_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_library","arguments":{"query":"test query","top_k":3}},"id":1}'
```
Expected: JSON response with matching chunks.
