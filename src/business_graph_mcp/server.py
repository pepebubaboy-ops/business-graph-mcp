from __future__ import annotations

from typing import Any

from business_graph_core.models import (
    AnalysisRequest,
    PathSearchRequest,
    RelationSearchRequest,
    RelationStatus,
    RelationType,
)
from business_graph_core.services.analyzer import AnalyzerService
from business_graph_core.services.graph_query import GraphQueryService

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install the 'mcp' package to run the MCP server.") from exc

mcp = FastMCP("business-graph-mcp")
_service = AnalyzerService()
_graph_query_service = GraphQueryService(_service.graph_repo)


@mcp.tool()
def business_healthcheck() -> dict[str, Any]:
    """Return MCP server health."""
    return {"status": "ok", "service": "business-graph-mcp"}


@mcp.tool()
def business_analyze_files(
    local_paths: list[str],
    workspace_id: str = "default",
    objective: str = "Find business relationships in provided files.",
    domain_hint: str = "generic",
) -> dict[str, Any]:
    """Analyze local files for business relationships. Local dev only."""
    result = _service.analyze_local_files(
        AnalysisRequest(
            workspace_id=workspace_id,
            local_paths=local_paths,
            objective=objective,
            domain_hint=domain_hint,
        )
    )
    return result.model_dump(mode="json")


@mcp.tool()
def business_analyze_registered_files(
    file_ids: list[str],
    workspace_id: str = "default",
    objective: str = "Find business relationships in provided files.",
    domain_hint: str = "generic",
) -> dict[str, Any]:
    """Analyze files that were already registered in the workspace file registry."""
    result = _service.analyze_registered_files(
        AnalysisRequest(
            workspace_id=workspace_id,
            file_ids=file_ids,
            objective=objective,
            domain_hint=domain_hint,
        )
    )
    return result.model_dump(mode="json")


@mcp.tool()
def business_get_graph_summary(workspace_id: str = "default") -> dict[str, Any]:
    """Return graph summary for a workspace."""
    return _service.graph_repo.get_summary(workspace_id=workspace_id).model_dump(mode="json")


@mcp.tool()
def business_find_relations(
    workspace_id: str = "default",
    query: str | None = None,
    relation_types: list[str] | None = None,
    statuses: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Search business relations in a workspace."""
    parsed_statuses = _parse_relation_statuses(statuses or [])
    result = _graph_query_service.find_relations(
        RelationSearchRequest(
            workspace_id=workspace_id,
            query=query,
            relation_types=_parse_relation_types(relation_types or []),
            statuses=parsed_statuses,
            include_rejected=RelationStatus.REJECTED in parsed_statuses,
            limit=limit,
        )
    )
    return result.model_dump(mode="json")


@mcp.tool()
def business_explain_relation(
    relation_id: str,
    workspace_id: str = "default",
) -> dict[str, Any]:
    """Explain a direct business relation."""
    return _graph_query_service.explain_relation(
        workspace_id,
        relation_id,
    ).model_dump(mode="json")


@mcp.tool()
def business_explain_path(
    from_id: str,
    to_id: str,
    workspace_id: str = "default",
    max_depth: int = 4,
) -> dict[str, Any] | None:
    """Explain a deterministic path between two business nodes."""
    result = _graph_query_service.explain_path(
        PathSearchRequest(
            workspace_id=workspace_id,
            from_id=from_id,
            to_id=to_id,
            max_depth=max_depth,
        )
    )
    if result is None:
        return None
    return result.model_dump(mode="json")


def _parse_relation_types(values: list[str]) -> list[RelationType]:
    return [RelationType(value.upper()) for value in values]


def _parse_relation_statuses(values: list[str]) -> list[RelationStatus]:
    return [RelationStatus(value.lower()) for value in values]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
