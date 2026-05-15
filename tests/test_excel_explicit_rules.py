from pathlib import Path

from business_graph_core.extractors.explicit_rules import ExplicitRulesExtractor
from business_graph_core.models import RelationStatus
from business_graph_core.parsers.excel import ExcelParser


def test_dependency_rules_extract_confirmed_relations():
    path = Path("examples/sample-data/dependency_rules.xlsx")
    parsed = ExcelParser().parse(path, file_id="file:sample-rules")
    extracted = ExplicitRulesExtractor().extract(parsed, workspace_id="test")

    assert extracted.nodes
    assert extracted.relations
    assert all(r.status == RelationStatus.CONFIRMED for r in extracted.relations)
    assert all(r.evidence_refs for r in extracted.relations)
