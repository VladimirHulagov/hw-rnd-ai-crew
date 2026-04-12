from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Any


@dataclass
class ParsedDocument:
    pages: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


class ParserBase(ABC):
    extensions: List[str] = []

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument:
        ...
