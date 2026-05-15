from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ParsedCell(BaseModel):
    sheet: str
    address: str
    value: Any = None
    formula: str | None = None


class ParsedSheet(BaseModel):
    name: str
    cells: list[ParsedCell] = Field(default_factory=list)
    dependency_rule_rows: list[dict[str, Any]] = Field(default_factory=list)


class ParsedFile(BaseModel):
    file_id: str
    source_name: str
    source_type: str
    sheets: list[ParsedSheet] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileParser(Protocol):
    def parse(self, path: Path, *, file_id: str | None = None) -> ParsedFile:
        """Parse a file into structured representation."""
