from __future__ import annotations

import re
from typing import Any, Callable

from app.services.relation_memory_user_language import normalize_user_text


def emit_trace_event(
    *,
    trace_events: list[dict[str, Any]],
    trace_sink: Callable[[dict[str, Any]], None] | None,
    event: dict[str, Any],
) -> None:
    clean_event = {
        "stage": str(event.get("stage") or ""),
        "title": str(event.get("title") or event.get("stage") or ""),
        "summary": str(event.get("summary") or ""),
        "details": event.get("details") or {},
    }
    trace_events.append(clean_event)
    if trace_sink is not None:
        trace_sink(clean_event)


def planner_trace_event(plan: Any) -> dict[str, Any]:
    intent = trace_intent_label(plan.intent)
    evidence_type = trace_evidence_type_label(plan.evidence_type)
    source = "LLM" if plan.planner_source == "llm" else "детерминированный классификатор"
    direction = (
        f", направление: {trace_direction_label(plan.requested_direction)}"
        if plan.requested_direction
        else ""
    )
    return {
        "stage": "planner",
        "title": "QuestionPlanner",
        "summary": (
            f"Определил тип вопроса: {intent}. Буду искать: {evidence_type}{direction}. "
            f"Источник плана: {source}."
        ),
        "details": {
            "raw_question": plan.raw_question,
            "intent": plan.intent,
            "metric_slots": list(plan.metric_slots),
            "evidence_type": plan.evidence_type,
            "answer_shape": plan.answer_shape,
            "requested_direction": plan.requested_direction,
            "is_question": plan.is_question,
            "confidence": round(float(plan.confidence), 3),
            "planner_source": plan.planner_source,
            "warnings": list(plan.warnings),
        },
    }


def resolver_trace_event(resolution: Any, display_labels: dict[str, str]) -> dict[str, Any]:
    metrics = trace_metrics(resolution.normalized.matched_metrics, display_labels)
    if metrics:
        summary = "Сопоставил метрики: " + "; ".join(
            trace_metric_summary(metric) for metric in metrics[:4]
        )
    elif getattr(resolution.normalized, "clarification_needed", False):
        summary = "Не выбрал метрику автоматически: требуется уточнение."
    else:
        summary = "Метрики не были найдены или не требовались для этого intent."
    clarification = None
    if getattr(resolution.normalized, "clarification", None) is not None:
        clarification = clarification_payload(resolution.normalized.clarification)
    return {
        "stage": "resolver",
        "title": "MetricResolver",
        "summary": summary,
        "details": {
            "planner_intent": resolution.plan.intent,
            "resolved_intent": resolution.intent,
            "handled": bool(resolution.handled),
            "matched_metrics": metrics,
            "warnings": list(getattr(resolution.normalized, "warnings", []) or []),
            "clarification_needed": bool(
                getattr(resolution.normalized, "clarification_needed", False)
            ),
            "clarification": clarification,
        },
    }


def evidence_trace_event(bundle: Any) -> dict[str, Any]:
    claim_count = len(bundle.claims)
    row_count = len(bundle.rows)
    if row_count:
        top = trace_row_summary(bundle.rows[0])
        summary = f"Нашел evidence: строк {row_count}, фактов {claim_count}. Главное: {top}"
    else:
        summary = (
            "Подходящих строк evidence не найдено; ответ будет строиться из fallback или уточнения."
        )
    return {
        "stage": "evidence",
        "title": "EvidenceRetriever",
        "summary": summary,
        "details": {
            "intent": bundle.intent,
            "rows_count": row_count,
            "claims_count": claim_count,
            "confidence": bundle.confidence,
            "caveats": list(bundle.warnings),
            "top_rows": [compact_trace_row(row) for row in bundle.rows[:5]],
            "claims": [
                {
                    "type": claim.claim_type,
                    "text": claim.text,
                    "confidence": claim.confidence,
                    "status": claim.status,
                    "source": claim.source,
                    "metric_codes": list(claim.metric_codes),
                }
                for claim in bundle.claims[:5]
            ],
        },
    }


def answer_trace_event(
    *,
    answer: Any,
    bundle: Any,
    draft: Any,
    validated: Any,
    resolution: Any,
    display_labels: dict[str, str],
) -> dict[str, Any]:
    fallback_used = validated.answer_mode == "fallback" and draft.answer_mode != "fallback"
    if fallback_used:
        summary = "GroundingValidator отклонил LLM-формулировку; возвращаю deterministic fallback."
    elif validated.answer_mode == "llm_grounded":
        summary = "Финальная формулировка сделана LLM только по найденному evidence."
    else:
        summary = "Финальный ответ собран детерминированно из найденного evidence."
    return {
        "stage": "composer",
        "title": "AnswerComposer + GroundingValidator",
        "summary": summary,
        "details": {
            "intent": answer.intent or resolution.intent,
            "answer_mode_before_validation": draft.answer_mode,
            "answer_mode": validated.answer_mode,
            "confidence": bundle.confidence,
            "warnings": list(
                dict.fromkeys([*answer.warnings, *bundle.warnings, *validated.warnings])
            ),
            "matched_metrics": trace_metrics(answer.matched_metrics, display_labels),
            "answer_preview": short_label(validated.answer, limit=220),
        },
    }


