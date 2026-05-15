from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class NodeType(StrEnum):
    METRIC = "Metric"
    PROCESS = "Process"
    ROLE = "Role"
    DEPARTMENT = "Department"
    SYSTEM = "System"
    DOCUMENT = "Document"
    DATASET = "Dataset"
    CONTROL = "Control"
    RISK = "Risk"
    BOTTLENECK = "Bottleneck"
    AUTOMATION = "Automation"
    DECISION = "Decision"


class RelationType(StrEnum):
    DRIVES = "DRIVES"
    INVERSELY_DRIVES = "INVERSELY_DRIVES"
    COMPONENT_OF = "COMPONENT_OF"
    DEPENDS_ON = "DEPENDS_ON"
    PRODUCES = "PRODUCES"
    USES_SYSTEM = "USES_SYSTEM"
    OWNED_BY = "OWNED_BY"
    HANDOFF_TO = "HANDOFF_TO"
    DUPLICATES = "DUPLICATES"
    HAS_RISK = "HAS_RISK"
    HAS_BOTTLENECK = "HAS_BOTTLENECK"
    CAN_BE_AUTOMATED_BY = "CAN_BE_AUTOMATED_BY"
    EVIDENCED_BY = "EVIDENCED_BY"


class RelationStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANDIDATE = "candidate"
    REJECTED = "rejected"


class EvidenceQuality(StrEnum):
    EXPLICIT_RULE = "explicit_rule"
    FORMULA = "formula"
    DIRECT_QUOTE = "direct_quote"
    STATISTICAL_SIGNAL = "statistical_signal"
    LLM_HYPOTHESIS = "llm_hypothesis"
    MANUAL_APPROVAL = "manual_approval"


class FileStatus(StrEnum):
    REGISTERED = "registered"
    STORED = "stored"
    PARSED = "parsed"
    FAILED = "failed"


class EvidenceRef(BaseModel):
    id: str = Field(default_factory=lambda: f"evidence:{uuid4().hex}")
    source_file_id: str | None = None
    source_name: str | None = None
    source_type: str | None = None
    locator: dict[str, Any] = Field(default_factory=dict)
    method: EvidenceQuality
    quote_or_value: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BusinessNode(BaseModel):
    id: str
    type: NodeType
    name: str
    workspace_id: str = "default"
    aliases: list[str] = Field(default_factory=list)
    domain: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BusinessRelation(BaseModel):
    id: str = Field(default_factory=lambda: f"rel:{uuid4().hex}")
    from_id: str
    to_id: str
    type: RelationType
    workspace_id: str = "default"
    status: RelationStatus = RelationStatus.CANDIDATE
    polarity: Literal[-1, 0, 1] = 0
    strength: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    explanation: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def confirmed_requires_evidence(self) -> BusinessRelation:
        if self.status == RelationStatus.CONFIRMED and not self.evidence_refs:
            raise ValueError("Confirmed relations must include at least one evidence ref.")
        return self


class RelationCandidate(BaseModel):
    relation: BusinessRelation
    reason: str | None = None

    @field_validator("relation")
    @classmethod
    def candidate_status(cls, relation: BusinessRelation) -> BusinessRelation:
        if relation.status != RelationStatus.CANDIDATE:
            raise ValueError("RelationCandidate must contain a candidate relation.")
        return relation


class AnalysisRequest(BaseModel):
    workspace_id: str = "default"
    file_ids: list[str] = Field(default_factory=list)
    local_paths: list[str] = Field(default_factory=list)
    objective: str = "Find business relationships in provided files."
    domain_hint: str = "generic"
    mode: str = "evidence_first"


class FileRecord(BaseModel):
    file_id: str
    workspace_id: str = "default"
    original_filename: str
    content_type: str | None = None
    size_bytes: int = Field(ge=0)
    sha256: str
    storage_path: str = Field(exclude=True)
    status: FileStatus = FileStatus.REGISTERED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileUploadResult(BaseModel):
    file_id: str
    workspace_id: str = "default"
    original_filename: str
    size_bytes: int = Field(ge=0)
    sha256: str
    status: FileStatus


class GraphSummary(BaseModel):
    workspace_id: str = "default"
    node_count: int = 0
    relation_count: int = 0
    confirmed_relation_count: int = 0
    candidate_relation_count: int = 0
    rejected_relation_count: int = 0
    node_counts_by_type: dict[str, int] = Field(default_factory=dict)
    relation_counts_by_type: dict[str, int] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    analysis_id: str = Field(default_factory=lambda: f"analysis:{uuid4().hex}")
    workspace_id: str = "default"
    graph_snapshot_id: str = Field(default_factory=lambda: f"graph:{uuid4().hex}")
    confirmed_relations_count: int = 0
    candidate_relations_count: int = 0
    detected_processes: int = 0
    detected_metrics: int = 0
    detected_risks: int = 0
    warnings: list[str] = Field(default_factory=list)
    summary: GraphSummary = Field(default_factory=GraphSummary)
    artifacts: dict[str, str] = Field(default_factory=dict)


class PipelineStep(BaseModel):
    id: str
    type: str
    title: str
    description: str | None = None
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    risk_level: str = "low"


class PipelineProposal(BaseModel):
    pipeline_id: str = Field(default_factory=lambda: f"pipeline:{uuid4().hex}")
    workspace_id: str = "default"
    title: str
    status: str = "proposed"
    mode: str = "dry_run_only"
    risk_level: str = "low"
    approvals_required: list[str] = Field(default_factory=list)
    steps: list[PipelineStep] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
