from __future__ import annotations

import re

from app.config import settings
from app.services.relation_memory_agent import LlmJsonError, RelationMemoryLlmClient
from app.services.relation_memory_language_utils import (
    CHANGE_DOWN_PATTERN,
    CHANGE_UP_PATTERN,
    CHANGE_VERB_PATTERN,
    normalize_phrase_text,
    normalize_user_text,
)
from app.services.relation_memory_question_models import QuestionPlan


class QuestionPlanner:
    CHANGE_UP_PATTERN = CHANGE_UP_PATTERN
    CHANGE_DOWN_PATTERN = CHANGE_DOWN_PATTERN
    CHANGE_VERB_PATTERN = CHANGE_VERB_PATTERN

    QUESTION_PREFIXES = (
        "что ",
        "кто ",
        "где ",
        "когда ",
        "куда ",
        "как ",
        "какие ",
        "какая ",
        "какой ",
        "факторы ",
        "причины ",
        "драйверы ",
        "почему ",
        "зачем ",
        "сколько ",
        "можно ",
        "покажи ",
        "расскажи ",
        "объясни ",
        "объясните ",
        "сравни ",
        "сравните ",
        "найди ",
        "найдите ",
        "перечисли ",
        "перечислите ",
        "опиши ",
        "опишите ",
        "сделай обзор",
        "дай обзор",
        "дай ",
        "what ",
        "which ",
        "where ",
        "when ",
        "how ",
        "why ",
        "show ",
        "list ",
        "explain ",
        "compare ",
        "find ",
        "describe ",
        "summarize ",
        "tell ",
    )

    def __init__(self, *, llm_client: RelationMemoryLlmClient | None = None, enabled: bool | None = None):
        self.enabled = settings.RELATION_MEMORY_LLM_ANSWER_ENABLED if enabled is None else enabled
        self.llm_client = llm_client

    def plan(self, *, question: str) -> QuestionPlan:
        fallback_plan = self._deterministic_plan(question)
        if self.enabled and fallback_plan.raw_question and not self._should_use_deterministic_plan(fallback_plan):
            llm_plan = self._llm_plan(question=question, fallback_plan=fallback_plan)
            if llm_plan is not None:
                return self._select_plan(llm_plan=llm_plan, fallback_plan=fallback_plan)
        return fallback_plan

    def _should_use_deterministic_plan(self, fallback_plan: QuestionPlan) -> bool:
        return fallback_plan.intent not in {"unknown", "statement"} and fallback_plan.confidence >= 0.85

    def _deterministic_plan(self, question: str) -> QuestionPlan:
        raw_question = str(question or "").strip()
        normalized = normalize_user_text(raw_question)
        is_question = "?" in raw_question or normalized.startswith(self.QUESTION_PREFIXES)
        intent = self._detect_intent(normalized)
        if intent != "unknown":
            is_question = True
        if not raw_question:
            intent = "unknown"
            is_question = False
        elif not is_question and intent == "unknown":
            intent = "statement"
        return QuestionPlan(
            raw_question=raw_question,
            intent=intent,
            metric_slots=self._extract_slots(raw_question, intent),
            evidence_type=self._evidence_type_for_intent(intent),
            requested_direction=self._requested_change_direction(normalized),
            is_question=is_question,
            confidence=0.9 if intent not in {"unknown", "statement"} else 0.4,
        )

    def _llm_plan(self, *, question: str, fallback_plan: QuestionPlan) -> QuestionPlan | None:
        client = self.llm_client or RelationMemoryLlmClient(
            timeout_seconds=int(settings.RELATION_MEMORY_LLM_ANSWER_TIMEOUT_SECONDS)
        )
        system_prompt = (
            "Classify a business relation-memory user message. Return strict JSON only with keys: "
            "intent, metric_slots, evidence_type, answer_shape, requested_direction, is_question, confidence. "
            "intent must be one of upstream, downstream, relation_why, change_cause, "
            "relations_overview, hypotheses_overview, observations_overview, metrics_overview, "
            "pending_confirmations, statement, unknown. requested_direction is up, down, or empty. "
            "metric_slots must contain natural-language metric phrases, not invented metric codes. "
            "For one-metric questions, return one slot with role metric. For relation_why, return source and target. "
            "If the user asks why a metric is growing, falling, changing, declining, rising, or 'падает/растет', "
            "use intent=change_cause, not upstream. "
            "Example: 'почему выручка поползла вверх?' => "
            "{\"intent\":\"change_cause\",\"metric_slots\":[{\"role\":\"metric\",\"phrase\":\"выручка\"}],"
            "\"requested_direction\":\"up\",\"is_question\":true,\"confidence\":0.9}. "
            "Example: 'почему выручка падает?' => "
            "{\"intent\":\"change_cause\",\"metric_slots\":[{\"role\":\"metric\",\"phrase\":\"выручка\"}],"
            "\"requested_direction\":\"down\",\"is_question\":true,\"confidence\":0.9}. "
            "Do not infer facts or metric codes."
        )
        try:
            payload = client.chat_json(
                system_prompt=system_prompt,
                user_payload={
                    "question": question,
                    "fallback_intent": fallback_plan.intent,
                    "fallback_metric_slots": fallback_plan.metric_slots,
                },
            )
        except LlmJsonError:
            return None
        intent = str(payload.get("intent") or fallback_plan.intent)
        allowed = {
            "upstream",
            "downstream",
            "relation_why",
            "change_cause",
            "relations_overview",
            "hypotheses_overview",
            "observations_overview",
            "metrics_overview",
            "pending_confirmations",
            "statement",
            "unknown",
        }
        if intent not in allowed:
            intent = fallback_plan.intent
        slots = payload.get("metric_slots")
        if not isinstance(slots, list):
            slots = fallback_plan.metric_slots
        try:
            confidence = min(1.0, max(0.0, float(payload.get("confidence", fallback_plan.confidence))))
        except (TypeError, ValueError):
            confidence = fallback_plan.confidence
        return QuestionPlan(
            raw_question=fallback_plan.raw_question,
            intent=intent,
            metric_slots=[
                {"role": str(item.get("role") or "metric"), "phrase": str(item.get("phrase") or "")}
                for item in slots
                if isinstance(item, dict)
            ],
            evidence_type=str(payload.get("evidence_type") or self._evidence_type_for_intent(intent)),
            answer_shape=str(payload.get("answer_shape") or fallback_plan.answer_shape),
            requested_direction=str(payload.get("requested_direction") or fallback_plan.requested_direction),
            is_question=bool(payload.get("is_question", fallback_plan.is_question)),
            confidence=confidence,
            warnings=[],
            planner_source="llm",
        )

    def _select_plan(self, *, llm_plan: QuestionPlan, fallback_plan: QuestionPlan) -> QuestionPlan:
        if (
            llm_plan.intent in {"unknown", "statement"}
            and fallback_plan.intent not in {"unknown", "statement"}
            and fallback_plan.confidence >= 0.7
        ):
            fallback_plan.warnings.append("llm_question_plan_fallback_to_deterministic")
            return fallback_plan
        if (
            llm_plan.confidence < 0.35
            and fallback_plan.intent not in {"unknown", "statement"}
            and fallback_plan.confidence >= 0.7
        ):
            fallback_plan.warnings.append("llm_question_plan_low_confidence")
            return fallback_plan
        if (
            fallback_plan.intent == "change_cause"
            and fallback_plan.requested_direction
            and llm_plan.intent in {"upstream", "downstream"}
        ):
            fallback_plan.warnings.append("llm_question_plan_overridden_by_change_heuristic")
            return fallback_plan
        if not llm_plan.metric_slots and fallback_plan.metric_slots:
            llm_plan.metric_slots = list(fallback_plan.metric_slots)
        if not llm_plan.requested_direction and fallback_plan.requested_direction:
            llm_plan.requested_direction = fallback_plan.requested_direction
        if not llm_plan.evidence_type or llm_plan.evidence_type == "unknown":
            llm_plan.evidence_type = self._evidence_type_for_intent(llm_plan.intent)
        return llm_plan

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
        if self._looks_like_change_cause(normalized_question):
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

    def _looks_like_change_cause(self, normalized_question: str) -> bool:
        has_cause_prefix = any(
            phrase in normalized_question
            for phrase in ("почему", "из за чего", "из-за чего", "why")
        )
        has_cause_prefix = has_cause_prefix or bool(re.search(r"причин\w*\s+изменени", normalized_question))
        if not has_cause_prefix:
            return False
        return bool(re.search(self.CHANGE_VERB_PATTERN, normalized_question, flags=re.IGNORECASE)) or any(
            token in normalized_question
            for token in ("рост", "вверх", "снижение", "падение", "изменени", "decline", "growth", "change")
        )

    def _requested_change_direction(self, normalized_question: str) -> str:
        if re.search(self.CHANGE_UP_PATTERN, normalized_question, flags=re.IGNORECASE) or any(
            token in normalized_question for token in ("рост", "вверх", "growth")
        ):
            return "up"
        if re.search(self.CHANGE_DOWN_PATTERN, normalized_question, flags=re.IGNORECASE) or any(
            token in normalized_question for token in ("падение", "снижение", "вниз", "decline", "drop")
        ):
            return "down"
        return ""

    def _extract_slots(self, question: str, intent: str) -> list[dict[str, str]]:
        cleaned = normalize_phrase_text(question)
        change_verb = self.CHANGE_VERB_PATTERN
        ambiguous_between = False
        if intent == "relation_why":
            between_slots = self._extract_simple_between_slots(cleaned)
            if between_slots:
                return between_slots
            ambiguous_between = bool(re.search(r"\bмежду\s+.+\s+и\s+.+", cleaned, flags=re.IGNORECASE))
        patterns = {
            "upstream": [
                re.compile(r"(?:что|кто|какие(?:\s+\w+)*)\s+влия(?:ет|ют)\s+на\s+(.+)$", re.IGNORECASE),
                re.compile(r"от\s+чего\s+зависит\s+(.+)$", re.IGNORECASE),
                re.compile(r"what\s+affects\s+(.+)$", re.IGNORECASE),
                re.compile(r"(?:факторы|причины|драйверы)(?:\s+\w+)*\s+(?:влияющие\s+на|влияния\s+на|для)\s+(.+)$", re.IGNORECASE),
                re.compile(r"(?:factors|drivers)\s+(?:affecting|for)\s+(.+)$", re.IGNORECASE),
                re.compile(r"из[\s-]*за\s+чего\s+(.+)$", re.IGNORECASE),
                re.compile(r"причин[аы]?\s+(.+)$", re.IGNORECASE),
            ],
            "change_cause": [
                re.compile(rf"почему\s+(.+?)\s+(?:{change_verb})(?:\s|$)", re.IGNORECASE),
                re.compile(rf"почему\s+(?:{change_verb})\s+(.+)$", re.IGNORECASE),
                re.compile(rf"why\s+is\s+(.+?)\s+(?:{change_verb})(?:\s|$)", re.IGNORECASE),
                re.compile(rf"из[\s-]*за\s+чего\s+(.+?)\s+(?:{change_verb})(?:\s|$)", re.IGNORECASE),
                re.compile(rf"из[\s-]*за\s+чего\s+(?:{change_verb})\s+(.+)$", re.IGNORECASE),
                re.compile(r"причин[аы]?\s+изменени[яй]?\s+(.+)$", re.IGNORECASE),
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
        for pattern in patterns.get(intent, []):
            match = pattern.search(cleaned)
            if not match:
                continue
            if ambiguous_between and pattern.pattern.startswith("между"):
                continue
            if intent == "relation_why":
                return [
                    {"role": "source", "phrase": self._strip_metric_phrase(match.group(1))},
                    {"role": "target", "phrase": self._strip_metric_phrase(match.group(2))},
                ]
            return [{"role": "metric", "phrase": self._strip_metric_phrase(match.group(1))}]
        return []

    def _extract_simple_between_slots(self, cleaned: str) -> list[dict[str, str]]:
        match = re.search(r"\bмежду\s+(.+)$", cleaned, flags=re.IGNORECASE)
        if not match:
            return []
        body = match.group(1).strip()
        separators = list(re.finditer(r"\s+и\s+", body, flags=re.IGNORECASE))
        if len(separators) != 1:
            return []
        separator = separators[0]
        source = self._strip_metric_phrase(body[: separator.start()])
        target = self._strip_metric_phrase(body[separator.end() :])
        if not source or not target:
            return []
        return [{"role": "source", "phrase": source}, {"role": "target", "phrase": target}]

    def _strip_metric_phrase(self, value: str) -> str:
        cleaned = normalize_phrase_text(value)
        return re.sub(r"^(?:почему|это|эта|этот|эту|она|он|оно|а|и)\s+", "", cleaned, flags=re.IGNORECASE).strip()

    def _evidence_type_for_intent(self, intent: str) -> str:
        if intent in {"upstream", "downstream", "relation_why"}:
            return "graph_paths"
        if intent == "change_cause":
            return "observations_hypotheses"
        if intent.endswith("_overview"):
            return "overview"
        if intent == "pending_confirmations":
            return "pending_confirmations"
        return "unknown"
