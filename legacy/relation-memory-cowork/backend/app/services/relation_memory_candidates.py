from __future__ import annotations

from typing import Any

from app.services.relation_memory_ingestion import normalize_metric_code


def candidate_id(candidate: dict[str, Any]) -> str:
    parts = [
        candidate["source_metric_code"],
        candidate["target_metric_code"],
        candidate["edge_type"],
        str(candidate.get("lag_period") or ""),
        str(candidate.get("source") or ""),
    ]
    return normalize_metric_code("__".join(parts))


def relation_key(candidate: Any) -> tuple[str, str, str]:
    def value(name: str, default: str = "") -> Any:
        if isinstance(candidate, dict):
            return candidate.get(name, default)
        return getattr(candidate, name, default)

    return (
        str(value("source_metric_code") or ""),
        str(value("target_metric_code") or ""),
        str(value("edge_type", "driver") or "driver"),
    )


def relation_key_set(priors: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    return {key for prior in priors if (key := relation_key(prior))[0] and key[1]}


def candidate_relations_from_hypotheses(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    ranked_hypotheses = sorted(
        (item for item in hypotheses if item.get("mechanism_type") != "boundary_gap"),
        key=lambda item: (-float(item.get("confidence") or 0.0), int(item.get("hops") or 0)),
    )
    for hypothesis in ranked_hypotheses[:5]:
        metric_codes = hypothesis.get("metric_codes", [])
        edge_types = hypothesis.get("path_edge_types", [])
        for index, (source, target) in enumerate(zip(metric_codes, metric_codes[1:], strict=False)):
            edge_type = edge_types[index] if index < len(edge_types) else "driver"
            candidates.append(
                {
                    "source_metric_code": source,
                    "target_metric_code": target,
                    "edge_type": edge_type,
                    "note": hypothesis.get("explanation", ""),
                    "evidence": "deterministic hypothesis path",
                    "confidence": hypothesis.get("confidence", 0.0),
                    "source": "deterministic_hypothesis",
                    "source_hypothesis_id": hypothesis.get("id"),
                }
            )
    return candidates


def candidate_relations_from_dependencies(
    dependencies: list[dict[str, Any]],
    *,
    row_relation_source: str,
    limit: int,
) -> list[dict[str, Any]]:
    row_dependencies = [
        dependency for dependency in dependencies if dependency.get("source") == row_relation_source
    ]
    row_dependencies.sort(
        key=lambda item: (
            -float(item.get("strength") or 0.0),
            str(item.get("target_metric_code") or ""),
            str(item.get("source_metric_code") or ""),
        )
    )
    return [
        {
            "source_metric_code": dependency.get("source_metric_code"),
            "target_metric_code": dependency.get("target_metric_code"),
            "edge_type": dependency.get("edge_type") or "component",
            "note": dependency.get("reason") or "",
            "evidence": "row hierarchy in uploaded Excel report",
            "confidence": dependency.get("strength", 0.7),
            "source": row_relation_source,
        }
        for dependency in row_dependencies[:limit]
    ]
