from pathlib import Path
from typing import Dict, List, Optional, Type

from .base import ParserBase
from .pdf import PdfParser
from .plaintext import PlaintextParser

_PARSERS: Dict[str, Type[ParserBase]] = {}


def _register(parser_cls: Type[ParserBase]) -> None:
    for ext in parser_cls.extensions:
        _PARSERS[ext.lower()] = parser_cls


_register(PlaintextParser)
_register(PdfParser)


def get_parser(ext: str) -> Optional[ParserBase]:
    cls = _PARSERS.get(ext.lower())
    return cls() if cls else None


def supported_extensions() -> List[str]:
    return list(_PARSERS.keys())
