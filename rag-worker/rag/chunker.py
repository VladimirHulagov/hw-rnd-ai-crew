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
