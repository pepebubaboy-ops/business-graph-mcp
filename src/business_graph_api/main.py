from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile

from business_graph_core.files.registry import FileRegistryLookupError, InMemoryFileRegistry
from business_graph_core.files.storage import LocalFileStorage
from business_graph_core.models import (
    AnalysisRequest,
    AnalysisResult,
    FileRecord,
    FileStatus,
    FileUploadResult,
    GraphSummary,
)
from business_graph_core.services.analyzer import AnalyzerService
from business_graph_core.settings import settings

app = FastAPI(title="Business Graph MCP API", version="0.1.0")
_file_registry = InMemoryFileRegistry()
_file_storage = LocalFileStorage(settings.file_storage_root)
_service = AnalyzerService(file_registry=_file_registry)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "business-graph-mcp"}


@app.post("/api/v1/files", response_model=FileUploadResult)
def upload_file(
    file: Annotated[UploadFile, File()],
    workspace_id: str = "default",
    _: None = Depends(require_api_key),
) -> FileUploadResult:
    stored = _file_storage.save(
        workspace_id=workspace_id,
        original_filename=file.filename,
        content_type=file.content_type,
        content=file.file,
    )
    record = _service.file_registry.register_file(
        file_id=stored.file_id,
        workspace_id=workspace_id,
        original_filename=stored.original_filename,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        storage_path=stored.storage_path,
        status=FileStatus.STORED,
    )
    return FileUploadResult(
        file_id=record.file_id,
        workspace_id=record.workspace_id,
        original_filename=record.original_filename,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        status=record.status,
    )


@app.get("/api/v1/files", response_model=list[FileRecord])
def list_files(
    workspace_id: str = "default",
    _: None = Depends(require_api_key),
) -> list[FileRecord]:
    return _service.file_registry.list_files(workspace_id)


@app.post("/api/v1/analyses/files", response_model=AnalysisResult)
def analyze_registered_files(
    request: AnalysisRequest,
    _: None = Depends(require_api_key),
) -> AnalysisResult:
    try:
        return _service.analyze_registered_files(request)
    except FileRegistryLookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/analyses/local-files", response_model=AnalysisResult)
def analyze_local_files(
    request: AnalysisRequest,
    _: None = Depends(require_api_key),
) -> AnalysisResult:
    return _service.analyze_local_files(request)


@app.get("/api/v1/graph/summary", response_model=GraphSummary)
def graph_summary(
    workspace_id: str = "default",
    _: None = Depends(require_api_key),
) -> GraphSummary:
    return _service.graph_repo.get_summary(workspace_id=workspace_id)


@app.get("/api/v1/relations")
def relations(
    workspace_id: str = "default",
    _: None = Depends(require_api_key),
):
    return _service.graph_repo.list_relations(workspace_id=workspace_id)
