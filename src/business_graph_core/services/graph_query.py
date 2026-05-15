from __future__ import annotations

from collections import deque

from business_graph_core.graph.repository import GraphRepository
from business_graph_core.models import (
    BusinessNode,
    BusinessRelation,
    GraphPath,
    GraphPathStep,
    PathSearchRequest,
    RelationExplanation,
    RelationSearchRequest,
    RelationSearchResult,
    RelationStatus,
)

DEFAULT_SEARCH_STATUSES = {
    RelationStatus.CONFIRMED,
    RelationStatus.CANDIDATE,
}

STATUS_PRIORITY = {
    RelationStatus.CONFIRMED: 0,
    RelationStatus.CANDIDATE: 1,
    RelationStatus.REJECTED: 2,
}


class GraphQueryService:
    """Read-only business graph relationship search and explanation service."""

    def __init__(self, graph_repo: GraphRepository) -> None:
        self.graph_repo = graph_repo

    def find_relations(self, request: RelationSearchRequest) -> RelationSearchResult:
        relations = [
            relation
            for relation in self.graph_repo.list_relations(request.workspace_id)
            if self._relation_allowed(
                relation,
                statuses=request.statuses,
                include_rejected=request.include_rejected,
            )
        ]

        if request.relation_types:
            relation_types = set(request.relation_types)
            relations = [relation for relation in relations if relation.type in relation_types]

        if request.from_id:
            relations = [relation for relation in relations if relation.from_id == request.from_id]

        if request.to_id:
            relations = [relation for relation in relations if relation.to_id == request.to_id]

        if request.query:
            query = self._normalize_search_text(request.query)
            nodes_by_id = self._nodes_by_id(request.workspace_id)
            relations = [
                relation
                for relation in relations
                if self._matches_relation_query(relation, query, nodes_by_id)
            ]

        relations = self._sort_relations(relations)[: request.limit]
        nodes = self._relation_nodes(request.workspace_id, relations)
        return RelationSearchResult(
            workspace_id=request.workspace_id,
            relations=relations,
            nodes=nodes,
            count=len(relations),
        )

    def explain_relation(
        self,
        workspace_id: str,
        relation_id: str,
    ) -> RelationExplanation:
        relation = self.graph_repo.get_relation(workspace_id, relation_id)
        if relation is None:
            raise LookupError(
                f"Relation {relation_id!r} is not registered in workspace {workspace_id!r}."
            )

        from_node = self.graph_repo.get_node(workspace_id, relation.from_id)
        to_node = self.graph_repo.get_node(workspace_id, relation.to_id)
        if from_node is None or to_node is None:
            raise LookupError(
                f"Relation {relation_id!r} has missing endpoint nodes in workspace "
                f"{workspace_id!r}."
            )

        explanation = relation.explanation or (
            f"{from_node.name} has relation {relation.type.value} to {to_node.name}."
        )
        return RelationExplanation(
            workspace_id=workspace_id,
            relation=relation,
            from_node=from_node,
            to_node=to_node,
            evidence=relation.evidence_refs,
            explanation=explanation,
        )

    def explain_path(self, request: PathSearchRequest) -> GraphPath | None:
        if request.from_id == request.to_id:
            node = self.graph_repo.get_node(request.workspace_id, request.from_id)
            if node is None:
                return None
            return GraphPath(
                workspace_id=request.workspace_id,
                from_id=request.from_id,
                to_id=request.to_id,
                steps=[],
                depth=0,
                evidence_refs=[],
                confidence=1.0,
            )

        adjacency = self._adjacency(request)
        queue = deque([(request.from_id, [])])
        visited = {request.from_id}

        while queue:
            node_id, path_relations = queue.popleft()
            if len(path_relations) >= request.max_depth:
                continue

            for relation in adjacency.get(node_id, []):
                if relation.to_id in visited:
                    continue

                next_path = [*path_relations, relation]
                if relation.to_id == request.to_id:
                    return self._build_path(request, next_path)

                visited.add(relation.to_id)
                queue.append((relation.to_id, next_path))

        return None

    def _adjacency(
        self,
        request: PathSearchRequest,
    ) -> dict[str, list[BusinessRelation]]:
        adjacency: dict[str, list[BusinessRelation]] = {}
        for relation in self.graph_repo.list_relations(request.workspace_id):
            if self._relation_allowed(
                relation,
                statuses=request.statuses,
                include_rejected=request.include_rejected,
            ):
                adjacency.setdefault(relation.from_id, []).append(relation)

        for relations in adjacency.values():
            relations.sort(
                key=lambda relation: (
                    STATUS_PRIORITY[relation.status],
                    -relation.confidence,
                    relation.to_id,
                )
            )
        return adjacency

    def _build_path(
        self,
        request: PathSearchRequest,
        relations: list[BusinessRelation],
    ) -> GraphPath | None:
        steps: list[GraphPathStep] = []
        evidence_refs = []
        confidences = []

        for relation in relations:
            from_node = self.graph_repo.get_node(request.workspace_id, relation.from_id)
            to_node = self.graph_repo.get_node(request.workspace_id, relation.to_id)
            if from_node is None or to_node is None:
                return None
            steps.append(
                GraphPathStep(
                    from_node=from_node,
                    relation=relation,
                    to_node=to_node,
                )
            )
            evidence_refs.extend(relation.evidence_refs)
            confidences.append(relation.confidence)

        return GraphPath(
            workspace_id=request.workspace_id,
            from_id=request.from_id,
            to_id=request.to_id,
            steps=steps,
            depth=len(steps),
            evidence_refs=evidence_refs,
            confidence=min(confidences) if confidences else 0.0,
        )

    def _relation_allowed(
        self,
        relation: BusinessRelation,
        *,
        statuses: list[RelationStatus],
        include_rejected: bool,
    ) -> bool:
        allowed = set(statuses) if statuses else set(DEFAULT_SEARCH_STATUSES)
        if include_rejected and not statuses:
            allowed.add(RelationStatus.REJECTED)
        if not include_rejected:
            allowed.discard(RelationStatus.REJECTED)
        return relation.status in allowed

    def _matches_relation_query(
        self,
        relation: BusinessRelation,
        query: str,
        nodes_by_id: dict[str, BusinessNode],
    ) -> bool:
        relation_text = self._normalize_search_text(
            " ".join(
                filter(
                    None,
                    [
                        relation.id,
                        relation.from_id,
                        relation.to_id,
                        relation.type.value,
                        relation.status.value,
                        relation.explanation,
                    ],
                )
            )
        )
        if query in relation_text:
            return True

        return self._matches_node_query(nodes_by_id.get(relation.from_id), query) or (
            self._matches_node_query(nodes_by_id.get(relation.to_id), query)
        )

    def _matches_node_query(self, node: BusinessNode | None, query: str) -> bool:
        if node is None:
            return False
        node_text = self._normalize_search_text(" ".join([node.id, node.name, *node.aliases]))
        return query in node_text

    def _normalize_search_text(self, value: str) -> str:
        return value.casefold().replace("_", " ").replace("-", " ")

    def _relation_nodes(
        self,
        workspace_id: str,
        relations: list[BusinessRelation],
    ) -> list[BusinessNode]:
        nodes_by_id = self._nodes_by_id(workspace_id)
        node_ids = {relation.from_id for relation in relations} | {
            relation.to_id for relation in relations
        }
        return [nodes_by_id[node_id] for node_id in sorted(node_ids) if node_id in nodes_by_id]

    def _nodes_by_id(self, workspace_id: str) -> dict[str, BusinessNode]:
        return {node.id: node for node in self.graph_repo.list_nodes(workspace_id)}

    def _sort_relations(
        self,
        relations: list[BusinessRelation],
    ) -> list[BusinessRelation]:
        return sorted(
            relations,
            key=lambda relation: (
                STATUS_PRIORITY[relation.status],
                relation.from_id,
                relation.to_id,
                relation.type.value,
            ),
        )
