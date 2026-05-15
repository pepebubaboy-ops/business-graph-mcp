from __future__ import annotations

import copy
import re
from difflib import SequenceMatcher
from typing import Any

from app.services.relation_memory_language_models import (
    ClarificationSlot,
    ConfirmedMetricAlias,
    MetricAliasRecord,
    NormalizedQuestion,
    PendingClarification,
    RelationMemoryConversationContext,
    ResolvedMetricCandidate,
)
from app.services.relation_memory_language_utils import (
    BROAD_ALIAS_PREFERRED_CODE_HINTS,
    CHANGE_VERB_PATTERN,
    CONTEXTUAL_TOKENS,
    SELECTION_WORDS,
    SEMANTIC_ALIAS_HINTS,
    _edit_distance,
    expand_abbreviation,
    informative_tokens,
    latinize_text,
    normalize_phrase_text,
    normalize_user_text,
    stem_phrase,
    stem_token,
)


class RelationMemoryUserLanguageResolver:
    def __init__(self, metric_map: dict[str, dict[str, Any]]):
        self.metric_map = metric_map
        self.alias_records = self._build_alias_records(metric_map)
        self.metric_lookup = {
            code: {
                "code": code,
                "label": str(metric.get("label") or code),
                "aliases": list(metric.get("aliases") or []),
                "approved_aliases": list(metric.get("approved_aliases") or []),
                "source_labels": list(metric.get("source_labels") or []),
            }
            for code, metric in metric_map.items()
        }

    def resolve(
        self,
        *,
        question: str,
        conversation_context: RelationMemoryConversationContext | None = None,
    ) -> NormalizedQuestion:
        context = copy.deepcopy(conversation_context or RelationMemoryConversationContext())
        raw_question = str(question or "").strip()
        normalized_question = normalize_user_text(raw_question)
        explicit_intent = self._detect_intent(normalized_question)

        if context.pending_clarification:
            if not self._looks_like_new_question(raw_question, explicit_intent):
                pending_resolution = self._resolve_pending_clarification(raw_question, context)
                if pending_resolution is not None:
                    return pending_resolution
            else:
                context.pending_clarification = None

        intent = explicit_intent
        if intent == "unknown" and self._is_contextual_follow_up(raw_question) and context.last_resolved_intent:
            intent = context.last_resolved_intent

        if intent == "unknown":
            return NormalizedQuestion(handled=False, intent="unknown", updated_context=context)
        if intent in {
            "pending_confirmations",
            "relations_overview",
            "hypotheses_overview",
            "metrics_overview",
            "observations_overview",
        }:
            context.pending_clarification = None
            context.last_resolved_intent = intent
            context.source_utterance = raw_question
            return NormalizedQuestion(handled=True, intent=intent, updated_context=context)

        slots = self._extract_slots(raw_question, intent)
        if not slots:
            slots = [{"role": "metric", "phrase": ""}] if intent != "relation_why" else [{"role": "source", "phrase": ""}, {"role": "target", "phrase": ""}]

        resolved_metrics: list[ResolvedMetricCandidate] = []
        clarification_slots: list[ClarificationSlot] = []
        warnings: list[str] = []
        used_context = False

        for slot in slots:
            role = str(slot["role"])
            phrase = str(slot.get("phrase") or "").strip()
            if self._is_contextual_phrase(phrase):
                context_metric = self._context_metric_for_role(intent=intent, role=role, context=context)
                if context_metric:
                    resolved_metrics.append(self._candidate_for_code(context_metric, role=role, match_reason="context_fallback", matched_alias=phrase or "context"))
                    used_context = True
                    continue

            if not phrase and intent != "relation_why":
                context_metric = self._context_metric_for_role(intent=intent, role=role, context=context)
                if context_metric:
                    resolved_metrics.append(self._candidate_for_code(context_metric, role=role, match_reason="context_fallback", matched_alias="context"))
                    used_context = True
                    continue

            resolved, candidates = self._resolve_metric_phrase(phrase=phrase, role=role)
            if resolved is not None:
                resolved_metrics.append(resolved)
                continue
            if candidates:
                clarification_slots.append(ClarificationSlot(role=role, phrase=phrase, options=candidates[:3]))
            else:
                clarification_slots.append(ClarificationSlot(role=role, phrase=phrase, options=[]))

        if intent == "relation_why":
            codes = [candidate.code for candidate in resolved_metrics]
            if len(codes) >= 2 and codes[0] == codes[1]:
                first_slot = slots[0] if slots else {"phrase": raw_question}
                second_slot = slots[1] if len(slots) > 1 else {"phrase": raw_question}
                clarification_slots = [
                    ClarificationSlot(role="source", phrase=str(first_slot.get("phrase") or ""), options=[resolved_metrics[0]]),
                    ClarificationSlot(role="target", phrase=str(second_slot.get("phrase") or ""), options=[resolved_metrics[1]]),
                ]
                resolved_metrics = []

        if clarification_slots:
            context.pending_clarification = PendingClarification(
                intent=intent,
                original_question=raw_question,
                slots=clarification_slots,
                source_utterance=raw_question,
            )
            return NormalizedQuestion(
                handled=True,
                intent=intent,
                matched_metrics=self._clarification_matched_metrics(clarification_slots),
                warnings=[self._clarification_warning(intent)],
                clarification_needed=True,
                clarification=context.pending_clarification,
                updated_context=context,
            )

        if used_context:
            warnings.append("clarification_resolved_from_context")

        context.pending_clarification = None
        context.last_resolved_intent = intent
        context.last_resolved_metric_codes = [candidate.code for candidate in resolved_metrics]
        context.source_utterance = raw_question
        return NormalizedQuestion(
            handled=True,
            intent=intent,
            resolved_metrics=resolved_metrics,
            matched_metrics=[candidate.to_payload() for candidate in resolved_metrics],
            warnings=warnings,
            updated_context=context,
        )

    def build_clarification_answer(self, clarification: PendingClarification) -> str:
        unresolved_slots = [slot for slot in clarification.slots if not slot.resolved_code]
        if len(unresolved_slots) == 1:
            slot = unresolved_slots[0]
            if slot.options:
                options_text = "; ".join(
                    f"{index + 1}. «{item.label}» ({item.code})"
                    for index, item in enumerate(slot.options[:3])
                )
                return (
                    f"Не хочу отвечать по случайной метрике. Уточните, что вы имеете в виду под «{slot.phrase or 'этой метрикой'}»: "
                    f"{options_text}."
                )
            return (
                f"Я понял тип вопроса, но не смог уверенно сопоставить «{slot.phrase or 'эту метрику'}» "
                "с текущим графом. Уточните название ближе к бизнес-термину или metric_code."
            )

        slot_lines = []
        for slot in unresolved_slots:
            if slot.options:
                options_text = "; ".join(
                    f"{index + 1}. «{item.label}» ({item.code})"
                    for index, item in enumerate(slot.options[:3])
                )
                slot_lines.append(f"{slot.role}: «{slot.phrase or slot.role}» -> {options_text}")
            else:
                slot_lines.append(
                    f"{slot.role}: «{slot.phrase or slot.role}» не удалось сопоставить с метрикой из графа"
                )
        return (
            "Чтобы объяснить связь, нужно уточнить метрики отдельно. "
            + " ".join(slot_lines)
            + "."
        )

    def _resolve_pending_clarification(
        self,
        user_input: str,
        context: RelationMemoryConversationContext,
    ) -> NormalizedQuestion | None:
        pending = copy.deepcopy(context.pending_clarification)
        if pending is None:
            return None

        unresolved_slots = [slot for slot in pending.slots if not slot.resolved_code]
        if not unresolved_slots:
            return None

        confirmed_aliases: list[ConfirmedMetricAlias] = []
        for slot in unresolved_slots:
            selected = self._select_clarification_option(user_input, slot, allow_numeric=len(unresolved_slots) == 1)
            if selected is None:
                continue
            slot.resolved_code = selected.code
            if self._is_persistable_alias(slot.phrase, selected.code):
                confirmed_aliases.append(
                    ConfirmedMetricAlias(canonical_code=selected.code, alias=slot.phrase.strip(), label=selected.label)
                )

        remaining_slots = [slot for slot in pending.slots if not slot.resolved_code]
        if remaining_slots:
            context.pending_clarification = pending
            return NormalizedQuestion(
                handled=True,
                intent=pending.intent,
                matched_metrics=self._clarification_matched_metrics(remaining_slots),
                warnings=[self._clarification_warning(pending.intent)],
                clarification_needed=True,
                clarification=pending,
                updated_context=context,
            )

        resolved_metrics = [
            self._candidate_for_code(str(slot.resolved_code), role=slot.role, match_reason="clarification", matched_alias=slot.phrase)
            for slot in pending.slots
            if slot.resolved_code
        ]
        context.pending_clarification = None
        context.last_resolved_intent = pending.intent
        context.last_resolved_metric_codes = [candidate.code for candidate in resolved_metrics]
        context.source_utterance = pending.original_question
        return NormalizedQuestion(
            handled=True,
            intent=pending.intent,
            resolved_metrics=resolved_metrics,
            matched_metrics=[candidate.to_payload() for candidate in resolved_metrics],
            updated_context=context,
            confirmed_aliases=confirmed_aliases,
        )

    def _select_clarification_option(
        self,
        user_input: str,
        slot: ClarificationSlot,
        *,
        allow_numeric: bool,
    ) -> ResolvedMetricCandidate | None:
        normalized_input = normalize_user_text(user_input)
        if not normalized_input:
            return None
        if allow_numeric:
            for token in normalized_input.split():
                if token in SELECTION_WORDS:
                    index = SELECTION_WORDS[token]
                    if 0 <= index < len(slot.options):
                        return slot.options[index]

        ranked: list[tuple[float, ResolvedMetricCandidate]] = []
        for option in slot.options:
            if option.code and re.search(rf"(?<![0-9a-zа-яё_]){re.escape(option.code.lower())}(?![0-9a-zа-яё_])", user_input.lower()):
                return option
            aliases = self._option_aliases(option)
            best_score = 0.0
            for alias in aliases:
                normalized_alias = normalize_user_text(alias)
                if not normalized_alias:
                    continue
                if normalized_alias == normalized_input:
                    return option
                if normalized_alias in normalized_input or normalized_input in normalized_alias:
                    best_score = max(best_score, 0.92)
                    continue
                ratio = SequenceMatcher(None, normalized_alias, normalized_input).ratio()
                best_score = max(best_score, ratio)
            if best_score >= 0.78:
                ranked.append((best_score, option))
        ranked.sort(key=lambda item: (-item[0], item[1].code))
        if not ranked:
            return None
        if len(ranked) == 1 or ranked[0][0] >= ranked[1][0] + 0.08:
            return ranked[0][1]
        return None

    def _resolve_metric_phrase(
        self,
        *,
        phrase: str,
        role: str,
    ) -> tuple[ResolvedMetricCandidate | None, list[ResolvedMetricCandidate]]:
        cleaned_phrase = normalize_phrase_text(phrase)
        if not cleaned_phrase:
            return None, []

        stages = [
            self._exact_metric_code_matches(cleaned_phrase, role),
            self._exact_alias_matches(cleaned_phrase, role, categories={"approved_alias"}),
            self._exact_alias_matches(cleaned_phrase, role, categories={"label", "source_label", "alias", "semantic_alias"}),
            self._normalized_phrase_matches(cleaned_phrase, role),
            self._abbreviation_matches(cleaned_phrase, role),
            self._translit_matches(cleaned_phrase, role),
            self._fuzzy_typo_matches(cleaned_phrase, role),
            self._token_overlap_matches(cleaned_phrase, role),
        ]
        for stage_name, candidates in stages:
            if not candidates:
                continue
            if len(candidates) == 1:
                return candidates[0], candidates
            if self._can_accept_best_candidate(stage_name, candidates):
                return candidates[0], candidates
            return None, candidates
        return None, []

    def _exact_metric_code_matches(self, phrase: str, role: str) -> tuple[str, list[ResolvedMetricCandidate]]:
        lowered = phrase.lower()
        matches = []
        for code, metric in self.metric_lookup.items():
            if re.search(rf"(?<![0-9a-zа-яё_]){re.escape(code.lower())}(?![0-9a-zа-яё_])", lowered):
                matches.append(
                    ResolvedMetricCandidate(
                        code=code,
                        label=str(metric.get("label") or code),
                        matched_alias=code,
                        match_reason="exact_metric_code",
                        score=1.0,
                        role=role,
                    )
                )
        return "exact_metric_code", matches

    def _exact_alias_matches(
        self,
        phrase: str,
        role: str,
        *,
        categories: set[str],
    ) -> tuple[str, list[ResolvedMetricCandidate]]:
        normalized = normalize_user_text(phrase)
        matches: list[ResolvedMetricCandidate] = []
        for record in self.alias_records:
            if record.category not in categories:
                continue
            if record.normalized != normalized:
                continue
            score = 0.98 if record.category == "approved_alias" else 0.95
            score += self._broad_alias_preference(normalized, record, raw_phrase=phrase)
            matches.append(
                ResolvedMetricCandidate(
                    code=record.code,
                    label=record.label,
                    matched_alias=record.alias,
                    match_reason=record.category,
                    score=score,
                    role=role,
                )
            )
        return "exact_alias", self._dedupe_candidates(matches)

    def _normalized_phrase_matches(self, phrase: str, role: str) -> tuple[str, list[ResolvedMetricCandidate]]:
        normalized = normalize_user_text(phrase)
        latinized = latinize_text(normalized)
        stemmed = stem_phrase(normalized)
        phrase_tokens = informative_tokens(normalized)
        matches: list[ResolvedMetricCandidate] = []
        for record in self.alias_records:
            if not record.normalized:
                continue
            if self._is_broad_alias_mismatch(normalized, record):
                continue
            score = 0.0
            if record.normalized == normalized:
                score = 0.93
            elif normalized in record.normalized or record.normalized in normalized:
                score = 0.9 + min(len(phrase_tokens), len(record.tokens)) / max(len(record.tokens), 1) * 0.03
            elif record.stemmed and record.stemmed == stemmed:
                score = 0.89
            elif stemmed and stemmed in record.stemmed:
                score = 0.87
            elif phrase_tokens and set(phrase_tokens).issubset(set(record.tokens)):
                score = 0.86 + len(phrase_tokens) / max(len(record.tokens), 1) * 0.05
            elif latinized and latinized == record.latinized:
                score = 0.88
            if score <= 0:
                continue
            matches.append(
                ResolvedMetricCandidate(
                    code=record.code,
                    label=record.label,
                    matched_alias=record.alias,
                    match_reason="normalized_phrase",
                    score=score,
                    role=role,
                )
            )
        return "normalized_phrase", self._dedupe_candidates(matches)

    def _is_broad_alias_mismatch(self, normalized_phrase: str, record: MetricAliasRecord) -> bool:
        if record.code == "gross_margin" and record.normalized in {"маржа", "margin"}:
            return any(token in normalized_phrase for token in ("операцион", "operating", "чист", "net"))
        return False

    def _abbreviation_matches(self, phrase: str, role: str) -> tuple[str, list[ResolvedMetricCandidate]]:
        normalized = normalize_user_text(phrase)
        tokens = normalized.split()
        matches: list[ResolvedMetricCandidate] = []
        for record in self.alias_records:
            if not record.abbreviations:
                continue
            if any(abbreviation in tokens for abbreviation in record.abbreviations):
                matches.append(
                    ResolvedMetricCandidate(
                        code=record.code,
                        label=record.label,
                        matched_alias=record.alias,
                        match_reason="abbreviation",
                        score=0.88,
                        role=role,
                    )
                )
        return "abbreviation", self._dedupe_candidates(matches)

    def _translit_matches(self, phrase: str, role: str) -> tuple[str, list[ResolvedMetricCandidate]]:
        normalized = normalize_user_text(phrase)
        latinized = latinize_text(phrase)
        if not latinized:
            return "translit", []
        matches: list[ResolvedMetricCandidate] = []
        for record in self.alias_records:
            if not record.latinized:
                continue
            if self._is_broad_alias_mismatch(normalized, record):
                continue
            if record.latinized == latinized:
                score = 0.87
            elif latinized in record.latinized or record.latinized in latinized:
                score = 0.84
            else:
                score = 0.0
            if score <= 0:
                continue
            matches.append(
                ResolvedMetricCandidate(
                    code=record.code,
                    label=record.label,
                    matched_alias=record.alias,
                    match_reason="translit",
                    score=score,
                    role=role,
                )
            )
        return "translit", self._dedupe_candidates(matches)

    def _fuzzy_typo_matches(self, phrase: str, role: str) -> tuple[str, list[ResolvedMetricCandidate]]:
        normalized = normalize_user_text(phrase)
        phrase_tokens = [token for token in normalized.split() if token]
        if len(phrase_tokens) != 1:
            return "fuzzy_typo", []
        token = phrase_tokens[0]
        if len(token) < 5:
            return "fuzzy_typo", []
        matches: list[ResolvedMetricCandidate] = []
        for record in self.alias_records:
            candidates = [record.normalized, *record.tokens]
            best_distance = min(
                (_edit_distance(token, candidate) for candidate in candidates if candidate and len(candidate) >= 5),
                default=999,
            )
            if best_distance > 2:
                continue
            score = 0.82 if best_distance == 2 else 0.88
            score += self._broad_alias_preference(record.normalized, record, raw_phrase=phrase)
            matches.append(
                ResolvedMetricCandidate(
                    code=record.code,
                    label=record.label,
                    matched_alias=record.alias,
                    match_reason="fuzzy_typo",
                    score=score,
                    role=role,
                )
            )
        return "fuzzy_typo", self._dedupe_candidates(matches)

    def _token_overlap_matches(self, phrase: str, role: str) -> tuple[str, list[ResolvedMetricCandidate]]:
        normalized = normalize_user_text(phrase)
        question_tokens = informative_tokens(normalized)
        if len(question_tokens) < 2:
            return "token_overlap", []
        matches: list[ResolvedMetricCandidate] = []
        for record in self.alias_records:
            if not record.tokens:
                continue
            matched = self._matched_tokens(question_tokens, record.tokens)
            if len(matched) < 2:
                continue
            score = 0.68 + len(matched) / max(len(record.tokens), 1) * 0.18
            if score < 0.72:
                continue
            matches.append(
                ResolvedMetricCandidate(
                    code=record.code,
                    label=record.label,
                    matched_alias=record.alias,
                    match_reason="token_overlap",
                    score=score,
                    role=role,
                )
            )
        return "token_overlap", self._dedupe_candidates(matches)

    def _dedupe_candidates(self, candidates: list[ResolvedMetricCandidate]) -> list[ResolvedMetricCandidate]:
        best_by_code: dict[str, ResolvedMetricCandidate] = {}
        for candidate in candidates:
            existing = best_by_code.get(candidate.code)
            if existing is None or float(candidate.score) > float(existing.score):
                best_by_code[candidate.code] = candidate
        return sorted(best_by_code.values(), key=lambda item: (-float(item.score), item.label, item.code))

    def _broad_alias_preference(
        self,
        normalized_alias: str,
        record: MetricAliasRecord,
        *,
        raw_phrase: str = "",
    ) -> float:
        raw_normalized = normalize_phrase_text(raw_phrase).lower()
        if raw_normalized in {"гсм", "gsm"} and record.code == "zatraty_na_gsm":
            return 0.04
        preferred_code_hints = BROAD_ALIAS_PREFERRED_CODE_HINTS.get(normalized_alias)
        if not preferred_code_hints:
            return 0.0
        raw_code = record.code.lower()
        normalized_code = normalize_user_text(record.code)
        normalized_label = normalize_user_text(record.label)
        if any(hint in raw_code or hint in normalized_code or hint in normalized_label for hint in preferred_code_hints):
            return 0.04
        return 0.0

    def _can_accept_best_candidate(self, stage_name: str, candidates: list[ResolvedMetricCandidate]) -> bool:
        if len(candidates) < 2:
            return True
        best = candidates[0]
        second = candidates[1]
        if stage_name in {"exact_metric_code", "exact_alias"}:
            return best.code == second.code or float(best.score) >= float(second.score) + 0.03
        gap = float(best.score) - float(second.score)
        if stage_name in {"normalized_phrase", "translit"}:
            return gap >= 0.08
        if stage_name == "abbreviation":
            return gap >= 0.1
        if stage_name == "fuzzy_typo":
            return gap >= 0.08 and float(best.score) >= 0.82
        if stage_name == "token_overlap":
            return gap >= 0.12 and float(best.score) >= 0.8
        return False

    def _build_alias_records(self, metric_map: dict[str, dict[str, Any]]) -> list[MetricAliasRecord]:
        records: list[MetricAliasRecord] = []
        seen: set[tuple[str, str, str]] = set()
        for code, metric in metric_map.items():
            label = str(metric.get("label") or code)
            alias_groups = [
                ("code", [code, code.replace("_", " ")]),
                ("approved_alias", metric.get("approved_aliases") or []),
                ("source_label", metric.get("source_labels") or []),
                ("label", [label]),
                ("alias", metric.get("aliases") or []),
                ("semantic_alias", self._semantic_aliases(code=code, label=label, aliases=metric.get("aliases") or [])),
            ]
            for category, alias_values in alias_groups:
                for alias in alias_values:
                    alias_text = str(alias or "").strip()
                    normalized = normalize_user_text(alias_text)
                    if not alias_text or not normalized:
                        continue
                    key = (code, category, normalized)
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(
                        MetricAliasRecord(
                            code=code,
                            label=label,
                            alias=alias_text,
                            category=category,
                            normalized=normalized,
                            latinized=latinize_text(alias_text),
                            stemmed=stem_phrase(alias_text),
                            tokens=informative_tokens(alias_text),
                            abbreviations=self._abbreviation_variants(alias_text),
                        )
                    )
        return records

    def _semantic_aliases(self, *, code: str, label: str, aliases: list[str]) -> list[str]:
        values = [code, label, *aliases]
        normalized_values = [normalize_user_text(value) for value in values]
        hints: list[str] = []
        for normalized in normalized_values:
            for token in normalized.split():
                for hint_key, hint_values in SEMANTIC_ALIAS_HINTS.items():
                    if hint_key in token:
                        hints.extend(hint_values)
        if any(token in normalize_user_text(code) for token in ("ftl", "фтл")) and any(
            token in normalize_user_text(code) for token in ("sebestoimost", "себестоимост")
        ):
            hints.extend(["себестоимость ftl", "себестоимость перевозки", "себестоимость рейса ftl"])
        return list(dict.fromkeys(hint for hint in hints if hint))

    def _abbreviation_variants(self, alias: str) -> list[str]:
        abbreviations: list[str] = []
        for token in re.findall(r"[A-Za-zА-ЯЁа-яё0-9]+", alias):
            lowered = token.lower()
            if len(lowered) >= 2 and token.isupper():
                abbreviations.append(lowered)
        initials = "".join(token[0].lower() for token in re.findall(r"[A-Za-zА-ЯЁа-яё0-9]+", alias) if token)
        if len(initials) >= 2:
            abbreviations.append(initials)
        return list(dict.fromkeys(item for item in abbreviations if item))

    def _candidate_for_code(
        self,
        code: str,
        *,
        role: str,
        match_reason: str,
        matched_alias: str,
    ) -> ResolvedMetricCandidate:
        metric = self.metric_lookup.get(code, {})
        return ResolvedMetricCandidate(
            code=code,
            label=str(metric.get("label") or code),
            matched_alias=str(matched_alias or ""),
            match_reason=match_reason,
            score=1.0 if match_reason == "clarification" else 0.9,
            role=role,
        )

    def _clarification_matched_metrics(self, slots: list[ClarificationSlot]) -> list[dict[str, Any]]:
        flattened = []
        seen: set[tuple[str, str]] = set()
        for slot in slots:
            for option in slot.options[:3]:
                payload = option.to_payload()
                payload["role"] = slot.role
                key = (str(payload.get("code") or ""), str(payload.get("role") or ""))
                if key in seen:
                    continue
                seen.add(key)
                flattened.append(payload)
        return flattened[:6]

    def _clarification_warning(self, intent: str) -> str:
        return "needs_relation_clarification" if intent == "relation_why" else "needs_metric_clarification"

    def _context_metric_for_role(
        self,
        *,
        intent: str,
        role: str,
        context: RelationMemoryConversationContext,
    ) -> str | None:
        metrics = list(context.last_resolved_metric_codes or [])
        if not metrics:
            return None
        if intent == "relation_why":
            if role == "source" and len(metrics) >= 1:
                return metrics[0]
            if role == "target" and len(metrics) >= 2:
                return metrics[1]
            return None
        return metrics[0]

    def _extract_slots(self, question: str, intent: str) -> list[dict[str, str]]:
        cleaned = normalize_phrase_text(question)
        if not cleaned:
            return []
        if intent == "relation_why":
            between_slots = self._extract_best_between_slots(cleaned)
            if between_slots:
                return between_slots
        change_verb = CHANGE_VERB_PATTERN
        patterns = {
            "upstream": [
                re.compile(r"(?:что|кто|какие(?:\s+\w+)*)\s+влия(?:ет|ют)\s+на\s+(.+)$", re.IGNORECASE),
                re.compile(r"от\s+чего\s+зависит\s+(.+)$", re.IGNORECASE),
                re.compile(r"what\s+affects\s+(.+)$", re.IGNORECASE),
                re.compile(r"(?:факторы|причины|драйверы)(?:\s+\w+)*\s+(?:влияющие\s+на|влияния\s+на|для)\s+(.+)$", re.IGNORECASE),
                re.compile(r"(?:factors|drivers)\s+(?:affecting|for)\s+(.+)$", re.IGNORECASE),
                re.compile(rf"почему\s+(.+?)\s+(?:{change_verb})$", re.IGNORECASE),
                re.compile(rf"почему\s+(?:{change_verb})\s+(.+)$", re.IGNORECASE),
                re.compile(rf"из[\s-]*за\s+чего\s+(.+?)\s+(?:{change_verb})$", re.IGNORECASE),
                re.compile(rf"из[\s-]*за\s+чего\s+(?:{change_verb})\s+(.+)$", re.IGNORECASE),
                re.compile(r"из[\s-]*за\s+чего\s+(.+)$", re.IGNORECASE),
                re.compile(r"причин[аы]?\s+(.+)$", re.IGNORECASE),
            ],
            "downstream": [
                re.compile(r"куда\s+влияет\s+(.+)$", re.IGNORECASE),
                re.compile(r"на\s+что\s+влияет\s+(.+)$", re.IGNORECASE),
                re.compile(r"что\s+зависит\s+от\s+(.+)$", re.IGNORECASE),
                re.compile(r"what\s+does\s+(.+?)\s+affect$", re.IGNORECASE),
            ],
            "relation_why": [
                re.compile(r"связан(?:а|о|ы)?\s+ли\s+(.+?)\s+с\s+(.+)$", re.IGNORECASE),
                re.compile(r"(.+?)\s+связан(?:а|о|ы)?\s+ли\s+.+?\s+с\s+(.+)$", re.IGNORECASE),
                re.compile(r"(?:^|\s)влия(?:ет|ют)\s+ли\s+(.+?)\s+на\s+(.+)$", re.IGNORECASE),
                re.compile(r"(?:может|могут)\s+ли\s+(.+?)\s+влия(?:ть|ет|ют)\s+на\s+(.+)$", re.IGNORECASE),
                re.compile(r"(?:как|почему)?\s*(.+?)\s+влия(?:ет|ют)\s+на\s+(.+)$", re.IGNORECASE),
                re.compile(r"между\s+(.+?)\s+и\s+(.+)$", re.IGNORECASE),
                re.compile(r"(.+?)\s+связан(?:а|о|ы)?\s+с\s+(.+)$", re.IGNORECASE),
            ],
        }
        if intent == "change_cause":
            patterns["change_cause"] = [
                re.compile(rf"почему\s+(.+?)\s+(?:{change_verb})$", re.IGNORECASE),
                re.compile(rf"почему\s+(?:{change_verb})\s+(.+)$", re.IGNORECASE),
                re.compile(rf"why\s+is\s+(.+?)\s+(?:{change_verb})$", re.IGNORECASE),
                re.compile(rf"из[\s-]*за\s+чего\s+(.+?)\s+(?:{change_verb})$", re.IGNORECASE),
                re.compile(rf"из[\s-]*за\s+чего\s+(?:{change_verb})\s+(.+)$", re.IGNORECASE),
                re.compile(r"из[\s-]*за\s+чего\s+(.+)$", re.IGNORECASE),
                re.compile(r"причин[аы]?\s+изменени[яй]?\s+(.+)$", re.IGNORECASE),
            ]
        for pattern in patterns.get(intent, []):
            match = pattern.search(cleaned)
            if not match:
                continue
            if intent == "relation_why":
                return [
                    {"role": "source", "phrase": self._strip_metric_phrase(match.group(1))},
                    {"role": "target", "phrase": self._strip_metric_phrase(match.group(2))},
                ]
            return [{"role": "metric", "phrase": self._strip_metric_phrase(match.group(1))}]
        if intent == "relation_why":
            return [{"role": "source", "phrase": ""}, {"role": "target", "phrase": ""}]
        return [{"role": "metric", "phrase": ""}]

    def _extract_best_between_slots(self, cleaned: str) -> list[dict[str, str]]:
        match = re.search(r"\bмежду\s+(.+)$", cleaned, flags=re.IGNORECASE)
        if not match:
            return []
        body = match.group(1).strip()
        separators = list(re.finditer(r"\s+и\s+", body, flags=re.IGNORECASE))
        if not separators:
            return []

        ranked: list[tuple[float, int, str, str]] = []
        for separator in separators:
            source_phrase = self._strip_metric_phrase(body[: separator.start()])
            target_phrase = self._strip_metric_phrase(body[separator.end() :])
            if not source_phrase or not target_phrase:
                continue
            source, _source_candidates = self._resolve_metric_phrase(phrase=source_phrase, role="source")
            target, _target_candidates = self._resolve_metric_phrase(phrase=target_phrase, role="target")
            if source is None or target is None or source.code == target.code:
                continue
            score = float(source.score or 0.0) + float(target.score or 0.0)
            score += self._relation_split_boundary_bonus(source_phrase, target_phrase)
            ranked.append((score, -abs(len(source_phrase) - len(target_phrase)), source_phrase, target_phrase))

        if not ranked:
            return []
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
        _score, _balance, source_phrase, target_phrase = ranked[0]
        return [{"role": "source", "phrase": source_phrase}, {"role": "target", "phrase": target_phrase}]

    def _relation_split_boundary_bonus(self, source_phrase: str, target_phrase: str) -> float:
        score = 0.0
        for phrase in (source_phrase, target_phrase):
            normalized = normalize_user_text(phrase)
            if any(record.normalized == normalized for record in self.alias_records):
                score += 0.05
        return score

    def _strip_metric_phrase(self, value: str) -> str:
        cleaned = normalize_phrase_text(value)
        cleaned = re.sub(
            r"^(?:почему|это|эта|этот|эту|она|он|оно|а|и)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        return cleaned

    def _detect_intent(self, normalized_question: str) -> str:
        has_relation_word = any(token in normalized_question for token in ("связ", "relation", "relations", "edge", "edges"))
        has_overview_word = any(
            token in normalized_question
            for token in ("какие", "есть", "покажи", "список", "все", "обзор", "list", "show")
        )
        if any(token in normalized_question for token in ("гипотез", "hypothesis", "hypotheses")):
            return "hypotheses_overview"
        if any(token in normalized_question for token in ("наблюден", "observation", "observations")):
            return "observations_overview"
        if has_overview_word and any(token in normalized_question for token in ("граф", "graph")):
            return "relations_overview"
        if (
            has_overview_word
            and any(token in normalized_question for token in ("метрик", "metric", "metrics", "показател"))
            and not any(token in normalized_question for token in ("влия", "завис", "driver", "drivers"))
        ):
            return "metrics_overview"
        if any(token in normalized_question for token in ("pending", "подтвержд", "подтверд", "апрув", "approval", "кандидат")):
            return "pending_confirmations"
        if "провер" in normalized_question and (
            has_relation_word or any(token in normalized_question for token in ("что", "какие", "которые"))
        ):
            return "pending_confirmations"
        if self._looks_like_relation_why_question(normalized_question):
            return "relation_why"
        if re.search(r"(?:^|\s)влия(?:ет|ют)\s+ли\s+.+?\s+на\s+.+$", normalized_question, flags=re.IGNORECASE):
            return "relation_why"
        if re.search(r"(?:может|могут)\s+ли\s+.+?\s+влия(?:ть|ет|ют)\s+на\s+.+$", normalized_question, flags=re.IGNORECASE):
            return "relation_why"
        if re.search(r"^(?:как|почему)\s+.+?\s+влия(?:ет|ют)\s+на\s+.+$", normalized_question, flags=re.IGNORECASE):
            return "relation_why"
        if has_relation_word and has_overview_word:
            return "relations_overview"
        if self._looks_like_change_cause_question(normalized_question):
            return "change_cause"
        if any(token in normalized_question for token in ("куда влияет", "на что влияет", "что зависит от", "what does")):
            return "downstream"
        if any(
            token in normalized_question
            for token in (
                "что влияет",
                "кто влияет",
                "от чего зависит",
                "какие метрики влияют",
                "факторы",
                "влияющ",
                "what affects",
                "factors affecting",
                "affecting",
                "из за чего",
                "из-за чего",
                "почему",
                "причина",
            )
        ):
            return "upstream"
        return "unknown"

    def _looks_like_relation_why_question(self, normalized_question: str) -> bool:
        return bool(
            re.search(r"(?:есть\s+ли\s+)?связь\s+между\s+.+?\s+и\s+.+", normalized_question)
            or re.search(r"(?:^|.+?\s+)связан(?:а|о|ы)?\s+ли\s+.+?\s+с\s+.+", normalized_question)
            or re.search(r".+?\s+связан(?:а|о|ы)?\s+с\s+.+", normalized_question)
            or re.search(r"\bbetween\b.+?\band\b", normalized_question)
        )

    def _looks_like_change_cause_question(self, normalized_question: str) -> bool:
        has_cause_prefix = any(
            phrase in normalized_question
            for phrase in ("почему", "из за чего", "из-за чего", "why")
        )
        has_cause_prefix = has_cause_prefix or bool(re.search(r"причин\w*\s+изменени", normalized_question))
        if not has_cause_prefix:
            return False
        return bool(re.search(CHANGE_VERB_PATTERN, normalized_question, flags=re.IGNORECASE)) or any(
            token in normalized_question
            for token in ("рост", "вверх", "снижение", "падение", "изменени", "decline", "growth", "change")
        )

    def _looks_like_new_question(self, question: str, explicit_intent: str) -> bool:
        if explicit_intent != "unknown":
            return True
        normalized = normalize_user_text(question)
        if question.strip().endswith("?") and re.search(
            r"^(?:что|как|почему|зачем|куда|откуда|какие|какая|какой|какую|сколько|можно ли|есть ли)\b",
            normalized,
        ):
            return True
        tokens = informative_tokens(normalized)
        return len(tokens) >= 4

    def _is_contextual_follow_up(self, question: str) -> bool:
        normalized = normalize_user_text(question)
        tokens = normalized.split()
        if not tokens:
            return False
        return any(token in CONTEXTUAL_TOKENS for token in tokens)

    def _is_contextual_phrase(self, phrase: str) -> bool:
        normalized = normalize_user_text(phrase)
        if not normalized:
            return True
        tokens = normalized.split()
        if not tokens:
            return True
        return all(token in CONTEXTUAL_TOKENS or token in {"а", "и", "на", "про"} for token in tokens)

    def _matched_tokens(self, source_tokens: list[str], target_tokens: list[str]) -> list[str]:
        matched: list[str] = []
        for token in source_tokens:
            if token in target_tokens:
                matched.append(token)
                continue
            for candidate in target_tokens:
                if SequenceMatcher(None, token, candidate).ratio() >= 0.84:
                    matched.append(token)
                    break
        return matched

    def _option_aliases(self, option: ResolvedMetricCandidate) -> list[str]:
        metric = self.metric_lookup.get(option.code, {})
        return list(
            dict.fromkeys(
                [
                    option.code,
                    option.label,
                    option.matched_alias,
                    *(metric.get("approved_aliases") or []),
                    *(metric.get("aliases") or []),
                ]
            )
        )

    def _is_persistable_alias(self, alias: str, code: str) -> bool:
        normalized_alias = normalize_user_text(alias)
        if not normalized_alias:
            return False
        if self._is_contextual_phrase(alias):
            return False
        metric = self.metric_lookup.get(code, {})
        known_aliases = {
            normalize_user_text(item)
            for item in [
                code,
                code.replace("_", " "),
                metric.get("label") or "",
                *(metric.get("approved_aliases") or []),
                *(metric.get("aliases") or []),
            ]
            if str(item or "").strip()
        }
        return normalized_alias not in known_aliases
