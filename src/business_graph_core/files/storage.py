from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import BinaryIO
from uuid import uuid4


@dataclass(frozen=True)
class StoredFile:
    file_id: str
    workspace_id: str
    original_filename: str
    content_type: str | None
    size_bytes: int
    sha256: str
    storage_path: Path


class LocalFileStorage:
    """Store uploaded files under workspace-scoped directories."""

    def __init__(self, root: Path | str = ".data/files") -> None:
        self.root = Path(root)

    def save(
        self,
        *,
        workspace_id: str,
        original_filename: str,
        content_type: str | None,
        content: BinaryIO,
        file_id: str | None = None,
    ) -> StoredFile:
        safe_workspace_id = self._safe_path_segment(workspace_id, fallback="default")
        safe_filename = self.sanitize_filename(original_filename)
        stored_file_id = file_id or f"file:{uuid4().hex}"
        safe_file_id = self._safe_path_segment(stored_file_id, fallback="file")

        destination_dir = self.root / safe_workspace_id / safe_file_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        storage_path = destination_dir / safe_filename

        digest = hashlib.sha256()
        size_bytes = 0
        with storage_path.open("wb") as output:
            for chunk in iter(lambda: content.read(1024 * 1024), b""):
                if not chunk:
                    continue
                size_bytes += len(chunk)
                digest.update(chunk)
                output.write(chunk)

        return StoredFile(
            file_id=stored_file_id,
            workspace_id=workspace_id,
            original_filename=safe_filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
            storage_path=storage_path,
        )

    def copy_from_path(
        self,
        *,
        workspace_id: str,
        source_path: Path,
        content_type: str | None = None,
        file_id: str | None = None,
    ) -> StoredFile:
        with source_path.open("rb") as source:
            return self.save(
                workspace_id=workspace_id,
                original_filename=source_path.name,
                content_type=content_type,
                content=source,
                file_id=file_id,
            )

    def clear(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def sanitize_filename(self, filename: str | None) -> str:
        raw_name = PurePath(filename or "upload.bin").name
        sanitized = "".join(
            character if character.isalnum() or character in {".", "-", "_"} else "_"
            for character in raw_name
        ).strip("._")
        return sanitized or "upload.bin"

    def _safe_path_segment(self, value: str, *, fallback: str) -> str:
        sanitized = "".join(
            character if character.isalnum() or character in {"-", "_"} else "_"
            for character in value
        ).strip("_")
        return sanitized or fallback
