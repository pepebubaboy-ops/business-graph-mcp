from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


BASE_DIR = Path(__file__).resolve().parents[3]
DOMAIN_PACK_DIR = BASE_DIR / "backend" / "data" / "relation_memory_domain_packs"
ROW_METRIC_HEADERS = {"показатель", "metric", "metrics", "indicator", "метрика"}
TECHNICAL_DEFAULT_PATTERNS = [
    r"^проверка$",
    r"^итого проверка",
    r"^расшифровка",
    r"^\*+расшифровка",
    r"^delta$",
    r"^отклонение",
    r"^%$",
]
SECTION_MARKER_PATTERNS = [
    r"^в том числе",
    r"^расшифровка",
    r"^\*+расшифровка",
]
LONG_FACT_HEADERS = [
    "period",
    "scenario",
    "department",
    "source_workbook",
    "sheet",
    "section_path",
    "metric_code",
    "raw_label",
    "unit",
    "value",
]
PIVOT_DIMENSIONS = ["month", "report_sheet", "source_workbook", "scenario"]
METRIC_CANDIDATE_HEADERS = [
    "metric_id",
    "metric_code",
    "canonical_code",
    "raw_label",
    "label",
    "description",
    "aliases",
    "unit",
    "department",
    "source_sheet",
    "section_path",
    "semantic_type",
    "aggregation",
    "status",
    "confidence",
    "evidence",
    "sensitivity_level",
    "allow_roles",
    "preferred_dataset",
]
RELATION_CANDIDATE_HEADERS = [
    "source_metric_code",
    "target_metric_code",
    "relation_type",
    "edge_type",
    "confidence",
    "score",
    "evidence_type",
    "evidence",
    "period_window",
    "source_document_id",
    "needs_approval",
    "needs_approval_reason",
]
SEMANTIC_TYPES = {
    "target_metric",
    "cost_component",
    "business_driver",
    "denominator",
    "external_context",
    "technical_check",
    "unknown",
}
SCENARIO_PATTERNS = [
    (r"\bгб\b|бюджет|budget", "gb"),
    (r"\bбм\b|business model|base model", "bm"),
    (r"без консервац", "without_conservation"),
    (r"с учетом лс|с уч[её]том лс", "with_ls"),
    (r"\bфакт\b|actual", "fact"),
]
RUSSIAN_MONTHS = {
    "янв": 1,
    "январ": 1,
    "фев": 2,
    "феврал": 2,
    "мар": 3,
    "март": 3,
    "апр": 4,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июнь": 6,
    "июл": 7,
    "июль": 7,
    "авг": 8,
    "август": 8,
    "сен": 9,
    "сент": 9,
    "сентябр": 9,
    "окт": 10,
    "октябр": 10,
    "ноя": 11,
    "нояб": 11,
    "ноябр": 11,
    "дек": 12,
    "декабр": 12,
}
RELATION_GATE_DECISIONS = {"approve", "keep_pending", "reject"}
RELATION_GATE_EDGE_TYPES = {"component", "driver", "inverse_driver"}
RELATION_GATE_CONTEXT_LIMIT = 48


@dataclass
class DynamicRelationMemoryArtifacts:
    workbook_profile_path: Path
    normalized_facts_path: Path
    pivoted_facts_path: Path
    metric_candidates_path: Path
    relation_candidates_path: Path
    dependency_rules_path: Path
    quality_report_path: Path
    generated_manifest_path: Path
    profile: dict[str, Any]
    metric_candidates: list[dict[str, Any]]
    relation_candidates: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in [
            "workbook_profile_path",
            "normalized_facts_path",
            "pivoted_facts_path",
            "metric_candidates_path",
            "relation_candidates_path",
            "dependency_rules_path",
            "quality_report_path",
            "generated_manifest_path",
        ]:
            payload[key] = str(payload[key])
        return payload


def normalize_text(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ").lower()
    text = re.sub(r"[^a-zа-яё0-9%/.,+ -]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def titleize_code(code: str) -> str:
    return code.replace("_", " ").strip().title()


def period_from_value(value: Any) -> str | None:
    if isinstance(value, datetime):
        return f"{value.year:04d}-{value.month:02d}"
    if isinstance(value, date):
        return f"{value.year:04d}-{value.month:02d}"
    text = str(value or "").strip()
    match = re.match(r"^(20\d{2})[-.](\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}"
    normalized = normalize_text(text)
    month_pattern = "|".join(sorted(RUSSIAN_MONTHS, key=len, reverse=True))
    month_match = re.search(rf"\b({month_pattern})[а-яё.]*\s+(20\d{{2}}|\d{{2}})\b", normalized)
    if month_match:
        year = int(month_match.group(2))
        if year < 100:
            year += 2000
        return f"{year:04d}-{RUSSIAN_MONTHS[month_match.group(1)]:02d}"
    return None


def looks_like_metric_label(value: Any) -> bool:
    text = str(value or "").strip()
    if len(text) < 2:
        return False
    normalized = normalize_text(text)
    if not normalized or normalized in ROW_METRIC_HEADERS:
        return False
    return True


def looks_like_section_marker(value: Any) -> bool:
    normalized = normalize_text(value)
    return any(re.search(pattern, normalized) for pattern in SECTION_MARKER_PATTERNS)


def formula_signature(formula: str) -> str:
    signature = formula.upper()
    signature = re.sub(r"'[^']+'!", "SHEET!", signature)
    signature = re.sub(r"\$?[A-Z]{1,3}\$?\d+", "CELL", signature)
    signature = re.sub(r"\d+(?:[.,]\d+)?", "NUM", signature)
    signature = re.sub(r"\s+", "", signature)
    return signature[:240]


def aggregation_for_metric(metric_code: str, unit: str | None = None) -> str:
    text = f"{metric_code} {unit or ''}".lower()
    average_tokens = (
        "rate",
        "avg",
        "margin",
        "index",
        "days",
        "temperature",
        "%",
        "доля",
        "индекс",
        "ставка",
    )
    return "mean" if any(token in text for token in average_tokens) else "sum"


def scenario_from_text(value: Any) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    for pattern, scenario in SCENARIO_PATTERNS:
        if re.search(pattern, normalized):
            return scenario
    return None


def _append_reason(existing: Any, reason: str) -> str:
    existing_text = str(existing or "").strip()
    if not existing_text:
        return reason
    if reason in existing_text:
        return existing_text
    return f"{existing_text}; {reason}"


def edge_type_for_relation(relation_type: str, source_metric_code: str = "") -> str:
    if relation_type in {"inverse_driver"}:
        return "inverse_driver"
    if relation_type in {"driver", "lagged_driver", "proxy"}:
        return "driver"
    if relation_type == "denominator":
        return "inverse_driver"
    return "component"


def read_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
