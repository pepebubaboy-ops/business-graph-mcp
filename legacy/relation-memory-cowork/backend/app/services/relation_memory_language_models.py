from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResolvedMetricCandidate:
    code: str
    label: str
    matched_alias: str = ""
    match_reason: str = ""
    score: float = 0.0
    role: str = "metric"

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "label": self.label,
        }
        if self.matched_alias:
            payload["matched_alias"] = self.matched_alias
        if self.match_reason:
            payload["match_reason"] = self.match_reason
        if self.score:
            payload["score"] = round(float(self.score), 3)
        if self.role:
            payload["role"] = self.role
        return payload


@dataclass
class ClarificationSlot:
    role: str
    phrase: str
    options: list[ResolvedMetricCandidate] = field(default_factory=list)
    resolved_code: str | None = None


@dataclass
class PendingClarification:
    intent: str
    original_question: str
    slots: list[ClarificationSlot] = field(default_factory=list)
    source_utterance: str = ""


@dataclass
class ConfirmedMetricAlias:
    canonical_code: str
    alias: str
    label: str


@dataclass
class RelationMemoryConversationContext:
    last_resolved_intent: str = ""
    last_resolved_metric_codes: list[str] = field(default_factory=list)
    pending_clarification: PendingClarification | None = None
    source_utterance: str = ""


@dataclass
class NormalizedQuestion:
    handled: bool
    intent: str = "unknown"
    resolved_metrics: list[ResolvedMetricCandidate] = field(default_factory=list)
    matched_metrics: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarification_needed: bool = False
    clarification: PendingClarification | None = None
    updated_context: RelationMemoryConversationContext = field(
        default_factory=RelationMemoryConversationContext
    )
    confirmed_aliases: list[ConfirmedMetricAlias] = field(default_factory=list)


@dataclass
class MetricAliasRecord:
    code: str
    label: str
    alias: str
    category: str
    normalized: str
    latinized: str
    stemmed: str
    tokens: list[str]
    abbreviations: list[str]
