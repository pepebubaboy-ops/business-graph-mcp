from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Callable

from app.schemas.relation_memory import CandidateRelation
from app.services.relation_memory_agent import (
    RelationMemoryAnswerNarrator,
    RelationMemoryExternalContextSuggester,
)
from app.services.relation_memory_question_models import (
    DEFAULT_GRAPH_PATH_MAX_HOPS,
    RELATION_GRAPH_PATH_MAX_HOPS,
    RUSSIAN_METRIC_ALIASES,
    RUSSIAN_METRIC_DISPLAY_LABELS,
    AnswerDraft,
    EvidenceBundle,
    EvidenceClaim,
    GraphQuestionAnswer,
    MetricResolution,
    QuestionPlan,
    ResolvedQuestionMetric,
)
from app.services.relation_memory_question_planner import QuestionPlanner
from app.services.relation_memory_question_trace import (
    answer_trace_event,
    append_evidence_caveats,
    clarification_payload,
    emit_trace_event,
    evidence_trace_event,
    planner_trace_event,
    resolver_trace_event,
)
from app.services.relation_memory_user_language import (
    RelationMemoryConversationContext,
    RelationMemoryUserLanguageResolver,
    normalize_user_text,
)


class MetricResolver:
    def __init__(self, metric_map: dict[str, dict[str, Any]]):
        self.resolver = RelationMemoryUserLanguageResolver(metric_map)

    def resolve(
        self,
        *,
        plan: QuestionPlan,
        conversation_context: RelationMemoryConversationContext | None,
    ) -> MetricResolution:
        normalized = self.resolver.resolve(question=plan.raw_question, conversation_context=conversation_context)
        intent = normalized.intent
        if plan.intent == "change_cause" and normalized.intent in {"upstream", "unknown"}:
            intent = "change_cause"
        elif normalized.intent == "unknown" and plan.intent not in {"unknown", "statement"}:
            intent = plan.intent
        elif plan.intent == "statement" and normalized.intent == "unknown":
            intent = "statement"
        if self._should_apply_plan_slots(plan=plan, normalized=normalized):
            planned_metrics = self._resolve_plan_slots(plan)
            if planned_metrics:
                normalized.resolved_metrics = planned_metrics
                normalized.matched_metrics = [candidate.to_payload() for candidate in planned_metrics]
                normalized.handled = True
                normalized.clarification_needed = False
                normalized.clarification = None
                normalized.warnings = [
                    warning
                    for warning in normalized.warnings
                    if warning != "clarification_resolved_from_context"
                ]
                normalized.updated_context.pending_clarification = None
                normalized.updated_context.last_resolved_intent = intent
                normalized.updated_context.last_resolved_metric_codes = [candidate.code for candidate in planned_metrics]
                normalized.updated_context.source_utterance = plan.raw_question
        metrics = [ResolvedQuestionMetric.from_payload(item) for item in normalized.matched_metrics]
        return MetricResolution(
            plan=plan,
            normalized=normalized,
            intent=intent,
            metrics=metrics,
            handled=bool(normalized.handled or intent not in {"unknown", "statement"}),
        )

    def build_clarification_answer(self, clarification: Any) -> str:
        return self.resolver.build_clarification_answer(clarification)

    def _should_apply_plan_slots(self, *, plan: QuestionPlan, normalized: Any) -> bool:
        if plan.intent in {"unknown", "statement"} or not self._slots_for_plan(plan):
            return False
        if not normalized.matched_metrics:
            return True
        if plan.intent != normalized.intent:
            return True
        return any(str(item.get("match_reason") or "") == "context_fallback" for item in normalized.matched_metrics)

    def _resolve_plan_slots(self, plan: QuestionPlan) -> list[Any]:
        resolved_metrics = []
        for slot in self._slots_for_plan(plan):
            role = str(slot.get("role") or "metric")
            phrase = str(slot.get("phrase") or "").strip()
            if not phrase:
                return []
            resolved, _candidates = self.resolver._resolve_metric_phrase(phrase=phrase, role=role)
            if resolved is None:
                return []
            resolved_metrics.append(resolved)
        if plan.intent == "relation_why":
            codes = [candidate.code for candidate in resolved_metrics]
            if len(codes) < 2 or codes[0] == codes[1]:
                return []
        return resolved_metrics

    def _slots_for_plan(self, plan: QuestionPlan) -> list[dict[str, str]]:
        if plan.metric_slots:
            return plan.metric_slots
        if plan.intent in {"upstream", "downstream", "change_cause"}:
            return [{"role": "metric", "phrase": plan.raw_question}]
        return []


class EvidenceRetriever:
    def build_bundle(self, answer: GraphQuestionAnswer) -> EvidenceBundle:
        claims = [self._claim_from_row(answer.intent, row) for row in answer.rows]
        confidence = answer.confidence if answer.confidence is not None else self._confidence_from_claims(answer, claims)
        return EvidenceBundle(
            intent=answer.intent,
            fallback_answer=answer.answer,
            matched_metrics=answer.matched_metrics,
            rows=answer.rows,
            claims=claims,
            cypher_hint=answer.cypher_hint,
            warnings=self._caveats_for_claims(claims),
            confidence=confidence,
            clarification=answer.clarification,
        )

    def _claim_from_row(self, intent: str, row: dict[str, Any]) -> EvidenceClaim:
        claim_type = str(row.get("status") or ("path" if row.get("metric_codes") else intent))
        metric_codes = [str(code) for code in row.get("metric_codes") or [] if str(code)]
        if not metric_codes:
            for key in ("source_metric_code", "target_metric_code", "metric_code", "code"):
                value = str(row.get(key) or "")
                if value:
                    metric_codes.append(value)
        return EvidenceClaim(
            claim_type=claim_type,
            text=self._claim_text(intent, row),
            row=row,
            confidence=self._row_confidence(row),
            metric_codes=list(dict.fromkeys(metric_codes)),
            status=str(row.get("status") or ""),
            source=str(row.get("source") or row.get("evidence_type") or ""),
        )

    def _claim_text(self, intent: str, row: dict[str, Any]) -> str:
        if row.get("path_text"):
            return str(row["path_text"])
        source = row.get("source_label") or row.get("source_metric_code")
        target = row.get("target_label") or row.get("target_metric_code")
        if source and target:
            return f"{source} -> {target}"
        label = row.get("metric_label") or row.get("label") or row.get("code") or row.get("metric_code")
        if label:
            return str(label)
        return intent

    def _row_confidence(self, row: dict[str, Any]) -> float | None:
        for key in ("confidence", "score", "strength", "total_strength"):
            if row.get(key) is None:
                continue
            try:
                value = float(row.get(key) or 0.0)
            except (TypeError, ValueError):
                continue
            if key == "total_strength":
                hops = max(1, int(row.get("hops") or 1))
                value = value / hops
            return round(max(0.0, min(1.0, value)), 3)
        return None

    def _confidence_from_claims(self, answer: GraphQuestionAnswer, claims: list[EvidenceClaim]) -> float | None:
        values = [float(claim.confidence) for claim in claims if claim.confidence is not None]
        if values:
            return round(max(values), 3)
        if not answer.handled:
            return None
        if answer.intent in {"upstream", "downstream", "relation_why", "change_cause"}:
            return 0.0
        return 1.0

    def _caveats_for_claims(self, claims: list[EvidenceClaim]) -> list[str]:
        warnings = []
        if any(claim.status == "pending_confirmation" for claim in claims):
            warnings.append("pending_evidence_needs_confirmation")
        if any(claim.confidence is not None and claim.confidence < 0.5 for claim in claims):
            warnings.append("weak_evidence")
        return warnings


class AnswerComposer:
    def __init__(self, *, answer_narrator: RelationMemoryAnswerNarrator | None = None):
        self.answer_narrator = answer_narrator

    def compose(self, *, question: str, bundle: EvidenceBundle) -> AnswerDraft:
        if any(str(row.get("status") or "") == "external_context_candidate" for row in bundle.rows):
            return AnswerDraft(answer=bundle.fallback_answer, answer_mode="llm_grounded", warnings=[])
        if (
            self.answer_narrator
            and getattr(self.answer_narrator, "enabled", False)
            and bundle.rows
            and bundle.intent in {"upstream", "downstream", "relation_why"}
        ):
            narration = self.answer_narrator.narrate(
                question=question,
                intent=bundle.intent,
                fallback_answer=bundle.fallback_answer,
                matched_metrics=bundle.matched_metrics,
                rows=bundle.rows,
            )
            mode = "llm_grounded" if not narration.warnings else "deterministic"
            return AnswerDraft(answer=narration.answer, answer_mode=mode, warnings=list(narration.warnings))
        return AnswerDraft(answer=bundle.fallback_answer, answer_mode="deterministic", warnings=[])


