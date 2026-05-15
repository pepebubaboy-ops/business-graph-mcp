from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from app.config import settings
from app.schemas.relation_memory import CandidateRelation, ConfirmedRelation
from app.services.relation_memory_candidates import relation_key
from app.services.relation_memory_ingestion import UploadedFilePayload, normalize_metric_code
from app.services.relation_memory_metric_map import display_metric_label
from app.services.relation_memory_neo4j import RelationMemoryNeo4jClient
from app.services.relation_memory_session import (
    RelationMemorySessionService,
    RelationMemorySessionState,
)


MCP_ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".txt", ".md", ".pdf"}
EXPLICIT_TEXT_MARKERS = (
    "drive",
    "drives",
    "driver",
    "increase",
    "increases",
    "decrease",
    "decreases",
    "reduce",
    "reduces",
    "depends on",
    "component",
    "part of",
    "lag",
    "влия",
    "драйвер",
    "увелич",
    "сниж",
    "сокращ",
    "привод",
    "завис",
    "компонент",
    "состав",
    "лаг",
)
UNCERTAIN_TEXT_MARKERS = (
    "may ",
    "might ",
    "could ",
    "possibly",
    "hypothesis",
    "correlation",
    "корреляц",
    "гипотез",
    "может ",
    "возможно",
    "предполож",
)


