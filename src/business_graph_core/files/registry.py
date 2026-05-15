from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from business_graph_core.models import FileRecord, FileStatus


class FileRegistryLookupError(FileNotFoundError):
    """Raised when a file is missing from the requested workspace."""


class FileRegistry(Protocol):
    def register_file(
        self,
        *,
        workspace_id: str,
        original_filename: str,
        content_type: str | None,
        size_bytes: int,
        sha256: str,
        storage_path: str | Path,
        status: FileStatus = FileStatus.STORED,
        metadata: dict[str, Any] | None = None,
        file_id: str | None = None,
    ) -> FileRecord:
        """Register a stored file and return its workspace-scoped record."""

    def get_file(self, workspace_id: str, file_id: str) -> FileRecord:
        """Return a file record only when it belongs to the requested workspace."""

    def list_files(self, workspace_id: str) -> list[FileRecord]:
        """List files registered in a workspace."""


class InMemoryFileRegistry:
    """Workspace-scoped file registry for tests and local MVP runs."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, FileRecord]] = {}

    def register_file(
        self,
        *,
        workspace_id: str,
        original_filename: str,
        content_type: str | None,
        size_bytes: int,
        sha256: str,
        storage_path: str | Path,
        status: FileStatus = FileStatus.STORED,
        metadata: dict[str, Any] | None = None,
        file_id: str | None = None,
    ) -> FileRecord:
        record = FileRecord(
            file_id=file_id or f"file:{uuid4().hex}",
            workspace_id=workspace_id,
            original_filename=original_filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            storage_path=str(storage_path),
            status=status,
            metadata=metadata or {},
        )
        self._records.setdefault(workspace_id, {})[record.file_id] = record
        return record

    def get_file(self, workspace_id: str, file_id: str) -> FileRecord:
        try:
            return self._records[workspace_id][file_id]
        except KeyError as exc:
            raise FileRegistryLookupError(
                f"File {file_id!r} is not registered in workspace {workspace_id!r}."
            ) from exc

    def list_files(self, workspace_id: str) -> list[FileRecord]:
        return list(self._records.get(workspace_id, {}).values())