class GroundingValidator:
    def __init__(self, metric_map: dict[str, dict[str, Any]]):
        self.metric_map = metric_map

    def validate(self, *, draft: AnswerDraft, bundle: EvidenceBundle) -> AnswerDraft:
        if draft.answer_mode != "llm_grounded":
            return draft
        if (
            self._mentions_forbidden_metric(draft.answer, bundle)
            or self._mentions_forbidden_number(draft.answer, bundle)
            or self._mentions_non_display_metric_label(draft.answer, bundle)
        ):
            return AnswerDraft(
                answer=bundle.fallback_answer,
                answer_mode="fallback",
                warnings=[*draft.warnings, "grounding_validator_fallback"],
            )
        return draft

    def _mentions_forbidden_metric(self, answer: str, bundle: EvidenceBundle) -> bool:
        normalized_answer = normalize_user_text(answer)
        allowed_codes = {
            str(code)
            for claim in bundle.claims
            for code in claim.metric_codes
            if str(code)
        }
        allowed_codes.update(str(item.get("code") or "") for item in bundle.matched_metrics if item.get("code"))
        known_metrics = {
            **self.metric_map,
            **{
                code: {
                    "label": label,
                    "aliases": RUSSIAN_METRIC_ALIASES.get(code, []),
                    "approved_aliases": [],
                }
                for code, label in RUSSIAN_METRIC_DISPLAY_LABELS.items()
                if code not in self.metric_map
            },
        }
        allowed_mentions = self._allowed_metric_mentions(allowed_codes=allowed_codes, known_metrics=known_metrics, bundle=bundle)
        evidence_text = self._normalized_evidence_text(bundle)
        for code, metric in known_metrics.items():
            if code in allowed_codes:
                continue
            candidates = [code, str(metric.get("label") or "")]
            candidates.extend(str(alias) for alias in metric.get("aliases") or [])
            candidates.extend(str(alias) for alias in metric.get("approved_aliases") or [])
            for candidate in candidates:
                normalized_candidate = normalize_user_text(candidate)
                if len(normalized_candidate) < 4:
                    continue
                if any(
                    normalized_candidate == mention
                    or normalized_candidate in mention
                    or mention in normalized_candidate
                    for mention in allowed_mentions
                ):
                    continue
                if self._contains_grounded_candidate(evidence_text=evidence_text, normalized_candidate=normalized_candidate):
                    continue
                if re.search(rf"(?<![0-9a-zа-яё_]){re.escape(normalized_candidate)}(?![0-9a-zа-яё_])", normalized_answer):
                    return True
        return False

    def _normalized_evidence_text(self, bundle: EvidenceBundle) -> str:
        values: list[str] = []
        for claim in bundle.claims:
            values.append(claim.text)
            self._collect_text_values(claim.row, values)
        return normalize_user_text(" ".join(value for value in values if value))

    def _collect_text_values(self, value: Any, values: list[str]) -> None:
        if isinstance(value, dict):
            for item in value.values():
                self._collect_text_values(item, values)
            return
        if isinstance(value, list):
            for item in value:
                self._collect_text_values(item, values)
            return
        if isinstance(value, str):
            values.append(value)

    def _contains_grounded_candidate(self, *, evidence_text: str, normalized_candidate: str) -> bool:
        if len(normalized_candidate) < 4 or not evidence_text:
            return False
        return bool(
            re.search(rf"(?<![0-9a-zа-яё_]){re.escape(normalized_candidate)}(?![0-9a-zа-яё_])", evidence_text)
        )

    def _allowed_metric_mentions(
        self,
        *,
        allowed_codes: set[str],
        known_metrics: dict[str, dict[str, Any]],
        bundle: EvidenceBundle,
    ) -> set[str]:
        mentions: set[str] = set()
        for code in allowed_codes:
            metric = known_metrics.get(code, {})
            values = [
                code,
                str(metric.get("label") or ""),
                *[str(alias) for alias in metric.get("aliases") or []],
                *[str(alias) for alias in metric.get("approved_aliases") or []],
            ]
            for value in values:
                normalized = normalize_user_text(value)
                if len(normalized) >= 4:
                    mentions.add(normalized)
        for labels in self._display_labels_by_code(bundle).values():
            for label in labels:
                normalized = normalize_user_text(label)
                if len(normalized) >= 4:
                    mentions.add(normalized)
        return mentions

    def _mentions_non_display_metric_label(self, answer: str, bundle: EvidenceBundle) -> bool:
        normalized_answer = normalize_user_text(answer)
        if not normalized_answer:
            return False
        display_labels_by_code = self._display_labels_by_code(bundle)
        if not display_labels_by_code:
            return False
        for code, display_labels in display_labels_by_code.items():
            if not any(self._contains_cyrillic(label) for label in display_labels):
                continue
            metric = self.metric_map.get(code, {})
            original_candidates = [
                code,
                str(metric.get("label") or ""),
                *(str(alias) for alias in metric.get("source_labels") or []),
            ]
            for candidate in original_candidates:
                if not candidate or self._contains_cyrillic(candidate):
                    continue
                normalized_candidate = normalize_user_text(candidate)
                if len(normalized_candidate) < 4:
                    continue
                if any(normalized_candidate == normalize_user_text(label) for label in display_labels):
                    continue
                if re.search(rf"(?<![0-9a-zа-яё_]){re.escape(normalized_candidate)}(?![0-9a-zа-яё_])", normalized_answer):
                    return True
        return False

    def _display_labels_by_code(self, bundle: EvidenceBundle) -> dict[str, set[str]]:
        labels: dict[str, set[str]] = defaultdict(set)
        for item in bundle.matched_metrics:
            code = str(item.get("code") or "")
            label = str(item.get("label") or "")
            if code and label:
                labels[code].add(label)
        for row in bundle.rows:
            source_code = str(row.get("source_metric_code") or "")
            target_code = str(row.get("target_metric_code") or "")
            metric_code = str(row.get("metric_code") or row.get("code") or "")
            if source_code and row.get("source_label"):
                labels[source_code].add(str(row.get("source_label") or ""))
            if target_code and row.get("target_label"):
                labels[target_code].add(str(row.get("target_label") or ""))
            if metric_code and (row.get("metric_label") or row.get("label")):
                labels[metric_code].add(str(row.get("metric_label") or row.get("label") or ""))
        return labels

    def _contains_cyrillic(self, value: str) -> bool:
        return bool(re.search(r"[А-Яа-яЁё]", str(value or "")))

    def _mentions_forbidden_number(self, answer: str, bundle: EvidenceBundle) -> bool:
        mentioned = set(re.findall(r"\d+(?:[.,]\d+)?", answer))
        if not mentioned:
            return False
        allowed: set[str] = set()
        for claim in bundle.claims:
            self._collect_numbers(claim.row, allowed)
        return not mentioned.issubset(allowed)

    def _collect_numbers(self, value: Any, allowed: set[str]) -> None:
        if isinstance(value, dict):
            for item in value.values():
                self._collect_numbers(item, allowed)
            return
        if isinstance(value, list):
            for item in value:
                self._collect_numbers(item, allowed)
            return
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            allowed.add(str(value))
            allowed.add(str(round(float(value), 3)).rstrip("0").rstrip("."))


