from business_graph_core.models import AnalysisRequest
from business_graph_core.services.analyzer import AnalyzerService


def test_analyzer_extracts_rules_from_sample_xlsx():
    service = AnalyzerService()
    result = service.analyze_local_files(
        AnalysisRequest(
            workspace_id="test",
            local_paths=["examples/sample-data/dependency_rules.xlsx"],
        )
    )

    assert result.confirmed_relations_count > 0
    assert result.summary.node_count > 0
