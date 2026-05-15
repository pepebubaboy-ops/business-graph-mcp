from __future__ import annotations

from collections import Counter

from business_graph_core.models import BusinessNode, BusinessRelation, GraphSummary, RelationStatus


class InMemoryGraphRepository:
    """Simple in-memory graph repository for tests and MVP smoke runs."""

    def __init__(self) -> None:
        self.nodes: dict[str, BusinessNode] = {}
        self.relations: dict[str, BusinessRelation] = {}

    def upsert_node(self, node: BusinessNode) -> None:
        self.nodes[self._node_key(node.workspace_id, node.id)] = node

    def upsert_relation(self, relation: BusinessRelation) -> None:
        key_parts = (
            relation.workspace_id,
            relation.from_id,
            relation.type,
            relation.to_id,
            relation.status,
        )
        key = ":".join(key_parts)
        self.relations[key] = relation

    def get_node(self, workspace_id: str, node_id: str) -> BusinessNode | None:
        return self.nodes.get(self._node_key(workspace_id, node_id))

    def get_relation(
        self,
        workspace_id: str,
        relation_id: str,
    ) -> BusinessRelation | None:
        for relation in self.list_relations(workspace_id=workspace_id):
            if relation.id == relation_id:
                return relation
        return None

    def list_nodes(self, workspace_id: str = "default") -> list[BusinessNode]:
        return [n for n in self.nodes.values() if n.workspace_id == workspace_id]

    def list_relations(self, workspace_id: str = "default") -> list[BusinessRelation]:
        return [r for r in self.relations.values() if r.workspace_id == workspace_id]

    def get_summary(self, workspace_id: str = "default") -> GraphSummary:
        nodes = self.list_nodes(workspace_id=workspace_id)
        relations = self.list_relations(workspace_id=workspace_id)
        node_counter = Counter(node.type.value for node in nodes)
        relation_counter = Counter(relation.type.value for relation in relations)
        return GraphSummary(
            workspace_id=workspace_id,
            node_count=len(nodes),
            relation_count=len(relations),
            confirmed_relation_count=sum(r.status == RelationStatus.CONFIRMED for r in relations),
            candidate_relation_count=sum(r.status == RelationStatus.CANDIDATE for r in relations),
            rejected_relation_count=sum(r.status == RelationStatus.REJECTED for r in relations),
            node_counts_by_type=dict(node_counter),
            relation_counts_by_type=dict(relation_counter),
        )

    def _node_key(self, workspace_id: str, node_id: str) -> str:
        return f"{workspace_id}:{node_id}"
