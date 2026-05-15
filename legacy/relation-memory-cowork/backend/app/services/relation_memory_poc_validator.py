from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.utils.text import extract_keywords


def _normalize_text(value: str) -> str:
    lowered = value.lower().replace("_", " ")
    lowered = re.sub(r"[^a-zа-яё0-9 ]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _extract_primary_phrase(question: str) -> str | None:
    patterns = [
        re.compile(r"что будет с\s+(.+?)\s*,?\s*если", re.IGNORECASE),
        re.compile(r"сравни\s+(.+?)\s+по\s+", re.IGNORECASE),
        re.compile(r"сравни\s+(.+?)\s+между\s+", re.IGNORECASE),
        re.compile(r"как\s+.+?\s+влияет на\s+(.+?)(?:\s+в\s+|\s+по\s+|$)", re.IGNORECASE),
        re.compile(
            r"почему\s+.+?(\bgross margin\b|\brevenue\b|\btotal cost\b|\bstockout rate\b|\breturn rate\b)",
            re.IGNORECASE,
        ),
    ]
    for pattern in patterns:
        match = pattern.search(question)
        if match:
            return _normalize_text(match.group(1))
    return None


def _extract_secondary_phrase(question: str) -> str | None:
    patterns = [
        re.compile(r"если\s+(.+?)\s+(?:вырастет|снизится|упадет|упадёт|изменится)", re.IGNORECASE),
        re.compile(r"как\s+(.+?)\s+влияет на\s+.+?(?:\s+в\s+|\s+по\s+|$)", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(question)
        if match:
            return _normalize_text(match.group(1))
    return None


class RelationMemoryPocValidator:
    MAX_UPSTREAM_HOPS = 3
    MAX_PATH_HOPS = 4

    def __init__(self, manifest_path: str | Path, graph_client):
        self.manifest_path = Path(manifest_path)
        self.graph_client = graph_client
        with self.manifest_path.open("r", encoding="utf-8") as handle:
            self.manifest = yaml.safe_load(handle)

    def validate(self) -> dict[str, Any]:
        catalog = self.graph_client.read_catalog()
        query_results = [self._evaluate_query(item, catalog) for item in self.manifest.get("golden_queries", [])]
        passed = sum(1 for item in query_results if item["passed"])
        return {
            "manifest": str(self.manifest_path),
            "golden_query_count": len(query_results),
            "passed_query_count": passed,
            "failed_query_count": len(query_results) - passed,
            "results": query_results,
        }

    def _evaluate_query(self, query: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
        resolved = self._resolve_question(str(query["question"]), catalog)
        expected = query.get("expects", {})
        comparisons = {
            "primary_metrics": self._compare_subset(expected.get("primary_metrics", []), resolved["primary_metrics"]),
            "secondary_metrics": self._compare_subset(expected.get("secondary_metrics", []), resolved["secondary_metrics"]),
            "datasets": self._compare_subset(expected.get("datasets", []), resolved["datasets"]),
            "entity_filters": self._compare_subset(expected.get("entity_filters", []), resolved["entity_filters"]),
            "period_filters": self._compare_subset(expected.get("period_filters", []), resolved["period_filters"]),
            "dependency_path": self._compare_subset(expected.get("dependency_path", []), resolved["dependency_path"]),
        }
        return {
            "id": query["id"],
            "question": query["question"],
            "passed": all(item["passed"] for item in comparisons.values()),
            "resolved": resolved,
            "checks": comparisons,
        }

    def _compare_subset(self, expected: list[str], actual: list[str]) -> dict[str, Any]:
        expected_norm = [str(item) for item in expected]
        actual_norm = [str(item) for item in actual]
        missing = [item for item in expected_norm if item not in actual_norm]
        return {
            "expected": expected_norm,
            "actual": actual_norm,
            "missing": missing,
            "passed": not missing,
        }

    def _resolve_question(self, question: str, catalog: dict[str, Any]) -> dict[str, Any]:
        metrics = self._resolve_metrics(question, catalog)
        primary_metrics = [metrics[0]["code"]] if metrics else []
        secondary_metrics = self._resolve_secondary_metrics(question, primary_metrics, catalog)
        datasets = self._resolve_datasets(primary_metrics + secondary_metrics, catalog)
        entity_filters, period_filters = self._resolve_filters(question, catalog)
        dependency_chains = self._resolve_dependency_chains(primary_metrics, secondary_metrics, catalog)
        dependency_path = self._flatten_dependency_chains(dependency_chains)
        return {
            "primary_metrics": primary_metrics,
            "secondary_metrics": secondary_metrics,
            "datasets": datasets,
            "entity_filters": entity_filters,
            "period_filters": period_filters,
            "dependency_path": dependency_path,
            "dependency_chains": dependency_chains,
        }

    def _resolve_metrics(self, question: str, catalog: dict[str, Any]) -> list[dict[str, Any]]:
        normalized_question = _normalize_text(question)
        keywords = set(extract_keywords(question))
        primary_phrase = _extract_primary_phrase(question)
        scored = []
        for metric in catalog["metrics"]:
            score = 0
            matched_terms: list[str] = []
            raw_terms = [metric["code"], metric["label"], *metric.get("aliases", [])]
            normalized_terms = [_normalize_text(term) for term in raw_terms if term]
            for term in normalized_terms:
                if not term:
                    continue
                if " " in term:
                    if term in normalized_question:
                        matched_terms.append(term)
                        score += 8
                else:
                    if term in keywords:
                        matched_terms.append(term)
                        score += 6
                if primary_phrase and term and term in primary_phrase:
                    score += 12
            term_keywords = set()
            for term in raw_terms:
                term_keywords.update(extract_keywords(str(term)))
            overlap = sorted(keywords & term_keywords)
            score += len(overlap) * 2
            matched_terms.extend(overlap)
            if score <= 0:
                continue
            scored.append(
                {
                    "code": metric["code"],
                    "score": score,
                    "matched_terms": list(dict.fromkeys(matched_terms)),
                }
            )
        scored.sort(key=lambda item: (-item["score"], item["code"]))
        return scored

    def _resolve_secondary_metrics(
        self,
        question: str,
        primary_metrics: list[str],
        catalog: dict[str, Any],
    ) -> list[str]:
        if not primary_metrics:
            return []
        normalized_question = _normalize_text(question)
        question_keywords = set(extract_keywords(question))
        secondary_phrase = _extract_secondary_phrase(question)
        ranked: list[tuple[float, str]] = []
        for item in self._find_upstream_metrics(primary_metrics, catalog):
            source_metric = catalog["metric_map"].get(item["source_metric_code"])
            if not source_metric:
                continue
            hops = int(item.get("min_hops") or 1)
            total_strength = float(item.get("total_strength") or 0.0)
            score = total_strength / max(hops, 1)
            if hops == 1:
                score += 3.0
            elif hops == 2:
                score += 1.5
            elif hops == 3:
                score += 0.5
            raw_terms = [source_metric["code"], source_metric["label"], *source_metric.get("aliases", [])]
            for term in raw_terms:
                normalized = _normalize_text(str(term))
                if not normalized:
                    continue
                if " " in normalized and normalized in normalized_question:
                    score += 2.0
                elif normalized in question_keywords:
                    score += 1.5
                if secondary_phrase and normalized and normalized in secondary_phrase:
                    score += 4.0
            ranked.append((score, source_metric["code"]))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        ordered = [code for _, code in ranked if code not in primary_metrics]
        return list(dict.fromkeys(ordered))[:5]

    def _find_upstream_metrics(
        self,
        primary_metrics: list[str],
        catalog: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not primary_metrics:
            return []
        if hasattr(self.graph_client, "find_upstream_metrics"):
            found = self.graph_client.find_upstream_metrics(
                primary_metrics,
                max_hops=self.MAX_UPSTREAM_HOPS,
                limit=200,
            )
            if found:
                return found

        reverse_edges: dict[str, list[dict[str, Any]]] = {}
        for dependency in catalog.get("dependencies", []):
            reverse_edges.setdefault(dependency["target_metric_code"], []).append(dependency)

        ranked: dict[tuple[str, str], dict[str, Any]] = {}
        for target_metric in primary_metrics:
            queue: list[tuple[str, list[str], float]] = [(target_metric, [target_metric], 0.0)]
            while queue:
                current_metric, path, total_strength = queue.pop(0)
                if len(path) > self.MAX_UPSTREAM_HOPS + 1:
                    continue
                for dependency in reverse_edges.get(current_metric, []):
                    source_metric = dependency["source_metric_code"]
                    if source_metric in path:
                        continue
                    next_path = [source_metric, *path]
                    next_strength = total_strength + float(dependency.get("strength") or 0.0)
                    record_key = (source_metric, target_metric)
                    candidate = {
                        "source_metric_code": source_metric,
                        "target_metric_code": target_metric,
                        "min_hops": len(next_path) - 1,
                        "total_strength": next_strength,
                    }
                    current_best = ranked.get(record_key)
                    if current_best is None or (
                        candidate["min_hops"] < current_best["min_hops"]
                        or (
                            candidate["min_hops"] == current_best["min_hops"]
                            and candidate["total_strength"] > current_best["total_strength"]
                        )
                    ):
                        ranked[record_key] = candidate
                    if len(next_path) - 1 < self.MAX_UPSTREAM_HOPS:
                        queue.append((source_metric, next_path, next_strength))
        return sorted(
            ranked.values(),
            key=lambda item: (item["min_hops"], -float(item["total_strength"]), item["source_metric_code"]),
        )

    def _resolve_datasets(self, metric_codes: list[str], catalog: dict[str, Any]) -> list[str]:
        scores: dict[str, int] = {}
        for metric_code in metric_codes:
            for dataset_key in catalog["metric_datasets"].get(metric_code, []):
                scores[dataset_key] = scores.get(dataset_key, 0) + 1
        return [item[0] for item in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]

    def _resolve_filters(self, question: str, catalog: dict[str, Any]) -> tuple[list[str], list[str]]:
        normalized_question = _normalize_text(question)
        question_tokens = set(re.findall(r"[a-zа-яё0-9_]+", question.lower()))
        entity_filters: list[str] = []
        period_filters: list[str] = []
        month_matches = re.findall(r"\b20\d{2}-\d{2}\b", question)
        for value in month_matches:
            period_filters.append(f"month={value}")

        for entity in catalog["entity_values"]:
            if entity["dimension_key"] == "month":
                continue
            values = [entity["value"], entity["label"], *entity.get("aliases", [])]
            for value in values:
                normalized = _normalize_text(str(value))
                if not normalized:
                    continue
                if len(normalized) <= 3:
                    if normalized in question_tokens:
                        entity_filters.append(f"{entity['dimension_key']}={entity['value']}")
                        break
                elif normalized in normalized_question:
                    entity_filters.append(f"{entity['dimension_key']}={entity['value']}")
                    break
        return list(dict.fromkeys(entity_filters)), list(dict.fromkeys(period_filters))

    def _resolve_dependency_chains(
        self,
        primary_metrics: list[str],
        secondary_metrics: list[str],
        catalog: dict[str, Any],
    ) -> list[str]:
        if not primary_metrics or not secondary_metrics:
            return []
        path_records = self._find_dependency_paths(secondary_metrics, primary_metrics, catalog)
        chains: list[str] = []
        for record in path_records:
            metric_codes = [str(item) for item in record.get("metric_codes", []) if str(item)]
            if len(metric_codes) >= 2:
                chains.append("->".join(metric_codes))
        return list(dict.fromkeys(chains))

    def _flatten_dependency_chains(self, dependency_chains: list[str]) -> list[str]:
        edges: list[str] = []
        for chain in dependency_chains:
            metric_codes = [part.strip() for part in chain.split("->") if part.strip()]
            for left, right in zip(metric_codes, metric_codes[1:]):
                edges.append(f"{left}->{right}")
        return list(dict.fromkeys(edges))

    def _find_dependency_paths(
        self,
        source_metrics: list[str],
        target_metrics: list[str],
        catalog: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not source_metrics or not target_metrics:
            return []
        if hasattr(self.graph_client, "find_dependency_paths"):
            found = self.graph_client.find_dependency_paths(
                source_metrics,
                target_metrics,
                max_hops=self.MAX_PATH_HOPS,
                limit=50,
            )
            if found:
                return found

        adjacency: dict[str, list[dict[str, Any]]] = {}
        for dependency in catalog.get("dependencies", []):
            adjacency.setdefault(dependency["source_metric_code"], []).append(dependency)

        path_records: list[dict[str, Any]] = []
        seen_paths: set[tuple[str, str, tuple[str, ...]]] = set()
        for source_metric in source_metrics:
            for target_metric in target_metrics:
                if source_metric == target_metric:
                    continue
                queue: list[tuple[str, list[str], float]] = [(source_metric, [source_metric], 0.0)]
                while queue:
                    current_metric, metric_codes, total_strength = queue.pop(0)
                    if len(metric_codes) > self.MAX_PATH_HOPS + 1:
                        continue
                    if current_metric == target_metric and len(metric_codes) > 1:
                        path_key = (source_metric, target_metric, tuple(metric_codes))
                        if path_key not in seen_paths:
                            seen_paths.add(path_key)
                            path_records.append(
                                {
                                    "source_code": source_metric,
                                    "target_code": target_metric,
                                    "metric_codes": metric_codes,
                                    "hops": len(metric_codes) - 1,
                                    "total_strength": total_strength,
                                }
                            )
                        continue
                    for dependency in adjacency.get(current_metric, []):
                        next_metric = dependency["target_metric_code"]
                        if next_metric in metric_codes:
                            continue
                        queue.append(
                            (
                                next_metric,
                                [*metric_codes, next_metric],
                                total_strength + float(dependency.get("strength") or 0.0),
                            )
                        )
        return sorted(
            path_records,
            key=lambda item: (item["hops"], -float(item["total_strength"]), item["source_code"], item["target_code"]),
        )[:50]
