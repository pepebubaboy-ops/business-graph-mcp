from __future__ import annotations

from typing import Protocol

from business_graph_core.models import BusinessNode, BusinessRelation, GraphSummary


class GraphRepository(Protocol):
    """Workspace-scoped graph repository contract."""

    def upsert_node(self, node: BusinessNode) -> None:
        """Insert or replace a business node."""

    def upsert_relation(self, relation: BusinessRelation) -> None:
        """Insert or replace a business relation."""

    def get_node(self, workspace_id: str, node_id: str) -> BusinessNode | None:
        """Return a node only when it belongs to the requested workspace."""

    def get_relation(
        self,
        workspace_id: str,
        relation_id: str,
    ) -> BusinessRelation | None:
        """Return a relation only when it belongs to the requested workspace."""

    def list_nodes(self, workspace_id: str = "default") -> list[BusinessNode]:
        """List nodes registered in a workspace."""

    def list_relations(self, workspace_id: str = "default") -> list[BusinessRelation]:
        """List relations registered in a workspace."""

    def get_summary(self, workspace_id: str = "default") -> GraphSummary:
        """Return aggregate graph counts for a workspace."""
