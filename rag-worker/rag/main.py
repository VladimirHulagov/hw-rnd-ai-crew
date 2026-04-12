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
