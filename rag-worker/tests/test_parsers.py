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

        def side_effect(cmd, **kwargs):
            out_dir = Path(cmd[cmd.index("--output-dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "test.md").write_text("Page 1 content")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = side_effect

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
