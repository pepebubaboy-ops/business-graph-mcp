from __future__ import annotations

from collections import Counter

from business_graph_core.models import BusinessNode, BusinessRelation, GraphSummary, RelationStatus


class InMemoryGraphRepository:
    """Simple in-memory graph repository for tests and MVP smoke runs."""

    def __init__(self) -> None:
        self.nodes: dict[str, BusinessNode] = {}
        self.relations: dict[str, BusinessRelation] = {}

    def upsert_node(self, node: BusinessNode) -> None:
        self.nodes[node.id] = node

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

    def list_relations(self, *, workspace_id: str = "default") -> list[BusinessRelation]:
        return [r for r in self.relations.values() if r.workspace_id == workspace_id]

    def get_summary(self, *, workspace_id: str = "default") -> GraphSummary:
        nodes = [n for n in self.nodes.values() if n.workspace_id == workspace_id]
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
