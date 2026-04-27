from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    CollectionStatus,
    Distance,
    PointStruct,
    VectorParams,
    models,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [session-indexer] %(levelname)s: %(message)s",
    datefmt="[%H:%M:%S]",
)
logger = logging.getLogger(__name__)

PROFILES_ROOT = Path(os.environ.get("HERMES_PROFILES_ROOT", "/root/.hermes/profiles"))
STATE_FILE = PROFILES_ROOT / "indexer-state.json"
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
COLLECTION_NAME = "agent_memory"
VECTOR_SIZE = 768
POLL_INTERVAL = int(os.environ.get("INDEXER_POLL_INTERVAL", "600"))
BATCH_SIZE = 1
MAX_TEXT_LEN = 1000


def _qdrant() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, timeout=30)


def _ensure_collection(qd: QdrantClient):
    try:
        qd.get_collection(COLLECTION_NAME)
        return
    except Exception:
        pass
    qd.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    qd.create_payload_index(COLLECTION_NAME, "agent_name", models.PayloadSchemaType.KEYWORD)
    qd.create_payload_index(COLLECTION_NAME, "source", models.PayloadSchemaType.KEYWORD)
    logger.info("Created Qdrant collection '%s'", COLLECTION_NAME)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_run": None, "files": {}}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def _file_hash(p: Path) -> str:
    st = p.stat()
    raw = f"{p}:{st.st_mtime}:{st.st_size}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _resolve_agent_name(profile_dir: Path) -> str:
    soul = profile_dir / "SOUL.md"
    if soul.exists():
        first_line = soul.read_text().split("\n")[0]
        name = first_line.strip().split("—")[0].strip()
        if name:
            return name
    return profile_dir.name[:8]


def _extract_jsonl_chunks(path: Path, agent_id: str, agent_name: str) -> list[dict]:
    chunks = []
    session_id = path.stem
    for line_no, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not content or len(content.strip()) < 20:
            continue
        text = content[:MAX_TEXT_LEN]
        tool_calls = []
        tc_raw = msg.get("tool_calls")
        if isinstance(tc_raw, list):
            for tc in tc_raw:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                if name:
                    tool_calls.append(name)
        ts = msg.get("timestamp", "")
        chunks.append({
            "agent_id": agent_id,
            "agent_name": agent_name,
            "session_id": session_id,
            "timestamp": ts,
            "text": text,
            "source": "session",
            "tool_calls": tool_calls,
            "chunk_index": line_no,
        })
    return chunks


def _extract_memory_chunks(path: Path, agent_id: str, agent_name: str) -> list[dict]:
    text = path.read_text()
    if not text.strip():
        return []
    paragraphs = [p.strip() for p in text.split("\u00a7") if p.strip()]
    chunks = []
    for i, para in enumerate(paragraphs):
        if len(para) < 20:
            continue
        chunks.append({
            "agent_id": agent_id,
            "agent_name": agent_name,
            "session_id": "memory_md",
            "timestamp": "",
            "text": para[:MAX_TEXT_LEN],
            "source": "memory_md",
            "tool_calls": [],
            "chunk_index": i,
        })
    return chunks


async def _embed(texts: list[str]) -> list[list[float]]:
    resp = await asyncio.to_thread(
        lambda: httpx.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": texts},
            timeout=120.0,
        ).raise_for_status().json()
    )
    embeddings = resp.get("embeddings", [])
    if len(embeddings) != len(texts):
        raise ValueError(f"Embedding count mismatch: {len(embeddings)} != {len(texts)}")
    return embeddings


def _point_id(agent_id: str, session_id: str, chunk_index: int) -> int:
    raw = f"{agent_id}:{session_id}:{chunk_index}"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:16], 16)


