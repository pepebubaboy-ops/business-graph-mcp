from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.schemas.relation_memory import CandidateRelation
from app.services.relation_memory_ingestion import TextChunk, normalize_metric_code


ALLOWED_EDGE_TYPES = {"driver", "inverse_driver", "component", "lag"}


class LlmJsonError(ValueError):
    pass


@dataclass
class LlmCandidateResult:
    assistant_message: str
    candidate_relations: list[dict[str, Any]]
    warnings: list[str]


@dataclass
class UserAnswerMemoryResult:
    assistant_message: str
    candidate_relations: list[dict[str, Any]]
    memory_facts: list[dict[str, Any]]
    warnings: list[str]


@dataclass
class GraphAnswerNarrationResult:
    answer: str
    warnings: list[str]


@dataclass
class ExternalContextSuggestionResult:
    suggestions: list[dict[str, Any]]
    warnings: list[str]


class RelationMemoryLlmClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int = 30,
    ):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.LLM_MODEL
        self.api_key = api_key or settings.LLM_API_KEY
        self.timeout_seconds = timeout_seconds

    def chat_json(self, *, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(request_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LlmJsonError(f"LLM request failed: {exc}") from exc

        try:
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmJsonError("LLM response did not contain choices[0].message.content") from exc
        return parse_json_object(content)


def parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    elif not cleaned.startswith("{"):
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first >= 0 and last > first:
            cleaned = cleaned[first : last + 1]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LlmJsonError(f"LLM output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LlmJsonError("LLM output must be a JSON object")
    return payload


class RelationMemoryAgent:
    def __init__(self, llm_client: RelationMemoryLlmClient | None = None):
        self.llm_client = llm_client or RelationMemoryLlmClient()

    def extract_relations_from_text(
        self,
        *,
        chunks: list[TextChunk],
        known_metrics: list[dict[str, Any]],
    ) -> LlmCandidateResult:
        if not chunks:
            return LlmCandidateResult("", [], [])
        system_prompt = (
            "You extract candidate metric relationships from business text. "
            "Return strict JSON only with keys assistant_message and candidate_relations. "
            "candidate_relations must be an array of objects with source_metric_code, "
            "target_metric_code, edge_type, lag_period, note, evidence, confidence, "
            "source_document_id. edge_type must be driver, inverse_driver, component, or lag. "
            "Do not mark anything as confirmed."
        )
        user_payload = {
            "known_metrics": known_metrics,
            "chunks": [chunk.to_dict() for chunk in chunks[:8]],
        }
        return self._safe_chat(system_prompt=system_prompt, user_payload=user_payload)

    def normalize_user_message(
        self,
        *,
        message: str,
        known_metrics: list[dict[str, Any]],
        pending_confirmations: list[CandidateRelation],
    ) -> LlmCandidateResult:
        system_prompt = (
            "Ты преобразуешь свободное описание бизнес-процесса в кандидатные связи между метриками. "
            "Верни strict JSON only with keys assistant_message and candidate_relations. "
            "assistant_message всегда пиши на русском языке, без английского, без фраз 'assuming/correcting', "
            "без объяснения исправлений опечаток. Never confirm or save anything. "
            "Если пользователь только говорит да/нет без описания нового процесса, верни пустой candidate_relations "
            "и коротко скажи по-русски, что нужно использовать явное действие подтверждения."
        )
        user_payload = {
            "message": message,
            "known_metrics": known_metrics,
            "pending_confirmations": [item.model_dump() for item in pending_confirmations],
        }
        return self._safe_chat(system_prompt=system_prompt, user_payload=user_payload)

    def interpret_user_answer(
        self,
        *,
        message: str,
        open_question: dict[str, Any],
        known_metrics: list[dict[str, Any]],
        pending_confirmations: list[CandidateRelation],
    ) -> UserAnswerMemoryResult:
        system_prompt = (
            "Ты обрабатываешь ответ пользователя на уточняющий вопрос relation-memory агента. "
            "Верни strict JSON only with keys assistant_message, candidate_relations, memory_facts. "
            "candidate_relations - массив кандидатных связей для проверки, не подтвержденные факты. "
            "Если open_question содержит target_metric_code и пользователь называет причину/драйвер, "
            "создай связь source_metric_code -> target_metric_code с edge_type=driver. "
            "Если нужной source-метрики нет в known_metrics, создай новый snake_case source_metric_code. "
            "memory_facts - массив кратких фактов из ответа пользователя: kind, content, keywords, metric_codes, confidence. "
            "Пиши assistant_message на русском. Не подтверждай связь окончательно; только предложи проверить карточки."
        )
        user_payload = {
            "message": message,
            "open_question": open_question,
            "known_metrics": known_metrics,
            "pending_confirmations": [item.model_dump() for item in pending_confirmations],
        }
        try:
            payload = self.llm_client.chat_json(
                system_prompt=system_prompt, user_payload=user_payload
            )
        except LlmJsonError as exc:
            return UserAnswerMemoryResult(
                assistant_message="Запомнил ответ как контекст, но не смог надежно разобрать его в новые связи.",
                candidate_relations=[],
                memory_facts=[],
                warnings=[str(exc)],
            )
        return UserAnswerMemoryResult(
            assistant_message=str(payload.get("assistant_message") or ""),
            candidate_relations=list(payload.get("candidate_relations") or []),
            memory_facts=list(payload.get("memory_facts") or []),
            warnings=[],
        )

    def build_initial_message(
        self,
        *,
        observations: list[dict[str, Any]],
        hypotheses: list[dict[str, Any]],
        inquiry_questions: list[dict[str, Any]],
        memory_priors: list[dict[str, Any]],
    ) -> str:
        if inquiry_questions:
            question_text = inquiry_questions[0]["prompt"]
        else:
            question_text = (
                "Опишите, какие метрики в этих данных действительно связаны бизнес-процессом."
            )
        return (
            f"Я нашел {len(observations)} наблюдений, {len(hypotheses)} гипотез и "
            f"{len(memory_priors)} подтвержденных связей в памяти. Первый вопрос: {question_text}"
        )

    def _safe_chat(self, *, system_prompt: str, user_payload: dict[str, Any]) -> LlmCandidateResult:
        try:
            payload = self.llm_client.chat_json(
                system_prompt=system_prompt, user_payload=user_payload
            )
        except LlmJsonError as exc:
            return LlmCandidateResult(
                assistant_message="Не смог надежно разобрать ответ LLM в JSON, поэтому ничего не предлагаю к сохранению.",
                candidate_relations=[],
                warnings=[str(exc)],
            )
        return LlmCandidateResult(
            assistant_message=str(payload.get("assistant_message") or ""),
            candidate_relations=list(payload.get("candidate_relations") or []),
            warnings=[],
        )


class RelationMemoryAnswerNarrator:
    def __init__(
        self,
        llm_client: RelationMemoryLlmClient | None = None,
        *,
        enabled: bool | None = None,
        max_rows: int | None = None,
    ):
        self.enabled = settings.RELATION_MEMORY_LLM_ANSWER_ENABLED if enabled is None else enabled
        self.max_rows = max_rows or settings.RELATION_MEMORY_LLM_ANSWER_MAX_ROWS
        self.llm_client = llm_client or RelationMemoryLlmClient(
            timeout_seconds=int(settings.RELATION_MEMORY_LLM_ANSWER_TIMEOUT_SECONDS)
        )

    def narrate(
        self,
        *,
        question: str,
        intent: str,
        fallback_answer: str,
        matched_metrics: list[dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> GraphAnswerNarrationResult:
        if not self.enabled or not rows:
            return GraphAnswerNarrationResult(answer=fallback_answer, warnings=[])

        system_prompt = (
            "Ты объясняешь ответы graph-QA бизнес-пользователю на русском языке. "
            "Используй только факты из payload. Не придумывай новых связей, не меняй смысл. "
            "Сделай ответ коротким и человеческим: 2-4 предложения, без слов hops, strength, cypher, metric_code. "
            "Если связь прямая, скажи это явно. Если есть косвенные связи, назови 1-2 наиболее заметные. "
            "Названия метрик бери ровно из source_label, target_label и path_text; не заменяй их английскими label или metric_code. "
            'Верни strict JSON only: {"answer": "..."}.'
        )
        user_payload = {
            "question": question,
            "intent": intent,
            "fallback_answer": fallback_answer,
            "matched_metrics": matched_metrics[:3],
            "rows": [
                {
                    "source_label": row.get("source_label"),
                    "target_label": row.get("target_label"),
                    "path_text": row.get("path_text"),
                    "hops": row.get("hops"),
                    "first_reason": row.get("first_reason"),
                }
                for row in rows[: self.max_rows]
            ],
        }
        try:
            payload = self.llm_client.chat_json(
                system_prompt=system_prompt, user_payload=user_payload
            )
        except LlmJsonError as exc:
            return GraphAnswerNarrationResult(
                answer=fallback_answer,
                warnings=[f"llm_answer_fallback:{exc}"],
            )

        answer = str(payload.get("answer") or "").strip()
        if not answer:
            return GraphAnswerNarrationResult(
                answer=fallback_answer,
                warnings=["llm_answer_fallback:empty_answer"],
            )
        return GraphAnswerNarrationResult(answer=answer, warnings=[])


class RelationMemoryExternalContextSuggester:
    def __init__(
        self,
        llm_client: RelationMemoryLlmClient | None = None,
        *,
        enabled: bool | None = None,
        max_suggestions: int = 3,
    ):
        self.enabled = (
            settings.RELATION_MEMORY_EXTERNAL_CONTEXT_LLM_ENABLED if enabled is None else enabled
        )
        self.max_suggestions = max_suggestions
        self.llm_client = llm_client or RelationMemoryLlmClient(
            timeout_seconds=int(settings.RELATION_MEMORY_LLM_ANSWER_TIMEOUT_SECONDS)
        )

    def suggest(
        self,
        *,
        question: str,
        intent: str,
        anchor_metric: dict[str, Any],
        known_metrics: list[dict[str, Any]],
    ) -> ExternalContextSuggestionResult:
        if not self.enabled or intent not in {"upstream", "downstream"}:
            return ExternalContextSuggestionResult(suggestions=[], warnings=[])
        system_prompt = (
            "Ты предлагаешь внешний бизнес-контекст для Relation Memory, когда в текущем графе нет фактов. "
            "Верни strict JSON only with key suggestions. suggestions is an array of 0-3 objects. "
            "Каждый объект: source_metric_code, source_label, target_metric_code, target_label, edge_type, rationale, confidence. "
            "edge_type must be one of driver, inverse_driver, component, lag; обычно используй driver. "
            "Это НЕ подтвержденные факты, а кандидаты для проверки пользователем. "
            "Для intent=upstream target_metric_code должен быть ровно anchor_metric.code; предложи внешние драйверы. "
            "Для intent=downstream source_metric_code должен быть ровно anchor_metric.code; предложи возможные следствия. "
            "Новые metric_code пиши snake_case латиницей. Labels и rationale пиши на русском. "
            "Не используй числа, если их нет в payload."
        )
        user_payload = {
            "question": question,
            "intent": intent,
            "anchor_metric": anchor_metric,
            "known_metrics": known_metrics[:80],
            "max_suggestions": self.max_suggestions,
        }
        try:
            payload = self.llm_client.chat_json(
                system_prompt=system_prompt, user_payload=user_payload
            )
        except LlmJsonError as exc:
            return ExternalContextSuggestionResult(
                suggestions=[], warnings=[f"external_context_fallback:{exc}"]
            )
        raw_suggestions = payload.get("suggestions") or []
        if not isinstance(raw_suggestions, list):
            return ExternalContextSuggestionResult(
                suggestions=[], warnings=["external_context_fallback:invalid_suggestions"]
            )
        return ExternalContextSuggestionResult(
            suggestions=[
                item for item in raw_suggestions[: self.max_suggestions] if isinstance(item, dict)
            ],
            warnings=[],
        )


def normalize_candidate_payload(
    raw: dict[str, Any], *, default_source: str
) -> dict[str, Any] | None:
    source = normalize_metric_code(str(raw.get("source_metric_code") or raw.get("source") or ""))
    target = normalize_metric_code(str(raw.get("target_metric_code") or raw.get("target") or ""))
    if not source or not target or source == "metric" or target == "metric":
        return None
    edge_type = str(raw.get("edge_type") or "driver").strip()
    if edge_type not in ALLOWED_EDGE_TYPES:
        return None
    confidence = raw.get("confidence")
    try:
        confidence_value = min(1.0, max(0.0, float(confidence if confidence is not None else 0.5)))
    except (TypeError, ValueError):
        confidence_value = 0.5
    raw_score = raw.get("score")
    try:
        score_value = min(
            1.0, max(0.0, float(raw_score if raw_score is not None else confidence_value))
        )
    except (TypeError, ValueError):
        score_value = confidence_value
    return {
        "source_metric_code": source,
        "target_metric_code": target,
        "edge_type": edge_type,
        "relation_type": str(raw.get("relation_type") or edge_type).strip(),
        "lag_period": raw.get("lag_period"),
        "note": str(raw.get("note") or raw.get("reason") or "").strip(),
        "evidence": str(raw.get("evidence") or "").strip(),
        "evidence_type": str(raw.get("evidence_type") or "").strip(),
        "confidence": confidence_value,
        "score": score_value,
        "needs_approval_reason": str(raw.get("needs_approval_reason") or "").strip(),
        "source": str(raw.get("source") or default_source),
        "source_document_id": raw.get("source_document_id"),
        "source_hypothesis_id": raw.get("source_hypothesis_id"),
    }