class RelationMemoryEngine:
    """MCP-facing facade over the existing relation memory session service."""

    def __init__(
        self,
        *,
        service: RelationMemorySessionService | None = None,
        graph_client: Any | None = None,
        max_file_size_mb: int | None = None,
    ):
        self.graph_client = graph_client or RelationMemoryNeo4jClient()
        self.service = service or RelationMemorySessionService(graph_client=self.graph_client)
        self.max_file_size_bytes = (
            int(max_file_size_mb or settings.RELATION_MEMORY_MCP_MAX_FILE_SIZE_MB) * 1024 * 1024
        )
        self.auto_saved_by_session: dict[str, list[dict[str, Any]]] = {}
        self.skipped_by_session: dict[str, list[dict[str, Any]]] = {}

    def analyze_files(
        self,
        *,
        workspace_root: str | Path | None,
        paths: list[str],
        domain: str = "generic",
    ) -> dict[str, Any]:
        root = self._workspace_root(workspace_root)
        payloads = [
            UploadedFilePayload(filename=str(path.relative_to(root)), content=path.read_bytes())
            for path in self._resolve_input_paths(root, paths)
        ]
        if not payloads:
            raise ValueError("No supported files were provided for relation memory analysis.")
        response = self.service.create_session(payloads)
        return self._finalize_analysis(response.session_id, root, domain=domain)

    def analyze_text(
        self,
        *,
        workspace_root: str | Path | None,
        text: str,
        source_name: str = "cowork_text",
    ) -> dict[str, Any]:
        root = self._workspace_root(workspace_root)
        if not str(text or "").strip():
            raise ValueError("Text must not be empty.")
        filename = self._safe_text_source_name(source_name)
        response = self.service.create_session(
            [UploadedFilePayload(filename=filename, content=text.encode("utf-8"))]
        )
        return self._finalize_analysis(response.session_id, root, domain="generic")

    def list_candidates(self, session_id: str) -> dict[str, Any]:
        state = self._state(session_id)
        return {
            "session_id": session_id,
            "candidate_relations": [
                self._candidate_payload(candidate, state)
                for candidate in state.pending_confirmations.values()
            ],
            "warnings": list(state.warnings),
        }

    def approve_candidate(self, session_id: str, candidate_id: str) -> dict[str, Any]:
        return self.service.confirm_candidate(session_id, candidate_id, "approve").model_dump()

    def reject_candidate(self, session_id: str, candidate_id: str) -> dict[str, Any]:
        return self.service.confirm_candidate(session_id, candidate_id, "reject").model_dump()

    def list_memory(self, limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(int(limit or 100), 1000))
        relations = self.service.list_memory_relations()[:limit]
        return {
            "relations": [relation.model_dump() for relation in relations],
            "limit": limit,
        }

    def export_review(
        self,
        *,
        session_id: str,
        workspace_root: str | Path | None,
        output_dir: str | Path = "output",
    ) -> dict[str, str]:
        root = self._workspace_root(workspace_root)
        output_path = self._resolve_output_dir(root, output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        state = self._state(session_id)
        review_path = output_path / "relations_review.xlsx"
        evidence_path = output_path / "evidence_report.md"
        auto_saved = self.auto_saved_by_session.get(session_id, [])
        candidates = [
            self._candidate_payload(candidate, state)
            for candidate in state.pending_confirmations.values()
        ]
        self._write_review_xlsx(review_path, auto_saved, candidates)
        self._write_evidence_report(evidence_path, auto_saved, candidates)
        return {
            "relations_review": str(review_path),
            "evidence_report": str(evidence_path),
        }

    def healthcheck(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "ok",
            "neo4j_connected": False,
            "llm_provider": settings.LLM_PROVIDER,
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.LLM_MODEL,
            "max_file_size_mb": settings.RELATION_MEMORY_MCP_MAX_FILE_SIZE_MB,
            "allowed_extensions": sorted(MCP_ALLOWED_EXTENSIONS),
            "warnings": [],
        }
        try:
            self.service.list_memory_relations()
            payload["neo4j_connected"] = True
        except Exception as exc:  # noqa: BLE001 - healthcheck must report dependency failures.
            payload["status"] = "degraded"
            payload["warnings"].append(f"Neo4j check failed: {exc}")
        return payload

    def _finalize_analysis(
        self, session_id: str, workspace_root: Path, *, domain: str
    ) -> dict[str, Any]:
        state = self._state(session_id)
        auto_saved, skipped = self._auto_save_explicit_relations(state)
        self.auto_saved_by_session[session_id] = auto_saved
        self.skipped_by_session[session_id] = skipped
        artifact_paths = self.export_review(
            session_id=session_id,
            workspace_root=workspace_root,
            output_dir="output",
        )
        candidates = [
            self._candidate_payload(candidate, state)
            for candidate in state.pending_confirmations.values()
        ]
        return {
            "session_id": session_id,
            "domain": domain,
            "auto_saved_relations": auto_saved,
            "candidate_relations": candidates,
            "skipped_relations": skipped,
            "evidence": [
                evidence
                for relation in [*auto_saved, *candidates]
                for evidence in relation.get("evidence", [])
            ],
            "warnings": list(state.warnings),
            "artifact_paths": artifact_paths,
        }

    def _auto_save_explicit_relations(
        self, state: RelationMemorySessionState
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        auto_saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for dependency in state.snapshot.dependencies:
            if self._is_explicit_dependency(dependency):
                saved = self._save_dependency_relation(state, dependency)
                if saved:
                    auto_saved.append(saved)
            elif dependency.get("source_metric_code") and dependency.get("target_metric_code"):
                skipped.append(
                    self._skipped_payload(
                        dependency, "dependency is inferred or lacks explicit evidence"
                    )
                )

        for candidate in list(state.pending_confirmations.values()):
            if self._is_explicit_text_candidate(candidate):
                saved = self._save_candidate_relation(state, candidate)
                if saved:
                    auto_saved.append(saved)
                if saved or relation_key(candidate) in state.confirmed_relation_keys:
                    state.pending_confirmations.pop(candidate.id, None)
            else:
                skipped.append(
                    self._skipped_payload(
                        candidate.model_dump(), "candidate requires manual review"
                    )
                )
        return auto_saved, skipped

    def _is_explicit_dependency(self, dependency: dict[str, Any]) -> bool:
        if str(dependency.get("source") or "") != "metadata":
            return False
        if not dependency.get("source_metric_code") or not dependency.get("target_metric_code"):
            return False
        return bool(str(dependency.get("reason") or "").strip())

    def _is_explicit_text_candidate(self, candidate: CandidateRelation) -> bool:
        if candidate.source != "llm_text":
            return False
        evidence_text = f"{candidate.evidence} {candidate.note}".strip()
        if len(evidence_text) < 10:
            return False
        lowered = evidence_text.lower()
        if any(marker in lowered for marker in UNCERTAIN_TEXT_MARKERS):
            return False
        if max(candidate.confidence, candidate.score) < 0.75:
            return False
        return any(marker in lowered for marker in EXPLICIT_TEXT_MARKERS)

    def _save_dependency_relation(
        self, state: RelationMemorySessionState, dependency: dict[str, Any]
    ) -> dict[str, Any] | None:
        if relation_key(dependency) in state.confirmed_relation_keys:
            return None
        evidence = self._dependency_evidence_payload(state, dependency)
        saved = self.service._graph_client().save_confirmed_relation(
            source_metric_code=str(dependency["source_metric_code"]),
            target_metric_code=str(dependency["target_metric_code"]),
            edge_type=str(dependency.get("edge_type") or "driver"),
            note=str(dependency.get("reason") or ""),
            source_session_id=state.id,
            evidence_text=evidence["reason"],
            evidence_type=evidence["evidence_type"],
            source_file=evidence["source_file"],
            source_sheet=evidence["source_sheet"],
            source_range=evidence["source_range"],
            confidence=float(dependency.get("strength") or 0.0),
            auto_saved=True,
            source="auto_saved",
        )
        relation = ConfirmedRelation(**saved)
        state.approved_relations.append(relation)
        state.confirmed_relation_keys.add(relation_key(dependency))
        return self._confirmed_payload(saved, evidence)

    def _save_candidate_relation(
        self, state: RelationMemorySessionState, candidate: CandidateRelation
    ) -> dict[str, Any] | None:
        if relation_key(candidate) in state.confirmed_relation_keys:
            return None
        evidence = self._candidate_evidence_payload(candidate, state)
        source_metric = state.metric_map.get(candidate.source_metric_code, {})
        target_metric = state.metric_map.get(candidate.target_metric_code, {})
        saved = self.service._graph_client().save_confirmed_relation(
            source_metric_code=candidate.source_metric_code,
            target_metric_code=candidate.target_metric_code,
            edge_type=candidate.edge_type,
            lag_period=candidate.lag_period,
            note=candidate.note or candidate.evidence,
            source_session_id=state.id,
            source_document_id=candidate.source_document_id,
            source_label=display_metric_label(candidate.source_metric_code, source_metric),
            target_label=display_metric_label(candidate.target_metric_code, target_metric),
            evidence_text=evidence["quote"] or evidence["reason"],
            evidence_type=evidence["evidence_type"],
            source_file=evidence["source_file"],
            source_sheet=evidence["source_sheet"],
            source_range=evidence["source_range"],
            confidence=max(candidate.confidence, candidate.score),
            auto_saved=True,
            source="auto_saved",
        )
        relation = ConfirmedRelation(**saved)
        state.approved_relations.append(relation)
        state.confirmed_relation_keys.add(relation_key(candidate))
        return self._confirmed_payload(saved, evidence)

    def _confirmed_payload(self, saved: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
        return {
            **saved,
            "evidence": [evidence],
            "status": "auto_saved",
        }

    def _candidate_payload(
        self, candidate: CandidateRelation, state: RelationMemorySessionState
    ) -> dict[str, Any]:
        return {
            **candidate.model_dump(),
            "status": "candidate",
            "evidence": [self._candidate_evidence_payload(candidate, state)],
        }

    def _candidate_evidence_payload(
        self, candidate: CandidateRelation, state: RelationMemorySessionState
    ) -> dict[str, Any]:
        filename = self._document_filename(state, candidate.source_document_id)
        source_sheet, source_range = self._sheet_range_from_evidence(candidate.evidence)
        return {
            "source_type": "text" if candidate.source == "llm_text" else "candidate",
            "source_file": filename,
            "source_sheet": source_sheet,
            "source_range": source_range,
            "quote": candidate.evidence,
            "reason": candidate.note or candidate.evidence,
            "evidence_type": candidate.evidence_type
            or ("text" if candidate.source == "llm_text" else "candidate"),
            "source_document_id": candidate.source_document_id,
        }

    def _dependency_evidence_payload(
        self, state: RelationMemorySessionState, dependency: dict[str, Any]
    ) -> dict[str, Any]:
        reason = str(dependency.get("reason") or "")
        evidence_type = "dependency_rule"
        match = re.match(r"^([a-z_]+):\s*(.+)$", reason, flags=re.IGNORECASE)
        if match:
            evidence_type = match.group(1).lower()
        source_sheet, source_range = self._sheet_range_from_evidence(reason)
        return {
            "source_type": "xlsx",
            "source_file": self._single_xlsx_filename(state),
            "source_sheet": source_sheet,
            "source_range": source_range,
            "quote": "",
            "reason": reason,
            "evidence_type": evidence_type,
            "source_document_id": None,
        }

    def _skipped_payload(self, relation: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "source_metric_code": relation.get("source_metric_code"),
            "target_metric_code": relation.get("target_metric_code"),
            "edge_type": relation.get("edge_type") or "driver",
            "source": relation.get("source") or "candidate",
            "reason": reason,
        }

    def _document_filename(self, state: RelationMemorySessionState, document_id: str | None) -> str:
        if document_id:
            for document in state.bundle.documents:
                if document.id == document_id:
                    return document.filename
        if len(state.bundle.documents) == 1:
            return state.bundle.documents[0].filename
        return ""

    def _single_xlsx_filename(self, state: RelationMemorySessionState) -> str:
        xlsx_documents = [
            document.filename
            for document in state.bundle.documents
            if document.file_type in {"xlsx", "xlsm"}
        ]
        return xlsx_documents[0] if len(xlsx_documents) == 1 else ""

    def _sheet_range_from_evidence(self, evidence: str) -> tuple[str, str]:
        match = re.search(
            r"([A-Za-zА-Яа-яЁё0-9 _.-]+)!([A-Z]{1,3}\d+(?::[A-Z]{1,3}\d+)?)", str(evidence or "")
        )
        if not match:
            return "", ""
        return match.group(1).strip(), match.group(2)

    def _workspace_root(self, workspace_root: str | Path | None) -> Path:
        root_value = workspace_root or settings.RELATION_MEMORY_MCP_WORKSPACE_ROOT
        if not root_value:
            raise ValueError("workspace_root is required.")
        root = Path(root_value).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"workspace_root does not exist or is not a directory: {root}")
        return root

    def _resolve_input_paths(self, root: Path, raw_paths: list[str]) -> list[Path]:
        resolved: list[Path] = []
        for raw_path in raw_paths:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = root / path
            path = path.resolve()
            self._ensure_inside_root(root, path)
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file() and child.suffix.lower() in MCP_ALLOWED_EXTENSIONS:
                        self._validate_input_file(child)
                        resolved.append(child)
                continue
            self._validate_input_file(path)
            resolved.append(path)
        return list(dict.fromkeys(resolved))

    def _validate_input_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            raise ValueError(f"Input path is not a file: {path}")
        extension = path.suffix.lower()
        if extension not in MCP_ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file extension for MCP relation memory: {extension or '<none>'}"
            )
        if path.stat().st_size > self.max_file_size_bytes:
            raise ValueError(
                f"Input file exceeds max size of {self.max_file_size_bytes // (1024 * 1024)} MB: {path.name}"
            )

    def _resolve_output_dir(self, root: Path, output_dir: str | Path) -> Path:
        path = Path(output_dir).expanduser()
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        self._ensure_inside_root(root, path)
        return path

    def _ensure_inside_root(self, root: Path, path: Path) -> None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Path must stay inside workspace_root: {path}") from exc

    def _safe_text_source_name(self, source_name: str) -> str:
        value = normalize_metric_code(source_name or "cowork_text")
        suffix = Path(source_name).suffix.lower()
        if suffix in {".txt", ".md"}:
            return f"{value}{suffix}"
        return f"{value}.txt"

    def _state(self, session_id: str) -> RelationMemorySessionState:
        try:
            return self.service.sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown relation memory session: {session_id}") from exc

    def _write_review_xlsx(
        self, path: Path, auto_saved: list[dict[str, Any]], candidates: list[dict[str, Any]]
    ) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "relations_review"
        headers = [
            "candidate_id",
            "status",
            "source_metric_code",
            "target_metric_code",
            "edge_type",
            "confidence",
            "evidence_type",
            "evidence_text",
            "source_file",
            "source_sheet",
            "source_range",
            "user_decision",
            "comment",
        ]
        sheet.append(headers)
        for relation in [*auto_saved, *candidates]:
            evidence = (relation.get("evidence") or [{}])[0]
            sheet.append(
                [
                    relation.get("id") or "",
                    relation.get("status") or "",
                    relation.get("source_metric_code") or "",
                    relation.get("target_metric_code") or "",
                    relation.get("edge_type") or "",
                    relation.get("confidence") or relation.get("score") or "",
                    evidence.get("evidence_type") or "",
                    evidence.get("quote") or evidence.get("reason") or "",
                    evidence.get("source_file") or "",
                    evidence.get("source_sheet") or "",
                    evidence.get("source_range") or "",
                    "",
                    "",
                ]
            )
        workbook.save(path)
        workbook.close()

    def _write_evidence_report(
        self, path: Path, auto_saved: list[dict[str, Any]], candidates: list[dict[str, Any]]
    ) -> None:
        lines = ["# Relation Memory Evidence", ""]
        for title, relations in (
            ("Auto-saved relations", auto_saved),
            ("Candidate relations", candidates),
        ):
            lines.extend([f"## {title}", ""])
            if not relations:
                lines.extend(["None.", ""])
                continue
            for relation in relations:
                evidence = (relation.get("evidence") or [{}])[0]
                lines.extend(
                    [
                        f"- `{relation.get('source_metric_code')}` -> `{relation.get('target_metric_code')}` ({relation.get('edge_type')})",
                        f"  - status: {relation.get('status')}",
                        f"  - evidence_type: {evidence.get('evidence_type') or ''}",
                        f"  - source: {evidence.get('source_file') or ''} {evidence.get('source_sheet') or ''} {evidence.get('source_range') or ''}".rstrip(),
                        f"  - evidence: {evidence.get('quote') or evidence.get('reason') or ''}",
                    ]
                )
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
