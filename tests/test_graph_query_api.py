import pytest
from fastapi.testclient import TestClient

from business_graph_api import main as api_main
from business_graph_core.graph.memory_repo import InMemoryGraphRepository
from business_graph_core.models import (
    BusinessNode,
    BusinessRelation,
    EvidenceQuality,
    EvidenceRef,
    NodeType,
    RelationStatus,
    RelationType,
)
from business_graph_core.services.analyzer import AnalyzerService
from business_graph_core.settings import settings

API_HEADERS = {"X-API-Key": settings.api_key}


def metric(code: str) -> BusinessNode:
    return BusinessNode(
        id=f"metric:{code}",
        type=NodeType.METRIC,
        name=code,
    )


def confirmed_relation(relation_id: str, source: str, target: str) -> BusinessRelation:
    return BusinessRelation(
        id=relation_id,
        from_id=f"metric:{source}",
        to_id=f"metric:{target}",
        type=RelationType.DRIVES,
        status=RelationStatus.CONFIRMED,
        confidence=0.8,
        evidence_refs=[
            EvidenceRef(
                method=EvidenceQuality.EXPLICIT_RULE,
                source_name="rules.xlsx",
                quote_or_value=f"{source} affects {target}",
            )
        ],
        explanation=f"{source} affects {target}",
    )


@pytest.fixture
def graph_client(monkeypatch):
    repo = InMemoryGraphRepository()
    for node in [
        metric("volume"),
        metric("freight_cost"),
        metric("total_cost"),
        metric("gross_margin"),
    ]:
        repo.upsert_node(node)
    for relation in [
        confirmed_relation("rel:volume-freight", "volume", "freight_cost"),
        confirmed_relation("rel:freight-cost", "freight_cost", "total_cost"),
        confirmed_relation("rel:cost-margin", "total_cost", "gross_margin"),
    ]:
        repo.upsert_relation(relation)

    monkeypatch.setattr(api_main, "_service", AnalyzerService(graph_repo=repo))
    return TestClient(api_main.app)


def test_api_relation_search(graph_client):
    response = graph_client.post(
        "/api/v1/relations/search",
        headers=API_HEADERS,
        json={"query": "gross_margin"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["relations"][0]["id"] == "rel:cost-margin"
    assert "storage_path" not in str(payload)


def test_api_relation_explanation(graph_client):
    response = graph_client.get(
        "/api/v1/relations/rel:cost-margin/explain",
        headers=API_HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["relation"]["id"] == "rel:cost-margin"
    assert payload["from_node"]["id"] == "metric:total_cost"
    assert payload["to_node"]["id"] == "metric:gross_margin"
    assert payload["evidence"]


def test_api_path_explanation(graph_client):
    response = graph_client.post(
        "/api/v1/paths/explain",
        headers=API_HEADERS,
        json={
            "from_id": "metric:volume",
            "to_id": "metric:gross_margin",
            "max_depth": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["depth"] == 3
    assert [step["relation"]["id"] for step in payload["steps"]] == [
        "rel:volume-freight",
        "rel:freight-cost",
        "rel:cost-margin",
    ]


def test_api_relation_explanation_returns_404_for_missing_relation(graph_client):
    response = graph_client.get(
        "/api/v1/relations/missing/explain",
        headers=API_HEADERS,
    )

    assert response.status_code == 404
