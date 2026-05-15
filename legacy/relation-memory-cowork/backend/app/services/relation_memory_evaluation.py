from __future__ import annotations

from collections import Counter
from typing import Any


APPROVAL_BENCHMARK_EVIDENCE_TYPES = {"statistical", "row_structure", "text"}
APPROVAL_BENCHMARK_EDGE_TYPES = {"driver", "inverse_driver", "component", "lag"}
TARGETED_APPROVAL_EVIDENCE_WEIGHTS = {
    "formula": 0.32,
    "domain_rule": 0.3,
    "memory_prior": 0.28,
    "statistical": 0.2,
    "row_structure": 0.12,
    "text": 0.1,
}


def evaluate_relation_memory_snapshot(
    snapshot: Any,
    *,
    designed_driver_pairs: list[tuple[str, str]] | None = None,
    pending_confirmation_count: int = 0,
) -> dict[str, Any]:
    observations = list(getattr(snapshot, "observations", []))
    hypotheses = list(getattr(snapshot, "hypotheses", []))
    dependencies = list(getattr(snapshot, "dependencies", []))
    metrics = list(getattr(snapshot, "metrics", []))
    inbound = Counter(item.get("target_metric_code") for item in dependencies)
    outbound = Counter(item.get("source_metric_code") for item in dependencies)
    boundary_gap_count = sum(
        1 for item in hypotheses if item.get("mechanism_type") == "boundary_gap"
    )
    observations_with_graph_path = sum(
        1
        for item in observations
        if inbound[item.get("metric_code")] > 0 or outbound[item.get("metric_code")] > 0
    )
    technical_metric_codes = {
        item.get("code")
        for item in metrics
        if _looks_technical(str(item.get("code") or ""))
        or _looks_technical(str(item.get("label") or ""))
    }
    technical_observations = [
        item for item in observations if item.get("metric_code") in technical_metric_codes
    ]
    designed_recall = _designed_driver_recall(hypotheses, designed_driver_pairs or [])
    return {
        "boundary_gap_rate": round(boundary_gap_count / len(hypotheses), 4) if hypotheses else 0.0,
        "observation_graph_coverage": round(observations_with_graph_path / len(observations), 4)
        if observations
        else 0.0,
        "designed_driver_recall": designed_recall,
        "top_k_relation_precision": None,
        "pending_confirmation_count": pending_confirmation_count,
        "technical_row_leakage": round(len(technical_observations) / len(observations), 4)
        if observations
        else 0.0,
        "technical_observation_count": len(technical_observations),
    }


def select_relation_candidates_for_approval(
    relation_candidates: list[dict[str, Any]],
    *,
    limit: int = 25,
    min_score: float = 0.55,
    evidence_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    allowed_evidence_types = evidence_types or APPROVAL_BENCHMARK_EVIDENCE_TYPES
    candidates = [
        dict(item)
        for item in relation_candidates
        if item.get("needs_approval")
        and str(item.get("evidence_type") or "") in allowed_evidence_types
        and str(item.get("edge_type") or "driver") in APPROVAL_BENCHMARK_EDGE_TYPES
        and str(item.get("source_metric_code") or "")
        and str(item.get("target_metric_code") or "")
        and str(item.get("source_metric_code") or "") != str(item.get("target_metric_code") or "")
    ]
    candidates.sort(
        key=lambda item: (
            -_relation_score(item),
            str(item.get("evidence_type") or ""),
            str(item.get("source_metric_code") or ""),
            str(item.get("target_metric_code") or ""),
        )
    )
    selected = [item for item in candidates if _relation_score(item) >= min_score][:limit]
    if not selected and candidates:
        selected = candidates[: min(5, limit)]
    return selected


def select_targeted_relation_candidates_for_approval(
    snapshot: Any,
    relation_candidates: list[dict[str, Any]],
    *,
    total_limit: int = 20,
    per_observation_limit: int = 2,
    min_score: float = 0.45,
    evidence_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    allowed_evidence_types = evidence_types or APPROVAL_BENCHMARK_EVIDENCE_TYPES
    observations = _observations_ranked_for_targeting(snapshot)
    baseline_dependencies = {
        (item.get("source_metric_code"), item.get("target_metric_code"), item.get("edge_type"))
        for item in getattr(snapshot, "dependencies", [])
    }
    candidates_by_target: dict[str, list[dict[str, Any]]] = {}
    for item in relation_candidates:
        if not _relation_candidate_is_approvable(item, allowed_evidence_types):
            continue
        target = str(item.get("target_metric_code") or "")
        source = str(item.get("source_metric_code") or "")
        edge_type = str(item.get("edge_type") or "driver")
        if (source, target, edge_type) in baseline_dependencies:
            continue
        candidates_by_target.setdefault(target, []).append(dict(item))

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str, str]] = set()
    for observation in observations:
        target_metric = str(observation.get("metric_code") or "")
        target_candidates = []
        for candidate in candidates_by_target.get(target_metric, []):
            targeted_score = _targeted_relation_score(candidate, observation, snapshot)
            if targeted_score < min_score:
                continue
            enriched_candidate = dict(candidate)
            enriched_candidate["targeted_score"] = targeted_score
            enriched_candidate["targeted_observation_id"] = observation.get("id")
            enriched_candidate["targeted_observation_metric_code"] = target_metric
            target_candidates.append(enriched_candidate)
        target_candidates.sort(
            key=lambda item: (
                -float(item.get("targeted_score") or 0.0),
                -_relation_score(item),
                str(item.get("source_metric_code") or ""),
            )
        )
        for candidate in target_candidates[:per_observation_limit]:
            key = (
                str(candidate.get("source_metric_code") or ""),
                str(candidate.get("target_metric_code") or ""),
                str(candidate.get("edge_type") or "driver"),
            )
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected.append(candidate)
            if len(selected) >= total_limit:
                return selected
    if not selected:
        return select_relation_candidates_for_approval(
            relation_candidates,
            limit=min(5, total_limit),
            min_score=min_score,
            evidence_types=allowed_evidence_types,
        )
    return selected


