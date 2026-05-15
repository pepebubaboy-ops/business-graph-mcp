from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.relation_memory_engine import RelationMemoryEngine  # noqa: E402


try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only when MCP dependency is missing.
    raise RuntimeError("The 'mcp' package is required to run backend.mcp_server.") from exc


mcp = FastMCP("relation-memory")
_engine: RelationMemoryEngine | None = None


def engine() -> RelationMemoryEngine:
    global _engine
    if _engine is None:
        _engine = RelationMemoryEngine()
    return _engine


@mcp.tool()
def relation_memory_analyze_files(
    workspace_root: str,
    paths: list[str],
    domain: str = "generic",
) -> dict[str, Any]:
    """Analyze local files under workspace_root and persist explicit relations to Neo4j."""

    return engine().analyze_files(workspace_root=workspace_root, paths=paths, domain=domain)


@mcp.tool()
def relation_memory_analyze_text(
    workspace_root: str,
    text: str,
    source_name: str = "cowork_text",
) -> dict[str, Any]:
    """Analyze free text and persist explicit relation statements to Neo4j."""

    return engine().analyze_text(workspace_root=workspace_root, text=text, source_name=source_name)


@mcp.tool()
def relation_memory_list_candidates(session_id: str) -> dict[str, Any]:
    """List pending relation candidates for a session."""

    return engine().list_candidates(session_id)


@mcp.tool()
def relation_memory_approve_candidate(session_id: str, candidate_id: str) -> dict[str, Any]:
    """Approve a pending relation candidate and save it to Neo4j."""

    return engine().approve_candidate(session_id, candidate_id)


@mcp.tool()
def relation_memory_reject_candidate(session_id: str, candidate_id: str) -> dict[str, Any]:
    """Reject a pending relation candidate without saving it to Neo4j."""

    return engine().reject_candidate(session_id, candidate_id)


@mcp.tool()
def relation_memory_list_memory(limit: int = 100) -> dict[str, Any]:
    """List confirmed relations stored in Neo4j memory."""

    return engine().list_memory(limit=limit)


@mcp.tool()
def relation_memory_export_review(
    session_id: str,
    workspace_root: str,
    output_dir: str = "output",
) -> dict[str, str]:
    """Export relations_review.xlsx and evidence_report.md for a session."""

    return engine().export_review(
        session_id=session_id, workspace_root=workspace_root, output_dir=output_dir
    )


@mcp.tool()
def relation_memory_healthcheck() -> dict[str, Any]:
    """Return dependency and configuration status for the relation memory MCP server."""

    return engine().healthcheck()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
