from __future__ import annotations

from pathlib import Path

from business_graph_core.extractors.explicit_rules import ExplicitRulesExtractor
from business_graph_core.graph.memory_repo import InMemoryGraphRepository
from business_graph_core.models import AnalysisRequest, AnalysisResult
from business_graph_core.parsers.excel import ExcelParser


class AnalyzerService:
    """Analyze files and persist extracted business relations."""

    def __init__(
        self,
        *,
        graph_repo: InMemoryGraphRepository | None = None,
        excel_parser: ExcelParser | None = None,
        explicit_rules_extractor: ExplicitRulesExtractor | None = None,
    ) -> None:
        self.graph_repo = graph_repo or InMemoryGraphRepository()
        self.excel_parser = excel_parser or ExcelParser()
        self.explicit_rules_extractor = explicit_rules_extractor or ExplicitRulesExtractor()

    def analyze_local_files(self, request: AnalysisRequest) -> AnalysisResult:
        warnings: list[str] = []
        for raw_path in request.local_paths:
            path = Path(raw_path)
            if path.suffix.lower() in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
                parsed = self.excel_parser.parse(path)
                extracted = self.explicit_rules_extractor.extract(
                    parsed,
                    workspace_id=request.workspace_id,
                )
                for node in extracted.nodes:
                    self.graph_repo.upsert_node(node)
                for relation in extracted.relations:
                    self.graph_repo.upsert_relation(relation)
            else:
                warnings.append(f"Unsupported file type for MVP: {path.name}")

        summary = self.graph_repo.get_summary(workspace_id=request.workspace_id)
        return AnalysisResult(
            workspace_id=request.workspace_id,
            confirmed_relations_count=summary.confirmed_relation_count,
            candidate_relations_count=summary.candidate_relation_count,
            detected_metrics=summary.node_counts_by_type.get("Metric", 0),
            warnings=warnings,
            summary=summary,
        )