def relation_candidates_to_dependency_priors(
    relation_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    priors: list[dict[str, Any]] = []
    for candidate in relation_candidates:
        score = _relation_score(candidate)
        priors.append(
            {
                "source_metric_code": candidate["source_metric_code"],
                "target_metric_code": candidate["target_metric_code"],
                "edge_type": candidate.get("edge_type") or "driver",
                "strength": score,
                "source": f"approval_benchmark_{candidate.get('evidence_type') or 'relation'}",
                "note": candidate.get("evidence")
                or candidate.get("note")
                or "Approved relation candidate.",
            }
        )
    return priors


def approval_benchmark_summary(
    baseline_snapshot: Any,
    approved_snapshot: Any,
    approved_candidates: list[dict[str, Any]],
    *,
    designed_driver_pairs: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    baseline_evaluation = evaluate_relation_memory_snapshot(
        baseline_snapshot,
        designed_driver_pairs=designed_driver_pairs,
        pending_confirmation_count=len(approved_candidates),
    )
    approved_evaluation = evaluate_relation_memory_snapshot(
        approved_snapshot,
        designed_driver_pairs=designed_driver_pairs,
        pending_confirmation_count=0,
    )
    coverage_diagnostics = _approval_coverage_diagnostics(baseline_snapshot, approved_candidates)
    return {
        "approved_relation_count": len(approved_candidates),
        "approved_relation_evidence_types": dict(
            Counter(str(item.get("evidence_type") or "") for item in approved_candidates)
        ),
        "approved_relation_edge_types": dict(
            Counter(str(item.get("edge_type") or "") for item in approved_candidates)
        ),
        "approval_coverage_diagnostics": coverage_diagnostics,
        "baseline_counts": _snapshot_counts(baseline_snapshot),
        "after_approval_counts": _snapshot_counts(approved_snapshot),
        "baseline_evaluation": baseline_evaluation,
        "after_approval_evaluation": approved_evaluation,
        "delta": {
            "dependency_count": _count_delta(approved_snapshot, baseline_snapshot, "dependencies"),
            "hypothesis_count": _count_delta(approved_snapshot, baseline_snapshot, "hypotheses"),
            "boundary_gap_rate": _numeric_delta(
                approved_evaluation.get("boundary_gap_rate"),
                baseline_evaluation.get("boundary_gap_rate"),
            ),
            "observation_graph_coverage": _numeric_delta(
                approved_evaluation.get("observation_graph_coverage"),
                baseline_evaluation.get("observation_graph_coverage"),
            ),
            "designed_driver_recall_ratio": _numeric_delta(
                (approved_evaluation.get("designed_driver_recall") or {}).get("ratio"),
                (baseline_evaluation.get("designed_driver_recall") or {}).get("ratio"),
            ),
        },
        "approved_relations": [
            {
                "source_metric_code": item.get("source_metric_code"),
                "target_metric_code": item.get("target_metric_code"),
                "edge_type": item.get("edge_type"),
                "relation_type": item.get("relation_type"),
                "score": _relation_score(item),
                "targeted_score": item.get("targeted_score"),
                "targeted_observation_id": item.get("targeted_observation_id"),
                "evidence_type": item.get("evidence_type"),
                "evidence": item.get("evidence"),
            }
            for item in approved_candidates
        ],
    }


def _designed_driver_recall(
    hypotheses: list[dict[str, Any]], pairs: list[tuple[str, str]]
) -> dict[str, Any]:
    items = []
    for source, target in pairs:
        found = any(_hypothesis_contains_path(item, source, target) for item in hypotheses)
        items.append({"source": source, "target": target, "found": found})
    return {
        "found": sum(1 for item in items if item["found"]),
        "total": len(items),
        "ratio": round(sum(1 for item in items if item["found"]) / len(items), 4)
        if items
        else None,
        "items": items,
    }


def _hypothesis_contains_path(hypothesis: dict[str, Any], source: str, target: str) -> bool:
    metric_codes = list(hypothesis.get("metric_codes") or [])
    if source not in metric_codes or target not in metric_codes:
        return False
    return metric_codes.index(source) < metric_codes.index(target)


def _looks_technical(value: str) -> bool:
    lowered = value.lower()
    return any(
        token in lowered for token in ("проверка", "technical_check", "контроль", "расшифровка")
    )


def _relation_score(candidate: dict[str, Any]) -> float:
    try:
        return round(float(candidate.get("score") or candidate.get("confidence") or 0.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _relation_candidate_is_approvable(candidate: dict[str, Any], evidence_types: set[str]) -> bool:
    return (
        bool(candidate.get("needs_approval"))
        and str(candidate.get("evidence_type") or "") in evidence_types
        and str(candidate.get("edge_type") or "driver") in APPROVAL_BENCHMARK_EDGE_TYPES
        and bool(str(candidate.get("source_metric_code") or ""))
        and bool(str(candidate.get("target_metric_code") or ""))
        and str(candidate.get("source_metric_code") or "")
        != str(candidate.get("target_metric_code") or "")
    )


def _observations_ranked_for_targeting(snapshot: Any) -> list[dict[str, Any]]:
    boundary_targets = {
        item.get("target_metric_code")
        for item in getattr(snapshot, "hypotheses", [])
        if item.get("mechanism_type") == "boundary_gap"
    }
    observations = []
    for observation in getattr(snapshot, "observations", []):
        item = dict(observation)
        item["has_boundary_gap"] = item.get("metric_code") in boundary_targets
        observations.append(item)
    observations.sort(
        key=lambda item: (
            not bool(item.get("has_boundary_gap")),
            -float(item.get("score") or 0.0),
            str(item.get("metric_code") or ""),
        )
    )
    return observations


def _targeted_relation_score(
    candidate: dict[str, Any], observation: dict[str, Any], snapshot: Any
) -> float:
    relation_score = _relation_score(candidate)
    evidence_weight = TARGETED_APPROVAL_EVIDENCE_WEIGHTS.get(
        str(candidate.get("evidence_type") or ""), 0.06
    )
    target_boost = (
        0.25 if candidate.get("target_metric_code") == observation.get("metric_code") else 0.0
    )
    boundary_boost = 0.2 if observation.get("has_boundary_gap") else 0.0
    source_support = _source_signal_support(candidate, observation, snapshot)
    observation_score = min(0.18, float(observation.get("score") or 0.0) / 20)
    return round(
        min(
            1.0,
            relation_score * 0.52
            + evidence_weight
            + target_boost
            + boundary_boost
            + source_support
            + observation_score,
        ),
        4,
    )


def _source_signal_support(
    candidate: dict[str, Any], observation: dict[str, Any], snapshot: Any
) -> float:
    source_metric = str(candidate.get("source_metric_code") or "")
    if not source_metric:
        return 0.0
    source_observation = next(
        (
            item
            for item in getattr(snapshot, "observations", [])
            if item.get("metric_code") == source_metric
        ),
        None,
    )
    if not source_observation:
        return 0.0
    source_sign = int(source_observation.get("direction_sign") or 0)
    target_sign = int(observation.get("direction_sign") or 0)
    if source_sign == 0 or target_sign == 0:
        return 0.0
    edge_type = str(candidate.get("edge_type") or "driver")
    expected_source_sign = -target_sign if edge_type == "inverse_driver" else target_sign
    if source_sign == expected_source_sign:
        return 0.18
    return -0.08


def _snapshot_counts(snapshot: Any) -> dict[str, int]:
    return {
        "dataset_count": len(getattr(snapshot, "datasets", [])),
        "metric_count": len(getattr(snapshot, "metrics", [])),
        "dependency_count": len(getattr(snapshot, "dependencies", [])),
        "observation_count": len(getattr(snapshot, "observations", [])),
        "hypothesis_count": len(getattr(snapshot, "hypotheses", [])),
    }


def _approval_coverage_diagnostics(
    snapshot: Any, approved_candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    boundary_gap_targets = sorted(
        {
            str(item.get("target_metric_code") or "")
            for item in getattr(snapshot, "hypotheses", [])
            if item.get("mechanism_type") == "boundary_gap" and item.get("target_metric_code")
        }
    )
    approved_targets = sorted(
        {
            str(item.get("target_metric_code") or "")
            for item in approved_candidates
            if item.get("target_metric_code")
        }
    )
    covered_targets = sorted(set(boundary_gap_targets).intersection(approved_targets))
    uncovered_targets = sorted(set(boundary_gap_targets).difference(approved_targets))
    return {
        "boundary_gap_target_count": len(boundary_gap_targets),
        "approved_relation_target_count": len(approved_targets),
        "covered_boundary_gap_target_count": len(covered_targets),
        "uncovered_boundary_gap_target_count": len(uncovered_targets),
        "covered_boundary_gap_targets": covered_targets,
        "uncovered_boundary_gap_targets": uncovered_targets,
    }


def _count_delta(after_snapshot: Any, before_snapshot: Any, attribute: str) -> int:
    return len(getattr(after_snapshot, attribute, [])) - len(
        getattr(before_snapshot, attribute, [])
    )


def _numeric_delta(after: Any, before: Any) -> float | None:
    if after is None or before is None:
        return None
    try:
        return round(float(after) - float(before), 4)
    except (TypeError, ValueError):
        return None
