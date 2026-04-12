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
