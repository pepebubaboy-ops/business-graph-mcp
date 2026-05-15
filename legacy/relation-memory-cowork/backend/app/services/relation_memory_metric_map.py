from __future__ import annotations

from typing import Any, Callable

from app.services.relation_memory_question_models import RUSSIAN_METRIC_ALIASES, RUSSIAN_METRIC_DISPLAY_LABELS


def metric_default_label(code: str) -> str:
    return str(code or "").replace("_", " ").title()


def unique_texts(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def extend_metric_values(metric: dict[str, Any], field_name: str, values: list[Any]) -> None:
    existing = metric.get(field_name) or []
    if isinstance(existing, str):
        existing = [existing]
    metric[field_name] = unique_texts([*existing, *values])


def ensure_metric_entry(
    metric_map: dict[str, dict[str, Any]],
    code: str,
    *,
    label: str | None = None,
    aliases: list[Any] | None = None,
    approved_aliases: list[Any] | None = None,
    source_labels: list[Any] | None = None,
    source: str,
) -> dict[str, Any]:
    cleaned_code = str(code or "").strip()
    if not cleaned_code:
        return {}
    cleaned_label = str(label or metric_default_label(cleaned_code)).strip()
    entry = metric_map.setdefault(
        cleaned_code,
        {
            "code": cleaned_code,
            "label": cleaned_label,
            "aliases": unique_texts([cleaned_code, cleaned_code.replace("_", " "), *(aliases or [])]),
            "approved_aliases": [],
            "source_labels": unique_texts(source_labels if source_labels is not None else [cleaned_label]),
            "source": source,
        },
    )
    entry.setdefault("code", cleaned_code)
    entry.setdefault("label", cleaned_label)
    entry.setdefault("approved_aliases", [])
    entry.setdefault("source_labels", [])
    entry.setdefault("source", source)
    extend_metric_values(entry, "aliases", aliases or [])
    extend_metric_values(entry, "approved_aliases", approved_aliases or [])
    extend_metric_values(entry, "source_labels", source_labels or [])
    return entry


def ensure_question_metric(metric_map: dict[str, dict[str, Any]], code: str) -> None:
    if not code:
        return
    display_label = RUSSIAN_METRIC_DISPLAY_LABELS.get(code, metric_default_label(code))
    ensure_metric_entry(
        metric_map,
        code,
        label=display_label,
        aliases=[
            code,
            code.replace("_", " "),
            RUSSIAN_METRIC_DISPLAY_LABELS.get(code, ""),
            *RUSSIAN_METRIC_ALIASES.get(code, []),
        ],
        source_labels=[display_label],
        source="memory_prior",
    )


def ensure_external_context_metric_label(
    metric_map: dict[str, dict[str, Any]],
    metric_code: str,
    label: str,
) -> None:
    if not metric_code:
        return
    cleaned_label = str(label or metric_default_label(metric_code)).strip()
    ensure_metric_entry(
        metric_map,
        metric_code,
        label=cleaned_label,
        aliases=[metric_code, metric_code.replace("_", " "), cleaned_label],
        source_labels=[cleaned_label],
        source="llm_external_context",
    )


def build_metric_map(
    *,
    snapshot_metrics: list[dict[str, Any]],
    detected_metrics: list[Any],
    parsed_document_ids: set[str],
    metric_mapping_priors: list[dict[str, Any]],
    mapping_aliases: Callable[[Any], list[str]],
) -> dict[str, dict[str, Any]]:
    metric_map: dict[str, dict[str, Any]] = {}
    for metric in snapshot_metrics:
        code = str(metric["code"])
        label = str(metric.get("label") or code)
        ensure_metric_entry(
            metric_map,
            code,
            label=label,
            aliases=[*(metric.get("aliases", []) or []), *RUSSIAN_METRIC_ALIASES.get(code, [])],
            source_labels=[label],
            source=str(metric.get("source") or "snapshot"),
        )
    for metric in detected_metrics:
        if metric.source_document_id not in parsed_document_ids:
            continue
        ensure_metric_entry(
            metric_map,
            metric.code,
            label=metric.label,
            aliases=[metric.code, metric.label, *RUSSIAN_METRIC_ALIASES.get(metric.code, [])],
            source_labels=[metric.label],
            source=metric.source,
        )
    for prior in metric_mapping_priors:
        canonical_code = str(prior.get("canonical_code") or prior.get("metric_code") or "")
        entry = metric_map.get(canonical_code)
        if not entry:
            continue
        extra_aliases = [
            str(prior.get("raw_label") or "").strip(),
            str(prior.get("label") or "").strip(),
            *mapping_aliases(prior.get("aliases")),
        ]
        extend_metric_values(entry, "aliases", extra_aliases)
        extend_metric_values(entry, "approved_aliases", extra_aliases)
        extend_metric_values(entry, "source_labels", extra_aliases[:2])
    return metric_map


def contains_cyrillic(value: str) -> bool:
    return any(("а" <= char.lower() <= "я") or char in {"ё", "Ё"} for char in str(value or ""))


def looks_like_business_cyrillic_label(value: str) -> bool:
    cleaned = str(value or "").strip()
    return contains_cyrillic(cleaned) and len(cleaned.replace(" ", "")) >= 3


def display_metric_label(code: str, metric: dict[str, Any]) -> str:
    label = str(metric.get("label") or metric.get("raw_label") or code).strip()
    if contains_cyrillic(label):
        return label
    for collection_name in ("source_labels", "approved_aliases"):
        for candidate in metric.get(collection_name) or []:
            candidate_text = str(candidate or "").strip()
            if looks_like_business_cyrillic_label(candidate_text):
                return candidate_text
    mapped = RUSSIAN_METRIC_DISPLAY_LABELS.get(str(code or "").lower())
    if mapped:
        return mapped
    for candidate in metric.get("aliases") or []:
        candidate_text = str(candidate or "").strip()
        if looks_like_business_cyrillic_label(candidate_text):
            return candidate_text
    return label or str(code or "")


def metric_label_for_chat(metric_map: dict[str, dict[str, Any]], metric_code: str) -> str:
    return display_metric_label(metric_code, metric_map.get(metric_code, {}))


def metric_response_items(metric_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "code": code,
            "label": display_metric_label(code, metric),
            "canonical_label": metric.get("label") or code,
            "aliases": list(metric.get("aliases") or []),
            "approved_aliases": list(metric.get("approved_aliases") or []),
            "source_labels": list(metric.get("source_labels") or []),
        }
        for code, metric in sorted(metric_map.items())
    ]
