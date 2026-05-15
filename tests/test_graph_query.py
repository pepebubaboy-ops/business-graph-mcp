from business_graph_core.graph.memory_repo import InMemoryGraphRepository
from business_graph_core.models import (
    BusinessNode,
    BusinessRelation,
    EvidenceQuality,
    EvidenceRef,
    NodeType,
    PathSearchRequest,
    RelationSearchRequest,
    RelationStatus,
    RelationType,
)
from business_graph_core.services.graph_query import GraphQueryService


def metric(code: str, *, workspace_id: str = "default", aliases: list[str] | None = None):
    return BusinessNode(
        id=f"metric:{code}",
        type=NodeType.METRIC,
        name=code,
        workspace_id=workspace_id,
        aliases=aliases or [],
    )


def relation(
    relation_id: str,
    source: str,
    target: str,
    *,
    workspace_id: str = "default",
    status: RelationStatus = RelationStatus.CONFIRMED,
    relation_type: RelationType = RelationType.DRIVES,
    confidence: float = 0.9,
):
    evidence_refs = []
    if status == RelationStatus.CONFIRMED:
        evidence_refs = [
            EvidenceRef(
                method=EvidenceQuality.EXPLICIT_RULE,
                source_name="rules.xlsx",
                quote_or_value=f"{source} affects {target}",
            )
        ]

    return BusinessRelation(
        id=relation_id,
        from_id=f"metric:{source}",
        to_id=f"metric:{target}",
        type=relation_type,
        workspace_id=workspace_id,
        status=status,
        confidence=confidence,
        evidence_refs=evidence_refs,
        explanation=f"{source} affects {target}",
    )


def build_repo() -> InMemoryGraphRepository:
    repo = InMemoryGraphRepository()
    for node in [
        metric("revenue", aliases=["sales"]),
        metric("orders"),
        metric("total_cost"),
        metric("gross_margin"),
        metric("volume"),
        metric("freight_cost"),
    ]:
        repo.upsert_node(node)

    for item in [
        relation("rel:orders-revenue", "orders", "revenue"),
        relation("rel:cost-margin", "total_cost", "gross_margin"),
        relation("rel:volume-freight", "volume", "freight_cost"),
        relation("rel:freight-cost", "freight_cost", "total_cost"),
        relation(
            "rel:candidate-revenue-cost",
            "revenue",
            "total_cost",
            status=RelationStatus.CANDIDATE,
        ),
        relation(
            "rel:rejected-orders-cost",
            "orders",
            "total_cost",
            status=RelationStatus.REJECTED,
        ),
    ]:
        repo.upsert_relation(item)
    return repo


def test_relation_search_by_node_name():
    service = GraphQueryService(build_repo())

    result = service.find_relations(RelationSearchRequest(query="gross margin"))

    relation_ids = {relation.id for relation in result.relations}
    assert relation_ids == {"rel:cost-margin"}
    assert result.count == 1
    assert {node.id for node in result.nodes} >= {
        "metric:total_cost",
        "metric:gross_margin",
    }


def test_relation_search_status_filters_rejected_by_default():
    service = GraphQueryService(build_repo())

    default_result = service.find_relations(RelationSearchRequest(query="cost"))
    default_statuses = {relation.status for relation in default_result.relations}
    assert RelationStatus.CONFIRMED in default_statuses
    assert RelationStatus.CANDIDATE in default_statuses
    assert RelationStatus.REJECTED not in default_statuses

    rejected_result = service.find_relations(
        RelationSearchRequest(
            query="orders",
            include_rejected=True,
            statuses=[RelationStatus.REJECTED],
        )
    )
    assert [relation.id for relation in rejected_result.relations] == ["rel:rejected-orders-cost"]


def test_relation_explanation_includes_nodes_and_evidence():
    service = GraphQueryService(build_repo())

    explanation = service.explain_relation("default", "rel:cost-margin")

    assert explanation.relation.id == "rel:cost-margin"
    assert explanation.from_node.id == "metric:total_cost"
    assert explanation.to_node.id == "metric:gross_margin"
    assert explanation.evidence
    assert explanation.explanation == "total_cost affects gross_margin"


def test_path_explanation_returns_ordered_steps():
    service = GraphQueryService(build_repo())

    path = service.explain_path(
        PathSearchRequest(
            from_id="metric:volume",
            to_id="metric:gross_margin",
            max_depth=4,
        )
    )

    assert path is not None
    assert path.depth == 3
    assert [step.relation.id for step in path.steps] == [
        "rel:volume-freight",
        "rel:freight-cost",
        "rel:cost-margin",
    ]
    assert path.confidence == 0.9


def test_graph_query_respects_workspace_boundaries():
    repo = InMemoryGraphRepository()
    repo.upsert_node(metric("revenue", workspace_id="workspace-a"))
    repo.upsert_node(metric("revenue", workspace_id="workspace-b"))
    repo.upsert_node(metric("orders", workspace_id="workspace-a"))
    repo.upsert_node(metric("orders", workspace_id="workspace-b"))
    repo.upsert_relation(relation("rel:a", "orders", "revenue", workspace_id="workspace-a"))
    repo.upsert_relation(relation("rel:b", "orders", "revenue", workspace_id="workspace-b"))

    service = GraphQueryService(repo)
    result = service.find_relations(
        RelationSearchRequest(workspace_id="workspace-a", query="revenue")
    )

    assert [relation.id for relation in result.relations] == ["rel:a"]
    assert service.explain_relation("workspace-a", "rel:a").relation.id == "rel:a"
    assert service.explain_relation("workspace-b", "rel:b").relation.id == "rel:b"
