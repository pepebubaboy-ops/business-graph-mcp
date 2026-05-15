from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


EdgeType = Literal["driver", "inverse_driver", "component", "lag"]
ConfirmationDecision = Literal["approve", "reject"]


class DocumentSummary(BaseModel):
    id: str
    filename: str
    file_type: str
    size_bytes: int


class DocumentParseStatus(BaseModel):
    document_id: str
    filename: str
    file_type: str
    parser: str
    status: Literal["parsed", "parsed_with_warnings", "unsupported_layout", "unsupported_file_type", "parse_failed"]
    dataset_count: int = 0
    metric_count: int = 0
    dependency_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class CandidateRelation(BaseModel):
    id: str
    source_metric_code: str
    target_metric_code: str
    edge_type: EdgeType = "driver"
    relation_type: str = "driver"
    lag_period: str | None = None
    note: str = ""
    evidence: str = ""
    evidence_type: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_approval_reason: str = ""
    source: str = "candidate"
    source_document_id: str | None = None
    source_hypothesis_id: str | None = None


class MetricCandidate(BaseModel):
    id: str
    metric_id: str
    canonical_code: str
    raw_label: str
    label: str = ""
    aliases: list[str] = Field(default_factory=list)
    unit: str = ""
    department: str = "generic"
    source_sheet: str = ""
    section_path: str = ""
    semantic_type: str = "unknown"
    aggregation: str = "sum"
    status: str = "proposed"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str = ""
    approved: bool = False


class ConfirmedRelation(BaseModel):
    source_metric_code: str
    target_metric_code: str
    edge_type: EdgeType
    lag_period: str | None = None
    note: str = ""
    source_session_id: str | None = None
    source_document_id: str | None = None
    evidence_text: str = ""
    evidence_type: str = ""
    source_file: str = ""
    source_sheet: str = ""
    source_range: str = ""
    confidence: float | None = None
    auto_saved: bool = False
    confirmed_at: str | None = None
    source: str = "user_confirmed"


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: str = ""
    pending_confirmation_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    debug_trace: list[dict[str, Any]] = Field(default_factory=list)


class RelationMemorySessionResponse(BaseModel):
    session_id: str
    assistant_message: str
    documents: list[DocumentSummary] = Field(default_factory=list)
    document_parse_statuses: list[DocumentParseStatus] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    inquiry_questions: list[dict[str, Any]] = Field(default_factory=list)
    metric_candidates: list[MetricCandidate] = Field(default_factory=list)
    pending_confirmations: list[CandidateRelation] = Field(default_factory=list)
    open_agent_question: dict[str, Any] | None = None
    memory_facts: list[dict[str, Any]] = Field(default_factory=list)
    chat_history: list[ConversationMessage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChatMessageRequest(BaseModel):
    message: str


class ChatMessageResponse(BaseModel):
    session_id: str
    assistant_message: str
    pending_confirmations: list[CandidateRelation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    debug_trace: list[dict[str, Any]] = Field(default_factory=list)


class RelationMemoryChatRequest(BaseModel):
    message: str


class RelationMemoryChatResponse(BaseModel):
    session_id: str
    assistant_message: str
    created_pending_confirmations: list[CandidateRelation] = Field(default_factory=list)
    pending_confirmations: list[CandidateRelation] = Field(default_factory=list)
    metric_candidates: list[MetricCandidate] = Field(default_factory=list)
    chat_history: list[ConversationMessage] = Field(default_factory=list)
    session: RelationMemorySessionResponse
    warnings: list[str] = Field(default_factory=list)
    debug_trace: list[dict[str, Any]] = Field(default_factory=list)


class GraphQuestionRequest(BaseModel):
    question: str


class GraphQuestionResponse(BaseModel):
    session_id: str
    handled: bool = False
    intent: str = "unknown"
    answer: str = ""
    matched_metrics: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    cypher_hint: str = ""
    warnings: list[str] = Field(default_factory=list)
    confidence: float | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    clarification: dict[str, Any] | None = None
    answer_mode: Literal["deterministic", "llm_grounded", "fallback"] = "deterministic"
    debug_trace: list[dict[str, Any]] = Field(default_factory=list)


class ConfirmationRequest(BaseModel):
    decision: ConfirmationDecision


class ConfirmationResponse(BaseModel):
    session_id: str
    candidate_id: str
    decision: ConfirmationDecision
    saved: bool
    relation: ConfirmedRelation | None = None
    message: str


class MetricCandidateConfirmationResponse(BaseModel):
    session_id: str
    candidate_id: str
    decision: ConfirmationDecision
    saved: bool
    metric_candidate: MetricCandidate | None = None
    message: str


class MetricCandidatesResponse(BaseModel):
    session_id: str
    metric_candidates: list[MetricCandidate] = Field(default_factory=list)


class MemoryRelationsResponse(BaseModel):
    relations: list[ConfirmedRelation] = Field(default_factory=list)