class RelationMemoryQuestionService:
    def __init__(
        self,
        *,
        answer_narrator: RelationMemoryAnswerNarrator | None = None,
        external_context_suggester: RelationMemoryExternalContextSuggester | None = None,
    ):
        self.answer_narrator = answer_narrator
        self.external_context_suggester = external_context_suggester or RelationMemoryExternalContextSuggester()
        self.planner = QuestionPlanner()
        self.evidence_retriever = EvidenceRetriever()

    def answer(
        self,
        *,
        question: str,
        snapshot: Any,
        metric_map: dict[str, dict[str, Any]],
        pending_confirmations: list[CandidateRelation],
        conversation_context: RelationMemoryConversationContext | None = None,
        trace_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> GraphQuestionAnswer:
        trace_events: list[dict[str, Any]] = []

        def emit(event: dict[str, Any]) -> None:
            emit_trace_event(trace_events=trace_events, trace_sink=trace_sink, event=event)

        plan = self.planner.plan(question=question)
        emit(planner_trace_event(plan))
        metric_resolver = MetricResolver(metric_map)
        resolution = metric_resolver.resolve(plan=plan, conversation_context=conversation_context)
        emit(resolver_trace_event(resolution, RUSSIAN_METRIC_DISPLAY_LABELS))
        resolved = resolution.normalized
        intent = resolution.intent

        def finalize(answer: GraphQuestionAnswer) -> GraphQuestionAnswer:
            return self._finalize_pipeline_answer(
                question=question,
                answer=answer,
                metric_map=metric_map,
                plan=plan,
                resolution=resolution,
                trace_events=trace_events,
                trace_sink=trace_sink,
            )

        if intent == "pending_confirmations":
            answer = self._answer_pending(pending_confirmations)
            answer.updated_context = resolved.updated_context
            return finalize(answer)
        if intent == "relations_overview":
            dependencies = list(getattr(snapshot, "dependencies", []))
            answer = self._answer_relations_overview(
                dependencies,
                self._metric_lookup(metric_map),
                pending_confirmations=pending_confirmations,
            )
            answer.updated_context = resolved.updated_context
            return finalize(answer)
        if intent == "hypotheses_overview":
            answer = self._answer_hypotheses_overview(
                list(getattr(snapshot, "hypotheses", [])),
                self._metric_lookup(metric_map),
            )
            answer.updated_context = resolved.updated_context
            return finalize(answer)
        if intent == "observations_overview":
            answer = self._answer_observations_overview(
                list(getattr(snapshot, "observations", [])),
                self._metric_lookup(metric_map),
            )
            answer.updated_context = resolved.updated_context
            return finalize(answer)
        if intent == "metrics_overview":
            answer = self._answer_metrics_overview(self._metric_lookup(metric_map))
            answer.updated_context = resolved.updated_context
            return finalize(answer)
        if intent in {"unknown", "statement"} or not resolution.handled:
            return finalize(
                GraphQuestionAnswer(
                    handled=False,
                    intent=intent,
                    updated_context=resolved.updated_context,
                    answer_mode="fallback",
                )
            )

        if resolved.clarification_needed and resolved.clarification is not None:
            return finalize(
                GraphQuestionAnswer(
                    handled=True,
                    intent=intent,
                    answer=metric_resolver.build_clarification_answer(resolved.clarification),
                    matched_metrics=resolved.matched_metrics[:6],
                    warnings=resolved.warnings,
                    updated_context=resolved.updated_context,
                    clarification=clarification_payload(resolved.clarification),
                )
            )
        metric_lookup = self._metric_lookup(metric_map)
        matched_metrics = self._localized_matched_metrics(resolved.matched_metrics, metric_lookup)
        resolved_codes = [str(item.get("code") or "") for item in matched_metrics if str(item.get("code") or "")]

        if intent in {"upstream", "downstream"} and not resolved_codes:
            return finalize(
                GraphQuestionAnswer(
                    handled=True,
                    intent=intent,
                    answer="Я понял тип вопроса, но не нашел метрику в текущем графе. Уточните название или metric_code.",
                    warnings=["metric_not_found", *resolved.warnings],
                    updated_context=resolved.updated_context,
                )
            )
        if intent == "relation_why" and len(resolved_codes) < 2:
            return finalize(
                GraphQuestionAnswer(
                    handled=True,
                    intent=intent,
                    answer="Чтобы объяснить связь, нужно уточнить источник и цель отдельно.",
                    matched_metrics=matched_metrics,
                    warnings=["needs_relation_clarification", *resolved.warnings],
                    updated_context=resolved.updated_context,
                )
            )
        if intent == "relation_why" and resolved_codes[0] == resolved_codes[1]:
            return finalize(
                GraphQuestionAnswer(
                    handled=True,
                    intent=intent,
                    answer="Обе части вопроса ссылаются на одну и ту же метрику. Уточните источник и цель отдельно.",
                    matched_metrics=matched_metrics[:1],
                    warnings=["needs_relation_clarification", *resolved.warnings],
                    updated_context=resolved.updated_context,
                )
            )

        dependencies = list(getattr(snapshot, "dependencies", []))
        if intent == "change_cause":
            target = resolved_codes[0] if resolved_codes else ""
            answer = self._answer_change_cause(
                target,
                list(getattr(snapshot, "observations", [])),
                list(getattr(snapshot, "hypotheses", [])),
                metric_lookup,
                requested_direction=plan.requested_direction,
                matched_metrics=matched_metrics,
            )
            answer.warnings = list(dict.fromkeys([*plan.warnings, *resolved.warnings, *answer.warnings]))
            answer.updated_context = resolved.updated_context
            answer.confirmed_aliases = [item.__dict__ for item in resolved.confirmed_aliases]
            return finalize(answer)
        if intent == "upstream":
            target = resolved_codes[0]
            answer = self._answer_upstream(
                target,
                dependencies,
                metric_lookup,
                matched_metrics=matched_metrics,
                question=question,
            )
            answer.warnings = list(dict.fromkeys([*plan.warnings, *resolved.warnings, *answer.warnings]))
            answer.updated_context = resolved.updated_context
            answer.confirmed_aliases = [item.__dict__ for item in resolved.confirmed_aliases]
            return finalize(answer)
        if intent == "downstream":
            source = resolved_codes[0]
            answer = self._answer_downstream(
                source,
                dependencies,
                metric_lookup,
                matched_metrics=matched_metrics,
                question=question,
            )
            answer.warnings = list(dict.fromkeys([*plan.warnings, *resolved.warnings, *answer.warnings]))
            answer.updated_context = resolved.updated_context
            answer.confirmed_aliases = [item.__dict__ for item in resolved.confirmed_aliases]
            return finalize(answer)
        if intent == "relation_why":
            first = resolved_codes[0]
            second = resolved_codes[1]
            answer = self._answer_relation(first, second, dependencies, metric_lookup, matched_metrics=matched_metrics)
            answer.warnings = list(dict.fromkeys([*plan.warnings, *resolved.warnings, *answer.warnings]))
            answer.updated_context = resolved.updated_context
            answer.confirmed_aliases = [item.__dict__ for item in resolved.confirmed_aliases]
            return finalize(answer)
        return finalize(GraphQuestionAnswer(handled=False, updated_context=resolved.updated_context, answer_mode="fallback"))

    def should_answer_as_question(
        self,
        *,
        message: str,
        conversation_context: RelationMemoryConversationContext | None = None,
    ) -> bool:
        plan = self.planner.plan(question=message)
        if plan.intent == "statement":
            return False
        if plan.is_question:
            return True
        return bool(conversation_context and conversation_context.pending_clarification)

    def _finalize_pipeline_answer(
        self,
        *,
        question: str,
        answer: GraphQuestionAnswer,
        metric_map: dict[str, dict[str, Any]],
        plan: QuestionPlan,
        resolution: MetricResolution,
        trace_events: list[dict[str, Any]],
        trace_sink: Callable[[dict[str, Any]], None] | None,
    ) -> GraphQuestionAnswer:
        bundle = self.evidence_retriever.build_bundle(answer)
        emit_trace_event(
            trace_events=trace_events,
            trace_sink=trace_sink,
            event=evidence_trace_event(bundle),
        )
        composer = AnswerComposer(answer_narrator=self.answer_narrator)
        draft = composer.compose(question=question, bundle=bundle)
        validated = GroundingValidator(metric_map).validate(draft=draft, bundle=bundle)
        emit_trace_event(
            trace_events=trace_events,
            trace_sink=trace_sink,
            event=answer_trace_event(
                answer=answer,
                bundle=bundle,
                draft=draft,
                validated=validated,
                resolution=resolution,
                display_labels=RUSSIAN_METRIC_DISPLAY_LABELS,
            ),
        )
        answer.answer = append_evidence_caveats(validated.answer, bundle.warnings)
        answer.answer_mode = validated.answer_mode
        answer.evidence = bundle.evidence
        answer.confidence = bundle.confidence
        answer.clarification = bundle.clarification
        answer.debug_trace = list(trace_events)
        answer.warnings = list(
            dict.fromkeys(
                [
                    *answer.warnings,
                    *plan.warnings,
                    *bundle.warnings,
                    *validated.warnings,
                ]
            )
        )
        return answer

    def _answer_change_cause(
        self,
        target: str,
        observations: list[dict[str, Any]],
        hypotheses: list[dict[str, Any]],
        metric_lookup: dict[str, dict[str, Any]],
        *,
        requested_direction: str = "",
        matched_metrics: list[dict[str, Any]] | None = None,
    ) -> GraphQuestionAnswer:
        if not target:
            return GraphQuestionAnswer(
                handled=True,
                intent="change_cause",
                answer="Я понял вопрос про причину изменения, но не нашел метрику в текущем графе. Уточните название или metric_code.",
                matched_metrics=matched_metrics or [],
                warnings=["metric_not_found"],
            )

        all_target_observations = [
            item
            for item in observations
            if str(item.get("metric_code") or "") == target
        ]
        target_observations = self._filter_observations_by_direction(
            all_target_observations,
            requested_direction=requested_direction,
        )
        target_label = self._metric_label(target, metric_lookup)
        if requested_direction and all_target_observations and not target_observations:
            strongest_observation = sorted(
                all_target_observations,
                key=lambda item: -abs(float(item.get("delta_abs") or 0.0)),
            )[0]
            answer = self._contradictory_change_answer(
                target_label=target_label,
                observation=strongest_observation,
                requested_direction=requested_direction,
            )
            return GraphQuestionAnswer(
                handled=True,
                intent="change_cause",
                answer=answer,
                matched_metrics=matched_metrics or [self._metric_payload(target, metric_lookup)],
                rows=[self._observation_row(strongest_observation, metric_lookup, status="direction_mismatch")],
                confidence=0.0,
                warnings=["requested_change_direction_not_observed"],
            )

        target_hypotheses = [
            item
            for item in hypotheses
            if str(item.get("target_metric_code") or "") == target
            and str(item.get("source_metric_code") or "") not in {"", target}
        ]
        target_hypotheses.sort(
            key=lambda item: (
                -float(item.get("confidence") or 0.0),
                int(item.get("hops") or 0),
                str(item.get("source_metric_code") or ""),
            )
        )
        observation_by_id = {str(item.get("id") or ""): item for item in target_observations}
        rows = []
        for hypothesis in target_hypotheses[:8]:
            source = str(hypothesis.get("source_metric_code") or "")
            metric_codes = [str(code) for code in hypothesis.get("metric_codes") or [] if str(code)]
            if not metric_codes and source:
                metric_codes = [source, target]
            observation = observation_by_id.get(str(hypothesis.get("observation_id") or "")) or (
                target_observations[0] if target_observations else {}
            )
            rows.append(
                {
                    "id": hypothesis.get("id") or "",
                    "observation_id": hypothesis.get("observation_id") or "",
                    "source_metric_code": source,
                    "source_label": self._metric_label(source, metric_lookup) if source else "",
                    "target_metric_code": target,
                    "target_label": self._metric_label(target, metric_lookup),
                    "metric_codes": metric_codes,
                    "path_text": " -> ".join(self._metric_label(code, metric_lookup) for code in metric_codes),
                    "hops": int(hypothesis.get("hops") or max(0, len(metric_codes) - 1)),
                    "confidence": float(hypothesis.get("confidence") or 0.0),
                    "mechanism_type": hypothesis.get("mechanism_type") or "",
                    "support_window": hypothesis.get("support_window") or "",
                    "explanation": hypothesis.get("explanation") or "",
                    "previous_period": observation.get("previous_period") or "",
                    "current_period": observation.get("current_period") or "",
                    "delta_abs": observation.get("delta_abs"),
                    "delta_pct": observation.get("delta_pct"),
                }
            )

        if rows:
            best = rows[0]
            source_label = best.get("source_label") or best.get("source_metric_code") or "драйвер"
            explanation = self._humanize_reason(str(best.get("explanation") or ""))
            answer = (
                f"По изменению «{self._short_label(target_label)}» главный кандидат причины - "
                f"«{self._short_label(str(source_label))}»."
            )
            if best.get("previous_period") and best.get("current_period"):
                answer += f" Это видно на периоде {best['previous_period']} -> {best['current_period']}."
            if explanation:
                answer += f" Основание: {explanation}."
        elif target_observations:
            rows = [self._observation_row(target_observations[0], metric_lookup, status="observation")]
            answer = (
                f"По «{self._short_label(target_label)}» есть наблюдение изменения, "
                "но в текущей сессии нет причинной гипотезы, которую можно уверенно назвать драйвером."
            )
        else:
            answer = (
                f"По «{self._short_label(target_label)}» пока нет наблюдений изменения и причинных гипотез "
                "в текущей сессии."
            )

        return GraphQuestionAnswer(
            handled=True,
            intent="change_cause",
            answer=answer,
            matched_metrics=matched_metrics or [self._metric_payload(target, metric_lookup)],
            rows=rows,
            confidence=self._answer_confidence_from_row(rows[0]) if rows else 0.0,
        )

    def _answer_confidence_from_row(self, row: dict[str, Any]) -> float:
        for key in ("confidence", "score", "strength"):
            value = row.get(key)
            if value is None:
                continue
            try:
                return round(max(0.0, min(1.0, float(value))), 3)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _filter_observations_by_direction(
        self,
        observations: list[dict[str, Any]],
        *,
        requested_direction: str,
    ) -> list[dict[str, Any]]:
        if not requested_direction:
            return observations
        return [
            observation
            for observation in observations
            if self._observation_direction(observation) == requested_direction
        ]

    def _observation_direction(self, observation: dict[str, Any]) -> str:
        try:
            delta_abs = float(observation.get("delta_abs") or 0.0)
        except (TypeError, ValueError):
            delta_abs = 0.0
        if delta_abs > 0:
            return "up"
        if delta_abs < 0:
            return "down"
        return "flat"

    def _contradictory_change_answer(
        self,
        *,
        target_label: str,
        observation: dict[str, Any],
        requested_direction: str,
    ) -> str:
        observed_direction = self._observation_direction(observation)
        observed_text = "снижение" if observed_direction == "down" else "рост" if observed_direction == "up" else "изменения без выраженного направления"
        requested_text = "роста" if requested_direction == "up" else "снижения"
        period = ""
        previous_period = str(observation.get("previous_period") or "")
        current_period = str(observation.get("current_period") or "")
        if previous_period and current_period:
            period = f" на периоде {previous_period} -> {current_period}"
        delta = observation.get("delta_abs")
        delta_text = f", delta_abs={delta}" if delta is not None else ""
        return (
            f"В данных по «{self._short_label(target_label)}» не видно {requested_text}{period}: "
            f"зафиксировано {observed_text}{delta_text}. Поэтому причину заявленного изменения назвать нельзя."
        )

    def _observation_row(
        self,
        observation: dict[str, Any],
        metric_lookup: dict[str, dict[str, Any]],
        *,
        status: str,
    ) -> dict[str, Any]:
        metric_code = str(observation.get("metric_code") or "")
        return {
            "id": observation.get("id") or "",
            "metric_code": metric_code,
            "metric_label": self._metric_label(metric_code, metric_lookup) if metric_code else "",
            "metric_codes": [metric_code] if metric_code else [],
            "previous_period": observation.get("previous_period") or "",
            "current_period": observation.get("current_period") or "",
            "delta_abs": observation.get("delta_abs"),
            "delta_pct": observation.get("delta_pct"),
            "score": observation.get("score"),
            "status": status,
        }

    def _answer_upstream(
        self,
        target: str,
        dependencies: list[dict[str, Any]],
        metric_lookup: dict[str, dict[str, Any]],
        *,
        matched_metrics: list[dict[str, Any]] | None = None,
        question: str = "",
    ) -> GraphQuestionAnswer:
        reverse_edges = self._reverse_edges(dependencies)
        paths = self._find_upstream_paths(target, reverse_edges)
        rows = [self._path_row(path, metric_lookup) for path in paths[:10]]
        label = self._metric_label(target, metric_lookup)
        warnings: list[str] = []
        if not rows:
            downstream_paths = self._find_downstream_paths(target, self._forward_edges(dependencies))
            if downstream_paths:
                downstream_rows = [self._path_row(path, metric_lookup) for path in downstream_paths[:3]]
                downstream_labels = [
                    str(row.get("target_label") or row.get("target_metric_code") or "")
                    for row in downstream_rows
                ]
                answer = (
                    f"Для «{self._short_label(label)}» в текущем графе пока не видно явных входящих связей. "
                    f"При этом сама метрика выступает источником: влияет на {self._format_label_list(downstream_labels)}."
                )
                warnings.append("opposite_direction_available")
            else:
                rows, warnings = self._external_context_rows(
                    question=question,
                    intent="upstream",
                    anchor_code=target,
                    metric_lookup=metric_lookup,
                )
                if rows:
                    answer = self._build_external_context_answer(
                        anchor_label=label,
                        rows=rows,
                        direction="upstream",
                    )
                else:
                    answer = f"Для «{self._short_label(label)}» в текущем графе пока не видно явных входящих связей."
        else:
            answer = self._build_direction_answer(
                anchor_label=label,
                rows=rows,
                direction="upstream",
            )
        return GraphQuestionAnswer(
            handled=True,
            intent="upstream",
            answer=answer,
            matched_metrics=matched_metrics or [self._metric_payload(target, metric_lookup)],
            rows=rows,
            cypher_hint=f"MATCH p=(:Metric)-[:DRIVES*1..3]->(:Metric {{code: '{target}'}}) RETURN p LIMIT 100;",
            warnings=warnings,
        )

    def _answer_downstream(
        self,
        source: str,
        dependencies: list[dict[str, Any]],
        metric_lookup: dict[str, dict[str, Any]],
        *,
        matched_metrics: list[dict[str, Any]] | None = None,
        question: str = "",
    ) -> GraphQuestionAnswer:
        forward_edges = self._forward_edges(dependencies)
        paths = self._find_downstream_paths(source, forward_edges)
        rows = [self._path_row(path, metric_lookup) for path in paths[:10]]
        label = self._metric_label(source, metric_lookup)
        warnings: list[str] = []
        if not rows:
            upstream_paths = self._find_upstream_paths(source, self._reverse_edges(dependencies))
            if upstream_paths:
                upstream_rows = [self._path_row(path, metric_lookup) for path in upstream_paths[:3]]
                upstream_labels = [
                    str(row.get("source_label") or row.get("source_metric_code") or "")
                    for row in upstream_rows
                ]
                answer = (
                    f"У «{self._short_label(label)}» в текущем графе пока не видно downstream-связей. "
                    f"При этом для нее есть входящие драйверы: {self._format_label_list(upstream_labels)}."
                )
                warnings.append("opposite_direction_available")
            else:
                rows, warnings = self._external_context_rows(
                    question=question,
                    intent="downstream",
                    anchor_code=source,
                    metric_lookup=metric_lookup,
                )
                if rows:
                    answer = self._build_external_context_answer(
                        anchor_label=label,
                        rows=rows,
                        direction="downstream",
                    )
                else:
                    answer = f"У «{self._short_label(label)}» в текущем графе пока не видно downstream-связей."
        else:
            answer = self._build_direction_answer(
                anchor_label=label,
                rows=rows,
                direction="downstream",
            )
        return GraphQuestionAnswer(
            handled=True,
            intent="downstream",
            answer=answer,
            matched_metrics=matched_metrics or [self._metric_payload(source, metric_lookup)],
            rows=rows,
            cypher_hint=f"MATCH p=(:Metric {{code: '{source}'}})-[:DRIVES*1..3]->(:Metric) RETURN p LIMIT 100;",
            warnings=warnings,
        )

    def _external_context_rows(
        self,
        *,
        question: str,
        intent: str,
        anchor_code: str,
        metric_lookup: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        anchor_label = self._metric_label(anchor_code, metric_lookup)
        static_suggestions = self._static_external_context_suggestions(
            intent=intent,
            anchor_code=anchor_code,
            anchor_label=anchor_label,
        )
        if static_suggestions:
            suggestions = static_suggestions
            result_warnings: list[str] = []
        else:
            result = self.external_context_suggester.suggest(
                question=question,
                intent=intent,
                anchor_metric={"code": anchor_code, "label": anchor_label},
                known_metrics=[
                    {
                        "code": code,
                        "label": self._metric_label(code, metric_lookup),
                        "aliases": list(metric.get("aliases") or [])[:8],
                    }
                    for code, metric in sorted(metric_lookup.items())
                ],
            )
            suggestions = result.suggestions
            result_warnings = result.warnings
        rows: list[dict[str, Any]] = []
        for suggestion in suggestions[:3]:
            row = self._external_context_row(
                suggestion=suggestion,
                intent=intent,
                anchor_code=anchor_code,
                anchor_label=anchor_label,
            )
            if row:
                rows.append(row)
        warnings = ["external_context_unconfirmed"] if rows else []
        warnings.extend(result_warnings)
        return rows, list(dict.fromkeys(warnings))

    def _external_context_row(
        self,
        *,
        suggestion: dict[str, Any],
        intent: str,
        anchor_code: str,
        anchor_label: str,
    ) -> dict[str, Any] | None:
        if intent == "upstream":
            source_label = str(suggestion.get("source_label") or suggestion.get("label") or "").strip()
            source_code = self._external_metric_code(suggestion.get("source_metric_code"), source_label)
            target_code = anchor_code
            target_label = anchor_label
        else:
            source_code = anchor_code
            source_label = anchor_label
            target_label = str(suggestion.get("target_label") or suggestion.get("label") or "").strip()
            target_code = self._external_metric_code(suggestion.get("target_metric_code"), target_label)
        if not source_code or not target_code or source_code == target_code:
            return None
        if not source_label:
            source_label = source_code.replace("_", " ").title()
        if not target_label:
            target_label = target_code.replace("_", " ").title()
        confidence = self._bounded_confidence(suggestion.get("confidence"), default=0.55)
        reason = str(suggestion.get("rationale") or suggestion.get("reason") or "").strip()
        if not reason:
            reason = "Внешний доменный контекст; требуется подтверждение пользователем."
        edge_type = str(suggestion.get("edge_type") or "driver")
        if edge_type not in {"driver", "inverse_driver", "component", "lag"}:
            edge_type = "driver"
        return {
            "id": f"external:{source_code}->{target_code}",
            "source_metric_code": source_code,
            "source_label": source_label,
            "target_metric_code": target_code,
            "target_label": target_label,
            "edge_type": edge_type,
            "strength": confidence,
            "confidence": confidence,
            "score": confidence,
            "reason": reason,
            "first_reason": reason,
            "status": "external_context_candidate",
            "source": "llm_external_context",
            "evidence_type": "external_context",
            "metric_codes": [source_code, target_code],
            "path_text": f"{source_label} -> {target_label}",
            "hops": 1,
        }

    def _static_external_context_suggestions(
        self,
        *,
        intent: str,
        anchor_code: str,
        anchor_label: str,
    ) -> list[dict[str, Any]]:
        if intent != "upstream" or anchor_code not in {"state_toll", "state_toll_index"}:
            return []
        return [
            {
                "source_metric_code": "toll_tariff_policy",
                "source_label": "Тарифная политика операторов платных дорог",
                "target_metric_code": anchor_code,
                "target_label": anchor_label,
                "edge_type": "driver",
                "rationale": "Тарифы операторов и индексация сборов напрямую задают стоимость проезда по платным участкам.",
                "confidence": 0.62,
            },
            {
                "source_metric_code": "vehicle_category_mix",
                "source_label": "Структура транспорта по категориям",
                "target_metric_code": anchor_code,
                "target_label": anchor_label,
                "edge_type": "driver",
                "rationale": "Класс, масса и категория транспорта влияют на применяемый тариф платных дорог.",
                "confidence": 0.58,
            },
            {
                "source_metric_code": "paid_road_route_share",
                "source_label": "Доля маршрутов по платным дорогам",
                "target_metric_code": anchor_code,
                "target_label": anchor_label,
                "edge_type": "driver",
                "rationale": "Чем больше доля маршрутов проходит через платные участки, тем выше индекс дорожных сборов.",
                "confidence": 0.56,
            },
        ]

    def _external_metric_code(self, raw_code: Any, label: str) -> str:
        code = str(raw_code or "").strip().lower()
        if not code and label:
            code = normalize_user_text(label)
        code = code.replace("ё", "е")
        code = re.sub(r"[^0-9a-zа-я_]+", "_", code, flags=re.IGNORECASE)
        code = re.sub(r"_+", "_", code).strip("_")
        if re.search(r"[а-я]", code):
            translit = {
                "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
                "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
                "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch",
                "ш": "sh", "щ": "sch", "ы": "y", "э": "e", "ю": "yu", "я": "ya", "ь": "", "ъ": "",
            }
            code = "".join(translit.get(char, char) for char in code)
            code = re.sub(r"[^0-9a-z_]+", "_", code)
            code = re.sub(r"_+", "_", code).strip("_")
        return code[:80]

    def _bounded_confidence(self, value: Any, *, default: float) -> float:
        try:
            confidence = float(value if value is not None else default)
        except (TypeError, ValueError):
            confidence = default
        return round(max(0.0, min(1.0, confidence)), 3)

    def _build_external_context_answer(
        self,
        *,
        anchor_label: str,
        rows: list[dict[str, Any]],
        direction: str,
    ) -> str:
        anchor_text = self._short_label(anchor_label)
        if direction == "upstream":
            labels = [str(row.get("source_label") or row.get("source_metric_code") or "") for row in rows[:3]]
            return (
                f"В подтвержденном графе для «{anchor_text}» входящих связей пока нет. "
                f"Как внешний контекст для проверки я бы добавил кандидаты: {self._format_label_list(labels)}. "
                "Это не сохраненные факты графа; их нужно подтвердить или отклонить."
            )
        labels = [str(row.get("target_label") or row.get("target_metric_code") or "") for row in rows[:3]]
        return (
            f"В подтвержденном графе у «{anchor_text}» downstream-связей пока нет. "
            f"Как внешний контекст для проверки я бы добавил кандидаты: {self._format_label_list(labels)}. "
            "Это не сохраненные факты графа; их нужно подтвердить или отклонить."
        )

    def _answer_relation(
        self,
        first: str,
        second: str,
        dependencies: list[dict[str, Any]],
        metric_lookup: dict[str, dict[str, Any]],
        *,
        matched_metrics: list[dict[str, Any]] | None = None,
    ) -> GraphQuestionAnswer:
        forward_edges = self._forward_edges(dependencies)
        max_hops = RELATION_GRAPH_PATH_MAX_HOPS
        paths = self._find_downstream_paths(first, forward_edges, target=second, max_hops=max_hops)
        direction = (first, second)
        if not paths:
            reverse_paths = self._find_downstream_paths(second, forward_edges, target=first, max_hops=max_hops)
            if reverse_paths:
                paths = reverse_paths
                direction = (second, first)
        rows = [self._path_row(path, metric_lookup) for path in paths[:5]]
        source_label = self._metric_label(direction[0], metric_lookup)
        target_label = self._metric_label(direction[1], metric_lookup)
        if rows:
            best = rows[0]
            answer = self._build_relation_answer(
                source_label=source_label,
                target_label=target_label,
                best_row=best,
            )
        else:
            answer = (
                f"В текущем графе пока не видно пути между «{self._short_label(self._metric_label(first, metric_lookup))}» "
                f"и «{self._short_label(self._metric_label(second, metric_lookup))}» в пределах {max_hops} шагов."
            )
        return GraphQuestionAnswer(
            handled=True,
            intent="relation_why",
            answer=answer,
            matched_metrics=matched_metrics or [self._metric_payload(first, metric_lookup), self._metric_payload(second, metric_lookup)],
            rows=rows,
            cypher_hint=(
                f"MATCH p=(:Metric {{code: '{direction[0]}'}})-[:DRIVES*1..{max_hops}]->"
                f"(:Metric {{code: '{direction[1]}'}}) RETURN p LIMIT 20;"
            ),
        )

    def _answer_pending(self, pending_confirmations: list[CandidateRelation]) -> GraphQuestionAnswer:
        rows = [
            {
                "id": item.id,
                "source_metric_code": item.source_metric_code,
                "target_metric_code": item.target_metric_code,
                "edge_type": item.edge_type,
                "score": item.score,
                "evidence_type": item.evidence_type,
                "reason": item.needs_approval_reason or item.note or item.evidence,
            }
            for item in sorted(pending_confirmations, key=lambda candidate: -float(candidate.score or candidate.confidence or 0.0))[:20]
        ]
        answer = (
            f"Есть {len(pending_confirmations)} связей, ожидающих подтверждения. "
            f"Показываю top-{len(rows)} по score."
        )
        return GraphQuestionAnswer(handled=True, intent="pending_confirmations", answer=answer, rows=rows)

    def _answer_relations_overview(
        self,
        dependencies: list[dict[str, Any]],
        metric_lookup: dict[str, dict[str, Any]],
        *,
        pending_confirmations: list[CandidateRelation],
    ) -> GraphQuestionAnswer:
        confirmed_rows = [
            {
                "source_metric_code": str(item.get("source_metric_code") or ""),
                "source_label": self._metric_label(str(item.get("source_metric_code") or ""), metric_lookup),
                "target_metric_code": str(item.get("target_metric_code") or ""),
                "target_label": self._metric_label(str(item.get("target_metric_code") or ""), metric_lookup),
                "edge_type": item.get("edge_type") or "driver",
                "strength": float(item.get("strength") or 0.0),
                "reason": item.get("reason") or "",
                "status": "graph",
            }
            for item in dependencies
            if item.get("source_metric_code") and item.get("target_metric_code")
        ]
        confirmed_rows.sort(
            key=lambda item: (
                -float(item.get("strength") or 0.0),
                str(item.get("target_label") or ""),
                str(item.get("source_label") or ""),
            )
        )
        pending_rows = [
            {
                "source_metric_code": item.source_metric_code,
                "source_label": self._metric_label(item.source_metric_code, metric_lookup),
                "target_metric_code": item.target_metric_code,
                "target_label": self._metric_label(item.target_metric_code, metric_lookup),
                "edge_type": item.edge_type,
                "score": item.score or item.confidence,
                "reason": item.needs_approval_reason or item.note or item.evidence,
                "status": "pending_confirmation",
            }
            for item in sorted(
                pending_confirmations,
                key=lambda candidate: -float(candidate.score or candidate.confidence or 0.0),
            )
        ]
        rows = [*confirmed_rows[:12], *pending_rows[:8]]
        if not rows:
            answer = "В текущей сессии пока нет связей в графе и нет связей на проверке."
            return GraphQuestionAnswer(handled=True, intent="relations_overview", answer=answer, rows=[])

        graph_count = len(confirmed_rows)
        pending_count = len(pending_rows)
        examples = "; ".join(
            f"«{self._short_label(row['source_label'])}» -> «{self._short_label(row['target_label'])}»"
            for row in rows[:5]
        )
        answer = (
            f"В текущей сессии есть {self._relation_count_phrase(graph_count)} в графе "
            f"и {self._relation_count_phrase(pending_count)} на проверке. "
            f"Первые примеры: {examples}."
        )
        return GraphQuestionAnswer(handled=True, intent="relations_overview", answer=answer, rows=rows)

    def _relation_count_phrase(self, count: int) -> str:
        return self._ru_count_phrase(count, one="связь", few="связи", many="связей")

    def _answer_hypotheses_overview(
        self,
        hypotheses: list[dict[str, Any]],
        metric_lookup: dict[str, dict[str, Any]],
    ) -> GraphQuestionAnswer:
        rows = []
        for item in sorted(
            hypotheses,
            key=lambda hypothesis: (
                -float(hypothesis.get("confidence") or 0.0),
                int(hypothesis.get("hops") or 0),
                str(hypothesis.get("target_metric_code") or ""),
            ),
        )[:12]:
            source = str(item.get("source_metric_code") or "")
            target = str(item.get("target_metric_code") or "")
            metric_codes = [str(code) for code in item.get("metric_codes") or [] if str(code)]
            rows.append(
                {
                    "id": item.get("id") or "",
                    "observation_id": item.get("observation_id") or "",
                    "source_metric_code": source,
                    "source_label": self._metric_label(source, metric_lookup) if source else "",
                    "target_metric_code": target,
                    "target_label": self._metric_label(target, metric_lookup) if target else "",
                    "metric_codes": metric_codes,
                    "path_labels": [
                        self._metric_label(code, metric_lookup)
                        for code in metric_codes
                    ],
                    "mechanism_type": item.get("mechanism_type") or "",
                    "support_window": item.get("support_window") or "",
                    "confidence": float(item.get("confidence") or 0.0),
                    "explanation": item.get("explanation") or "",
                }
            )

        if not rows:
            return GraphQuestionAnswer(
                handled=True,
                intent="hypotheses_overview",
                answer="В текущей сессии пока нет гипотез.",
                rows=[],
            )

        examples = "; ".join(
            self._hypothesis_example(row)
            for row in rows[:5]
        )
        count_phrase = self._ru_count_phrase(
            len(hypotheses),
            one="гипотеза",
            few="гипотезы",
            many="гипотез",
        )
        answer = (
            f"В текущей сессии есть {count_phrase}. "
            f"Показываю top-{len(rows)} по confidence. Первые примеры: {examples}."
        )
        return GraphQuestionAnswer(handled=True, intent="hypotheses_overview", answer=answer, rows=rows)

    def _hypothesis_example(self, row: dict[str, Any]) -> str:
        source = str(row.get("source_label") or "").strip()
        target = str(row.get("target_label") or "").strip()
        mechanism = str(row.get("mechanism_type") or "").strip()
        if source and target:
            return f"«{self._short_label(source)}» -> «{self._short_label(target)}» ({mechanism})"
        if target:
            return f"«{self._short_label(target)}» ({mechanism})"
        return mechanism or "гипотеза"

    def _answer_observations_overview(
        self,
        observations: list[dict[str, Any]],
        metric_lookup: dict[str, dict[str, Any]],
    ) -> GraphQuestionAnswer:
        rows = []
        for item in sorted(
            observations,
            key=lambda observation: (
                -float(observation.get("score") or 0.0),
                str(observation.get("metric_code") or ""),
            ),
        )[:12]:
            metric_code = str(item.get("metric_code") or "")
            rows.append(
                {
                    "id": item.get("id") or "",
                    "metric_code": metric_code,
                    "metric_label": self._metric_label(metric_code, metric_lookup) if metric_code else "",
                    "kind": item.get("kind") or "",
                    "reference_mode": item.get("reference_mode") or "",
                    "previous_period": item.get("previous_period") or "",
                    "current_period": item.get("current_period") or "",
                    "previous_value": item.get("previous_value"),
                    "current_value": item.get("current_value"),
                    "delta_abs": item.get("delta_abs"),
                    "delta_pct": item.get("delta_pct"),
                    "score": float(item.get("score") or 0.0),
                }
            )

        if not rows:
            return GraphQuestionAnswer(
                handled=True,
                intent="observations_overview",
                answer="В текущей сессии пока нет наблюдений.",
                rows=[],
            )

        examples = "; ".join(self._observation_example(row) for row in rows[:5])
        count_phrase = self._ru_count_phrase(
            len(observations),
            one="наблюдение",
            few="наблюдения",
            many="наблюдений",
        )
        answer = (
            f"В текущей сессии есть {count_phrase}. "
            f"Показываю top-{len(rows)} по score. Первые примеры: {examples}."
        )
        return GraphQuestionAnswer(handled=True, intent="observations_overview", answer=answer, rows=rows)

    def _observation_example(self, row: dict[str, Any]) -> str:
        label = self._short_label(str(row.get("metric_label") or row.get("metric_code") or "метрика"))
        previous_period = str(row.get("previous_period") or "")
        current_period = str(row.get("current_period") or "")
        delta_abs = row.get("delta_abs")
        direction = (
            "рост"
            if float(delta_abs or 0.0) > 0
            else "снижение"
            if float(delta_abs or 0.0) < 0
            else "без изменения"
        )
        if previous_period and current_period:
            return f"«{label}»: {direction} с {previous_period} до {current_period}"
        return f"«{label}» {direction}"

    def _answer_metrics_overview(self, metric_lookup: dict[str, dict[str, Any]]) -> GraphQuestionAnswer:
        rows = [
            {
                "code": code,
                "label": item.get("label") or code,
                "aliases": list(item.get("aliases") or [])[:6],
            }
            for code, item in sorted(
                metric_lookup.items(),
                key=lambda pair: str(pair[1].get("label") or pair[0]).lower(),
            )
        ]
        if not rows:
            return GraphQuestionAnswer(
                handled=True,
                intent="metrics_overview",
                answer="В текущей сессии пока нет метрик.",
                rows=[],
            )
        examples = "; ".join(f"«{self._short_label(str(row['label']))}»" for row in rows[:8])
        count_phrase = self._ru_count_phrase(len(rows), one="метрика", few="метрики", many="метрик")
        answer = f"В текущей сессии есть {count_phrase}. Первые примеры: {examples}."
        return GraphQuestionAnswer(handled=True, intent="metrics_overview", answer=answer, rows=rows[:30])

    def _ru_count_phrase(self, count: int, *, one: str, few: str, many: str) -> str:
        value = abs(int(count))
        last_two = value % 100
        last = value % 10
        if 11 <= last_two <= 14:
            noun = many
        elif last == 1:
            noun = one
        elif 2 <= last <= 4:
            noun = few
        else:
            noun = many
        return f"{count} {noun}"

    def _metric_lookup(self, metric_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            code: {
                "code": code,
                "label": self._display_metric_label(code, metric),
                "canonical_label": metric.get("label") or code,
                "aliases": metric.get("aliases") or [],
                "approved_aliases": metric.get("approved_aliases") or [],
                "source_labels": metric.get("source_labels") or [],
            }
            for code, metric in metric_map.items()
        }

    def _display_metric_label(self, code: str, metric: dict[str, Any]) -> str:
        label = str(metric.get("label") or code).strip()
        if self._contains_cyrillic(label):
            return label
        for collection_name in ("source_labels", "approved_aliases"):
            for candidate in metric.get(collection_name) or []:
                candidate_text = str(candidate or "").strip()
                if self._looks_like_business_cyrillic_label(candidate_text):
                    return candidate_text
        mapped = RUSSIAN_METRIC_DISPLAY_LABELS.get(str(code or "").lower())
        if mapped:
            return mapped
        for candidate in metric.get("aliases") or []:
            candidate_text = str(candidate or "").strip()
            if self._looks_like_business_cyrillic_label(candidate_text):
                return candidate_text
        return label or str(code or "")

    def _localized_matched_metrics(
        self,
        matched_metrics: list[dict[str, Any]],
        metric_lookup: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        localized = []
        for item in matched_metrics:
            payload = dict(item)
            code = str(payload.get("code") or "")
            if code in metric_lookup:
                payload["label"] = self._metric_label(code, metric_lookup)
            localized.append(payload)
        return localized

    def _looks_like_business_cyrillic_label(self, value: str) -> bool:
        cleaned = str(value or "").strip()
        if not self._contains_cyrillic(cleaned):
            return False
        return len(normalize_user_text(cleaned).replace(" ", "")) >= 3

    def _contains_cyrillic(self, value: str) -> bool:
        return bool(re.search(r"[А-Яа-яЁё]", str(value or "")))

    def _build_direction_answer(
        self,
        *,
        anchor_label: str,
        rows: list[dict[str, Any]],
        direction: str,
    ) -> str:
        direct_rows = self._unique_terminal_rows(rows, direction=direction, direct_only=True)
        indirect_rows = self._unique_terminal_rows(rows, direction=direction, direct_only=False)
        anchor_text = self._short_label(anchor_label)
        direct_labels = [self._terminal_label(row, direction=direction) for row in direct_rows[:3]]
        indirect_labels = [
            self._terminal_label(row, direction=direction)
            for row in indirect_rows
            if self._terminal_code(row, direction=direction) not in {
                self._terminal_code(item, direction=direction) for item in direct_rows
            }
        ][:2]

        if direction == "downstream":
            if len(direct_labels) == 1:
                answer = f"«{anchor_text}» в первую очередь влияет на «{direct_labels[0]}»."
            else:
                answer = f"«{anchor_text}» в первую очередь влияет на {self._format_label_list(direct_labels)}."
        else:
            if len(direct_labels) == 1:
                answer = f"На «{anchor_text}» в первую очередь влияет «{direct_labels[0]}»."
            else:
                answer = f"На «{anchor_text}» в первую очередь влияют {self._format_label_list(direct_labels)}."

        if indirect_labels:
            answer += f" Косвенно также видны связи с {self._format_label_list(indirect_labels)}."

        strongest_direct = direct_rows[0] if direct_rows else rows[0]
        strongest_label = self._terminal_label(strongest_direct, direction=direction)
        if direction == "downstream":
            answer += f" Самая заметная прямая связь идет к «{strongest_label}»."
        else:
            answer += f" Самый заметный прямой драйвер здесь - «{strongest_label}»."
        return answer

    def _build_relation_answer(
        self,
        *,
        source_label: str,
        target_label: str,
        best_row: dict[str, Any],
    ) -> str:
        source_text = self._short_label(source_label)
        target_text = self._short_label(target_label)
        path_labels = [self._short_label(item) for item in str(best_row.get("path_text") or "").split(" -> ") if item]
        if int(best_row.get("hops") or 0) <= 1:
            answer = f"Связь есть, и она прямая: «{source_text}» влияет на «{target_text}»."
        else:
            via_labels = path_labels[1:-1]
            if via_labels:
                answer = (
                    f"Связь есть, но она не прямая: «{source_text}» влияет на «{target_text}» "
                    f"через {self._format_label_list(via_labels)}."
                )
            else:
                answer = f"Связь есть между «{source_text}» и «{target_text}»."
        return answer

    def _forward_edges(self, dependencies: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for dependency in dependencies:
            edges[str(dependency.get("source_metric_code") or "")].append(dependency)
        return edges

    def _reverse_edges(self, dependencies: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for dependency in dependencies:
            edges[str(dependency.get("target_metric_code") or "")].append(dependency)
        return edges

    def _find_upstream_paths(
        self,
        target: str,
        reverse_edges: dict[str, list[dict[str, Any]]],
        *,
        max_hops: int = DEFAULT_GRAPH_PATH_MAX_HOPS,
    ) -> list[dict[str, Any]]:
        queue = [(target, [target], [], 0.0)]
        paths = []
        while queue:
            current, metric_codes, edge_records, total_strength = queue.pop(0)
            if len(metric_codes) > max_hops + 1:
                continue
            for dependency in reverse_edges.get(current, []):
                source = str(dependency.get("source_metric_code") or "")
                if not source or source in metric_codes:
                    continue
                next_codes = [source, *metric_codes]
                next_edges = [dependency, *edge_records]
                next_strength = total_strength + float(dependency.get("strength") or 0.0)
                paths.append({"metric_codes": next_codes, "edge_records": next_edges, "total_strength": next_strength})
                if len(next_codes) - 1 < max_hops:
                    queue.append((source, next_codes, next_edges, next_strength))
        paths.sort(key=lambda item: (len(item["metric_codes"]), -float(item["total_strength"]), item["metric_codes"][0]))
        return paths[:50]

    def _find_downstream_paths(
        self,
        source: str,
        forward_edges: dict[str, list[dict[str, Any]]],
        *,
        target: str | None = None,
        max_hops: int = DEFAULT_GRAPH_PATH_MAX_HOPS,
    ) -> list[dict[str, Any]]:
        queue = [(source, [source], [], 0.0)]
        paths = []
        found_target_hops: int | None = None
        while queue:
            current, metric_codes, edge_records, total_strength = queue.pop(0)
            if len(metric_codes) > max_hops + 1:
                continue
            if target is not None and found_target_hops is not None and len(metric_codes) - 1 >= found_target_hops:
                continue
            for dependency in forward_edges.get(current, []):
                next_metric = str(dependency.get("target_metric_code") or "")
                if not next_metric or next_metric in metric_codes:
                    continue
                next_codes = [*metric_codes, next_metric]
                next_edges = [*edge_records, dependency]
                next_strength = total_strength + float(dependency.get("strength") or 0.0)
                path = {"metric_codes": next_codes, "edge_records": next_edges, "total_strength": next_strength}
                if target is None or next_metric == target:
                    paths.append(path)
                    if target is not None:
                        found_target_hops = min(found_target_hops or max_hops, len(next_codes) - 1)
                if (
                    len(next_codes) - 1 < max_hops
                    and (target is None or found_target_hops is None or len(next_codes) - 1 < found_target_hops)
                ):
                    queue.append((next_metric, next_codes, next_edges, next_strength))
        paths.sort(key=lambda item: (len(item["metric_codes"]), -float(item["total_strength"]), item["metric_codes"][-1]))
        return paths[:50]

    def _path_row(self, path: dict[str, Any], metric_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
        metric_codes = list(path["metric_codes"])
        first_edge = path["edge_records"][0] if path.get("edge_records") else {}
        return {
            "source_metric_code": metric_codes[0],
            "source_label": self._metric_label(metric_codes[0], metric_lookup),
            "target_metric_code": metric_codes[-1],
            "target_label": self._metric_label(metric_codes[-1], metric_lookup),
            "metric_codes": metric_codes,
            "path_text": " -> ".join(self._metric_label(code, metric_lookup) for code in metric_codes),
            "edge_types": [item.get("edge_type") for item in path.get("edge_records", [])],
            "hops": len(metric_codes) - 1,
            "total_strength": round(float(path.get("total_strength") or 0.0), 3),
            "first_reason": self._humanize_reason(str(first_edge.get("reason") or "")),
        }

    def _metric_payload(self, code: str, metric_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "code": code,
            "label": self._metric_label(code, metric_lookup),
        }

    def _metric_label(self, code: str, metric_lookup: dict[str, dict[str, Any]]) -> str:
        return str(metric_lookup.get(code, {}).get("label") or code)

    def _narrate_answer(self, *, question: str, answer: GraphQuestionAnswer) -> None:
        if not self.answer_narrator or not answer.rows:
            return
        narration = self.answer_narrator.narrate(
            question=question,
            intent=answer.intent,
            fallback_answer=answer.answer,
            matched_metrics=answer.matched_metrics,
            rows=answer.rows,
        )
        answer.answer = narration.answer
        answer.warnings = list(dict.fromkeys([*answer.warnings, *narration.warnings]))

    def _unique_terminal_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        direction: str,
        direct_only: bool,
    ) -> list[dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for row in rows:
            is_direct = int(row.get("hops") or 0) == 1
            if direct_only and not is_direct:
                continue
            if not direct_only and is_direct:
                continue
            code = self._terminal_code(row, direction=direction)
            if not code:
                continue
            current = selected.get(code)
            if current is None or self._row_rank(row) < self._row_rank(current):
                selected[code] = row
        return sorted(selected.values(), key=self._row_rank)

    def _terminal_code(self, row: dict[str, Any], *, direction: str) -> str:
        key = "target_metric_code" if direction == "downstream" else "source_metric_code"
        return str(row.get(key) or "")

    def _terminal_label(self, row: dict[str, Any], *, direction: str) -> str:
        key = "target_label" if direction == "downstream" else "source_label"
        return self._short_label(str(row.get(key) or ""))

    def _row_rank(self, row: dict[str, Any]) -> tuple[int, float, str]:
        return (
            int(row.get("hops") or 0),
            -float(row.get("total_strength") or 0.0),
            str(row.get("path_text") or ""),
        )

    def _format_label_list(self, labels: list[str]) -> str:
        unique_labels = [item for item in dict.fromkeys(label for label in labels if label)]
        if not unique_labels:
            return ""
        if len(unique_labels) == 1:
            return f"«{unique_labels[0]}»"
        if len(unique_labels) == 2:
            return f"«{unique_labels[0]}» и «{unique_labels[1]}»"
        head = ", ".join(f"«{item}»" for item in unique_labels[:-1])
        return f"{head} и «{unique_labels[-1]}»"

    def _short_label(self, label: str, *, limit: int = 96) -> str:
        cleaned = re.sub(r"\s+", " ", str(label or "")).strip()
        if len(cleaned) <= limit:
            return cleaned
        shortened = cleaned[: limit - 3].rstrip(" ,;:")
        return f"{shortened}..."

    def _humanize_reason(self, reason: str) -> str:
        cleaned = str(reason or "").strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"^[a-z_]+:\s*", "", cleaned)
        cleaned = re.sub(r"\s*Evidence:.*$", "", cleaned)
        cleaned = re.sub(r"\s*source_semantic=.*$", "", cleaned)
        cleaned = cleaned.replace("downstream-цепочку", "исходящую цепочку")
        cleaned = cleaned.replace("upstream-цепочку", "входящую цепочку")
        cleaned = cleaned.replace("downstream", "исходящая связь")
        cleaned = cleaned.replace("upstream", "входящая связь")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ;.")
        for code, label in RUSSIAN_METRIC_DISPLAY_LABELS.items():
            variants = {
                code,
                code.replace("_", " "),
                code.replace("_", " ").title(),
            }
            for variant in sorted(variants, key=len, reverse=True):
                if variant and variant != label:
                    cleaned = re.sub(
                        rf"(?<![\w]){re.escape(variant)}(?![\w])",
                        label,
                        cleaned,
                        flags=re.IGNORECASE,
                    )
        return cleaned
