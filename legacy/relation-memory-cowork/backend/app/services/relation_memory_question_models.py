from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.relation_memory_language_models import RelationMemoryConversationContext


RUSSIAN_METRIC_DISPLAY_LABELS = {
    "revenue": "Выручка",
    "gross_margin": "Валовая маржа",
    "operating_profit": "Операционная прибыль",
    "operating_income": "Операционная прибыль",
    "ebit": "Операционная прибыль",
    "net_profit": "Чистая прибыль",
    "total_cost": "Совокупные затраты",
    "freight_cost": "Логистические затраты",
    "volume": "Объем",
    "orders": "Заказы",
    "avg_price": "Средняя цена",
    "average_price": "Средняя цена",
    "stockout_rate": "Доля out-of-stock",
    "return_rate": "Доля возвратов",
    "state_toll": "Государственные дорожные сборы",
    "state_toll_index": "Индекс государственных дорожных сборов",
    "toll_tariff_policy": "Тарифная политика операторов платных дорог",
    "vehicle_category_mix": "Структура транспорта по категориям",
    "paid_road_route_share": "Доля маршрутов по платным дорогам",
    "platon": "Платон",
    "platon_platnaya_doroga": "Платон и платные дороги",
    "weather_risk": "Погодный риск",
    "weather_risk_index": "Индекс погодного риска",
    "market_ftl_rate": "Рыночная ставка FTL",
    "market_ftl_rate_index": "Индекс рыночной ставки FTL",
    "road_restrictions": "Дорожные ограничения",
    "road_restrictions_index": "Индекс дорожных ограничений",
    "zatraty_na_gsm": "Затраты на ГСМ",
    "gsm": "ГСМ",
    "fot": "ФОТ",
    "prochee": "Прочее",
    "sebestoimost_ftl_total": "Себестоимость FTL",
}
RUSSIAN_METRIC_ALIASES = {
    "revenue": ["выручка", "доход", "продажи"],
    "gross_margin": ["валовая маржа", "маржа"],
    "operating_profit": ["операционная прибыль"],
    "total_cost": ["совокупные затраты", "общие затраты"],
    "freight_cost": ["логистические затраты", "стоимость логистики"],
    "volume": ["объем", "объем перевозок"],
    "orders": ["заказы", "объем заказов"],
    "state_toll": ["государственные дорожные сборы", "дорожные сборы", "гос сборы", "стоимость платных дорог", "тариф платных дорог"],
    "state_toll_index": [
        "индекс государственных дорожных сборов",
        "государственные дорожные сборы",
        "дорожные сборы",
        "гос сборы",
        "стоимость платных дорог",
        "тариф платных дорог",
        "платные дороги стоимость",
    ],
    "toll_tariff_policy": [
        "тарифная политика операторов платных дорог",
        "тарифная политика платных дорог",
        "тарифы операторов платных дорог",
        "тариф платных дорог",
    ],
    "vehicle_category_mix": ["структура транспорта по категориям", "категории транспорта", "класс транспорта"],
    "paid_road_route_share": [
        "доля маршрутов по платным дорогам",
        "маршруты по платным дорогам",
        "доля платных дорог",
    ],
    "platon": ["платон", "платная дорога", "платные дороги", "платон и платные дороги"],
    "platon_platnaya_doroga": ["платон", "платная дорога", "платные дороги", "платон и платные дороги"],
    "weather_risk_index": ["индекс погодного риска", "погодный риск", "погода"],
    "market_ftl_rate_index": ["индекс рыночной ставки ftl", "рыночная ставка ftl", "ставка ftl"],
    "road_restrictions_index": ["индекс дорожных ограничений", "дорожные ограничения"],
    "road_restriction_days": ["дни дорожных ограничений", "дорожные ограничения"],
    "zatraty_na_gsm": ["затраты на гсм", "гсм", "топливо"],
    "sebestoimost_ftl_total": ["себестоимость ftl", "себестоимость перевозки", "себестоимость рейса"],
    "prochee": ["прочее", "прочие расходы"],
}

DEFAULT_GRAPH_PATH_MAX_HOPS = 3
RELATION_GRAPH_PATH_MAX_HOPS = 6


@dataclass
class GraphQuestionAnswer:
    handled: bool
    intent: str = "unknown"
    answer: str = ""
    matched_metrics: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    cypher_hint: str = ""
    warnings: list[str] = field(default_factory=list)
    updated_context: RelationMemoryConversationContext | None = None
    confirmed_aliases: list[dict[str, str]] = field(default_factory=list)
    confidence: float | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    clarification: dict[str, Any] | None = None
    answer_mode: str = "deterministic"
    debug_trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handled": self.handled,
            "intent": self.intent,
            "answer": self.answer,
            "matched_metrics": self.matched_metrics,
            "rows": self.rows,
            "cypher_hint": self.cypher_hint,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "clarification": self.clarification,
            "answer_mode": self.answer_mode,
            "debug_trace": self.debug_trace,
        }


@dataclass
class QuestionPlan:
    raw_question: str
    intent: str = "unknown"
    metric_slots: list[dict[str, str]] = field(default_factory=list)
    evidence_type: str = "unknown"
    answer_shape: str = "short_explanation"
    requested_direction: str = ""
    is_question: bool = False
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    planner_source: str = "deterministic"


@dataclass
class ResolvedQuestionMetric:
    code: str
    label: str
    role: str = "metric"
    matched_alias: str = ""
    match_reason: str = ""
    score: float = 0.0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ResolvedQuestionMetric":
        return cls(
            code=str(payload.get("code") or ""),
            label=str(payload.get("label") or payload.get("code") or ""),
            role=str(payload.get("role") or "metric"),
            matched_alias=str(payload.get("matched_alias") or ""),
            match_reason=str(payload.get("match_reason") or ""),
            score=float(payload.get("score") or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "label": self.label, "role": self.role}
        if self.matched_alias:
            payload["matched_alias"] = self.matched_alias
        if self.match_reason:
            payload["match_reason"] = self.match_reason
        if self.score:
            payload["score"] = round(float(self.score), 3)
        return payload


@dataclass
class MetricResolution:
    plan: QuestionPlan
    normalized: Any
    intent: str
    metrics: list[ResolvedQuestionMetric] = field(default_factory=list)
    handled: bool = False


@dataclass
class EvidenceClaim:
    claim_type: str
    text: str
    row: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    metric_codes: list[str] = field(default_factory=list)
    status: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.claim_type,
            "text": self.text,
            "row": self.row,
        }
        if self.confidence is not None:
            payload["confidence"] = round(float(self.confidence), 3)
        if self.metric_codes:
            payload["metric_codes"] = self.metric_codes
        if self.status:
            payload["status"] = self.status
        if self.source:
            payload["source"] = self.source
        return payload


@dataclass
class EvidenceBundle:
    intent: str
    fallback_answer: str
    matched_metrics: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    claims: list[EvidenceClaim] = field(default_factory=list)
    cypher_hint: str = ""
    warnings: list[str] = field(default_factory=list)
    confidence: float | None = None
    clarification: dict[str, Any] | None = None

    @property
    def evidence(self) -> list[dict[str, Any]]:
        return [claim.to_dict() for claim in self.claims]


@dataclass
class AnswerDraft:
    answer: str
    answer_mode: str = "deterministic"
    warnings: list[str] = field(default_factory=list)
