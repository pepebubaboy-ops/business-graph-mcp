from __future__ import annotations

from pathlib import Path

from business_graph_core.extractors.explicit_rules import ExplicitRulesExtractor
from business_graph_core.files.registry import FileRegistry, InMemoryFileRegistry
from business_graph_core.graph.memory_repo import InMemoryGraphRepository
from business_graph_core.graph.repository import GraphRepository
from business_graph_core.models import AnalysisRequest, AnalysisResult, FileStatus
from business_graph_core.parsers.excel import ExcelParser

EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xltx", ".xltm"}


class AnalyzerService:
    """Analyze files and persist extracted business relations."""

    def __init__(
        self,
        *,
        graph_repo: GraphRepository | None = None,
        file_registry: FileRegistry | None = None,
        excel_parser: ExcelParser | None = None,
        explicit_rules_extractor: ExplicitRulesExtractor | None = None,
    ) -> None:
        self.graph_repo = graph_repo or InMemoryGraphRepository()
        self.file_registry = file_registry or InMemoryFileRegistry()
        self.excel_parser = excel_parser or ExcelParser()
        self.explicit_rules_extractor = explicit_rules_extractor or ExplicitRulesExtractor()

    def analyze_local_files(self, request: AnalysisRequest) -> AnalysisResult:
        warnings: list[str] = []
        for raw_path in request.local_paths:
            path = Path(raw_path)
            if path.suffix.lower() in EXCEL_EXTENSIONS:
                self._analyze_excel_path(path, workspace_id=request.workspace_id)
            else:
                warnings.append(f"Unsupported file type for MVP: {path.name}")

        return self._build_result(request.workspace_id, warnings=warnings)

    def analyze_registered_files(self, request: AnalysisRequest) -> AnalysisResult:
        warnings: list[str] = []
        if not request.file_ids:
            warnings.append("No file_ids provided for registered-file analysis.")
            return self._build_result(request.workspace_id, warnings=warnings)

        for file_id in request.file_ids:
            record = self.file_registry.get_file(request.workspace_id, file_id)
            path = Path(record.storage_path)
            if path.suffix.lower() in EXCEL_EXTENSIONS:
                self._analyze_excel_path(
                    path,
                    workspace_id=request.workspace_id,
                    file_id=record.file_id,
                )
                record.status = FileStatus.PARSED
            else:
                warnings.append(f"Unsupported file type for MVP: {record.original_filename}")

        return self._build_result(request.workspace_id, warnings=warnings)

    def _analyze_excel_path(
        self,
        path: Path,
        *,
        workspace_id: str,
        file_id: str | None = None,
    ) -> None:
        parsed = self.excel_parser.parse(path, file_id=file_id)
        extracted = self.explicit_rules_extractor.extract(
            parsed,
            workspace_id=workspace_id,
        )
        for node in extracted.nodes:
            self.graph_repo.upsert_node(node)
        for relation in extracted.relations:
            self.graph_repo.upsert_relation(relation)

    def _build_result(self, workspace_id: str, *, warnings: list[str]) -> AnalysisResult:
        summary = self.graph_repo.get_summary(workspace_id=workspace_id)
        return AnalysisResult(
            workspace_id=workspace_id,
            confirmed_relations_count=summary.confirmed_relation_count,
            candidate_relations_count=summary.candidate_relation_count,
            detected_metrics=summary.node_counts_by_type.get("Metric", 0),
            warnings=warnings,
            summary=summary,
        )
