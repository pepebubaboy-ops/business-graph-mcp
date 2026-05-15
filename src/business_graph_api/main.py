from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException

from business_graph_core.models import AnalysisRequest, AnalysisResult, GraphSummary
from business_graph_core.services.analyzer import AnalyzerService
from business_graph_core.settings import settings

app = FastAPI(title="Business Graph MCP API", version="0.1.0")
_service = AnalyzerService()


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "business-graph-mcp"}


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