async def _index_batch(qd: QdrantClient, chunks: list[dict]) -> bool:
    texts = [c["text"] for c in chunks]
    vectors = None
    for attempt in range(3):
        try:
            vectors = await _embed(texts)
            break
        except Exception as e:
            if attempt == 2:
                logger.error("Embedding failed after 3 attempts: %s", e)
                return False
            wait = 2 ** attempt
            logger.warning("Embedding attempt %d failed, retrying in %ds: %s", attempt + 1, wait, e)
            await asyncio.sleep(wait)

    points = []
    for chunk, vector in zip(chunks, vectors):
        pid = _point_id(chunk["agent_id"], chunk["session_id"], chunk["chunk_index"])
        points.append(PointStruct(
            id=pid,
            vector=vector,
            payload={
                "agent_id": chunk["agent_id"],
                "agent_name": chunk["agent_name"],
                "session_id": chunk["session_id"],
                "timestamp": chunk["timestamp"],
                "text": chunk["text"],
                "source": chunk["source"],
                "tool_calls": chunk.get("tool_calls", []),
            },
        ))

    qd.upsert(collection_name=COLLECTION_NAME, points=points)
    return True


async def run_index_cycle():
    qd = _qdrant()
    _ensure_collection(qd)
    state = _load_state()
    all_chunks = []
    file_hashes = {}

    for profile_dir in sorted(PROFILES_ROOT.iterdir()):
        if not profile_dir.is_dir():
            continue
        agent_id = profile_dir.name
        agent_name = _resolve_agent_name(profile_dir)

        sessions_dir = profile_dir / "sessions"
        if sessions_dir.is_dir():
            for jsonl_file in sorted(sessions_dir.glob("*.jsonl")):
                fh = _file_hash(jsonl_file)
                file_hashes[str(jsonl_file)] = fh
                if state["files"].get(str(jsonl_file), {}).get("hash") == fh:
                    continue
                chunks = _extract_jsonl_chunks(jsonl_file, agent_id, agent_name)
                all_chunks.extend(chunks)
                logger.info("Extracted %d chunks from %s", len(chunks), jsonl_file.name)

        memory_file = profile_dir / "memories" / "MEMORY.md"
        if memory_file.exists():
            fh = _file_hash(memory_file)
            file_hashes[str(memory_file)] = fh
            if state["files"].get(str(memory_file), {}).get("hash") != fh:
                chunks = _extract_memory_chunks(memory_file, agent_id, agent_name)
                all_chunks.extend(chunks)
                logger.info("Extracted %d chunks from MEMORY.md (%s)", len(chunks), agent_name)

    if not all_chunks:
        logger.info("No new chunks to index")
    else:
        failed_sources = set()
        logger.info("Indexing %d chunks in batches of %d...", len(all_chunks), BATCH_SIZE)
        for i in range(0, len(all_chunks), BATCH_SIZE):
            batch = all_chunks[i : i + BATCH_SIZE]
            ok = await _index_batch(qd, batch)
            if not ok:
                for c in batch:
                    src = f"{c['agent_id']}/{c['session_id']}"
                    failed_sources.add(src)
            logger.info("Indexed batch %d/%d%s", i // BATCH_SIZE + 1, (len(all_chunks) + BATCH_SIZE - 1) // BATCH_SIZE,
                        "" if ok else " (FAILED)")

    for path_str, fh in file_hashes.items():
        skip = False
        for src in failed_sources:
            if src in path_str:
                skip = True
                break
        if skip:
            logger.warning("Not marking as done (had failures): %s", path_str)
            continue
        state["files"][path_str] = {"hash": fh}
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    logger.info("Index cycle complete. Tracked: %d, Failed sources: %d", len(file_hashes), len(failed_sources))


async def main():
    logger.info("Session indexer starting (poll=%ds, ollama=%s, qdrant=%s, model=%s)",
                POLL_INTERVAL, OLLAMA_URL, QDRANT_URL, EMBED_MODEL)
    while True:
        try:
            await run_index_cycle()
        except Exception as e:
            logger.error("Index cycle failed: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
