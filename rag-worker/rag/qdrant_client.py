import logging
import os
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
    FilterSelector,
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
        points_selector=FilterSelector(
            filter=Filter(
                must=[FieldCondition(key="path", match=MatchValue(value=file_path))]
            )
        ),
    )
    log.info("Deleted points for path=%s", file_path)
    return 0


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
