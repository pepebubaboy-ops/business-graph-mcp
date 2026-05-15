import pytest

from business_graph_core.models import (
    BusinessRelation,
    EvidenceQuality,
    EvidenceRef,
    RelationStatus,
    RelationType,
)


def test_confirmed_relation_requires_evidence():
    with pytest.raises(ValueError):
        BusinessRelation(
            from_id="metric:a",
            to_id="metric:b",
            type=RelationType.DRIVES,
            status=RelationStatus.CONFIRMED,
        )


def test_confirmed_relation_with_evidence_is_valid():
    relation = BusinessRelation(
        from_id="metric:a",
        to_id="metric:b",
        type=RelationType.DRIVES,
        status=RelationStatus.CONFIRMED,
        evidence_refs=[EvidenceRef(method=EvidenceQuality.EXPLICIT_RULE, source_name="rules.xlsx")],
    )
    assert relation.status == RelationStatus.CONFIRMED
