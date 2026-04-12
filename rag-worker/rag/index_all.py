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
