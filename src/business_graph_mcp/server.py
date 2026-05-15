from __future__ import annotations

from typing import Any

from business_graph_core.models import AnalysisRequest
from business_graph_core.services.analyzer import AnalyzerService

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install the 'mcp' package to run the MCP server.") from exc

mcp = FastMCP("business-graph-mcp")
_service = AnalyzerService()


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
def business_find_relations(workspace_id: str = "default") -> list[dict[str, Any]]:
    """List relations for a workspace."""
    return [
        relation.model_dump(mode="json")
        for relation in _service.graph_repo.list_relations(workspace_id=workspace_id)
    ]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
