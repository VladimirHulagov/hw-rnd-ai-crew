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
