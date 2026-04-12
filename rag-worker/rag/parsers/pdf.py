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