def trace_metrics(
    metrics: list[dict[str, Any]], display_labels: dict[str, str]
) -> list[dict[str, Any]]:
    result = []
    for metric in metrics:
        item = {
            key: metric.get(key)
            for key in ("code", "label", "role", "matched_alias", "match_reason", "score")
            if metric.get(key) not in (None, "")
        }
        code = str(item.get("code") or "")
        if code in display_labels:
            item["label"] = display_labels[code]
        result.append(item)
    return result


def trace_metric_summary(metric: dict[str, Any]) -> str:
    label = str(metric.get("label") or metric.get("code") or "metric")
    code = str(metric.get("code") or "")
    alias = str(metric.get("matched_alias") or "")
    score = metric.get("score")
    parts = [f"«{short_label(label)}»"]
    if alias:
        parts.append(f"по фразе «{short_label(alias)}»")
    if score:
        parts.append(f"уверенность {score}")
    if code:
        parts.append(f"код {code}")
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} ({', '.join(parts[1:])})"


def trace_intent_label(intent: str) -> str:
    return {
        "upstream": "что влияет на метрику",
        "downstream": "на что влияет метрика",
        "relation_why": "есть ли связь между метриками",
        "change_cause": "причина изменения",
        "relations_overview": "обзор связей",
        "hypotheses_overview": "обзор гипотез",
        "observations_overview": "обзор наблюдений",
        "metrics_overview": "обзор метрик",
        "pending_confirmations": "связи на проверке",
        "statement": "новый факт или обратная связь",
        "unknown": "не распознано",
    }.get(intent, intent or "не распознано")


def trace_evidence_type_label(evidence_type: str) -> str:
    return {
        "graph_paths": "пути в графе",
        "observations_hypotheses": "наблюдения и гипотезы",
        "overview": "обзор snapshot",
        "pending_confirmations": "связи на проверке",
        "unknown": "не задано",
    }.get(evidence_type, evidence_type or "не задано")


def trace_direction_label(direction: str) -> str:
    return {"up": "рост", "down": "снижение"}.get(direction, direction or "не задано")


def trace_row_summary(row: dict[str, Any]) -> str:
    if row.get("path_text"):
        hops = row.get("hops")
        strength = row.get("total_strength") or row.get("confidence") or row.get("score")
        suffix = []
        if hops is not None:
            suffix.append(f"шаги={hops}")
        if strength is not None:
            suffix.append(f"сила={strength}")
        return f"{row['path_text']}" + (f" ({', '.join(suffix)})" if suffix else "")
    source = row.get("source_label") or row.get("source_metric_code")
    target = row.get("target_label") or row.get("target_metric_code")
    if source and target:
        return f"{source} -> {target}"
    label = row.get("metric_label") or row.get("label") or row.get("metric_code") or row.get("code")
    if label:
        return str(label)
    return str(row.get("id") or "row")


def compact_trace_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "source_metric_code",
        "source_label",
        "target_metric_code",
        "target_label",
        "metric_code",
        "metric_label",
        "path_text",
        "hops",
        "total_strength",
        "strength",
        "confidence",
        "score",
        "status",
        "source",
        "edge_type",
        "previous_period",
        "current_period",
        "delta_abs",
        "delta_pct",
        "reason",
        "first_reason",
        "explanation",
    )
    compact = {key: row.get(key) for key in keys if row.get(key) not in (None, "", [])}
    metric_codes = [str(code) for code in row.get("metric_codes") or [] if str(code)]
    if metric_codes:
        compact["metric_codes"] = metric_codes
    return compact


def append_evidence_caveats(answer: str, warnings: list[str]) -> str:
    result = str(answer or "").strip()
    normalized = normalize_user_text(result)
    if "pending_evidence_needs_confirmation" in warnings and not any(
        token in normalized for token in ("провер", "подтвержд", "approval", "pending")
    ):
        result = (
            f"{result} Часть связей требует подтверждения перед использованием как факта.".strip()
        )
    if "weak_evidence" in warnings and "слаб" not in normalized and "низк" not in normalized:
        result = f"{result} Уверенность по части evidence ниже средней.".strip()
    return result


def clarification_payload(clarification: Any) -> dict[str, Any]:
    return {
        "intent": getattr(clarification, "intent", ""),
        "original_question": getattr(clarification, "original_question", ""),
        "slots": [
            {
                "role": getattr(slot, "role", ""),
                "phrase": getattr(slot, "phrase", ""),
                "resolved_code": getattr(slot, "resolved_code", None),
                "options": [
                    option.to_payload() if hasattr(option, "to_payload") else dict(option)
                    for option in list(getattr(slot, "options", []) or [])
                ],
            }
            for slot in list(getattr(clarification, "slots", []) or [])
        ],
    }


def short_label(label: str, *, limit: int = 96) -> str:
    cleaned = re.sub(r"\s+", " ", str(label or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    shortened = cleaned[: limit - 3].rstrip(" ,;:")
    return f"{shortened}..."
