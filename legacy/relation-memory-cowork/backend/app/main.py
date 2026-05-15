from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, StreamingResponse

from app.config import settings
from app.schemas.relation_memory import (
    ChatMessageRequest,
    ChatMessageResponse,
    ConfirmationRequest,
    ConfirmationResponse,
    GraphQuestionRequest,
    GraphQuestionResponse,
    MemoryRelationsResponse,
    MetricCandidateConfirmationResponse,
    MetricCandidatesResponse,
    RelationMemoryChatRequest,
    RelationMemoryChatResponse,
    RelationMemorySessionResponse,
)
from app.services.relation_memory_ingestion import IngestionError, UploadedFilePayload
from app.services.relation_memory_session import RelationMemorySessionService


DEMO_HTML_PATH = Path(__file__).resolve().parent / "static" / "relation_memory_demo.html"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_PRESETS = {
    "synthetic-ftl": {
        "id": "synthetic-ftl",
        "label": "Synthetic FTL",
        "description": "Синтетическая FTL-себестоимость для быстрого demo и graph questions.",
        "files": [
            PROJECT_ROOT / "work_data" / "synthetic_ftl_cost_experiment_v2" / "синтетическая_себестоимость_ftl_focus_fact_v2.xlsx"
        ],
    },
    "synthetic-finance": {
        "id": "synthetic-finance",
        "label": "Synthetic Finance",
        "description": "Небольшой finance demo dataset с простыми зависимостями и pending confirmations.",
        "files": [
            PROJECT_ROOT / "backend" / "data" / "relation_memory_poc" / "demo_v2" / "finance_fact.xlsx"
        ],
    },
    "synthetic-ftl-year": {
        "id": "synthetic-ftl-year",
        "label": "Synthetic FTL Year",
        "description": "Годовая синтетика FTL-себестоимости с государством, погодой и рынком FTL.",
        "files": [
            PROJECT_ROOT / "backend" / "data" / "relation_memory_poc" / "synthetic_ftl_year" / "synthetic_ftl_cost_year.xlsx",
            PROJECT_ROOT / "backend" / "data" / "relation_memory_poc" / "synthetic_ftl_year" / "synthetic_ftl_external_context_year.xlsx",
            PROJECT_ROOT / "backend" / "data" / "relation_memory_poc" / "synthetic_ftl_year" / "synthetic_ftl_metric_dictionary.xlsx",
            PROJECT_ROOT / "backend" / "data" / "relation_memory_poc" / "synthetic_ftl_year" / "synthetic_ftl_dependency_rules.xlsx",
        ],
    },
}


def _missing_demo_preset_files(preset: dict) -> list[Path]:
    return [path for path in preset["files"] if not path.exists()]


def _demo_preset_payload(preset: dict) -> dict:
    return {
        "id": preset["id"],
        "label": preset["label"],
        "description": preset["description"],
        "filenames": [path.name for path in preset["files"]],
    }


def _is_llm_reachable() -> bool:
    request = urllib.request.Request(
        f"{settings.llm_base_url}/models",
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=0.75):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code in {200, 401, 403}
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def create_app(session_service: RelationMemorySessionService | None = None) -> FastAPI:
    app = FastAPI(title="Relation Memory POC")
    app.state.relation_memory_session_service = session_service or RelationMemorySessionService()

    @app.get("/relation-memory/demo", include_in_schema=False)
    async def relation_memory_demo():
        return FileResponse(DEMO_HTML_PATH)

    @app.get("/relation-memory/demo/presets", include_in_schema=False)
    async def relation_memory_demo_presets():
        return [
            _demo_preset_payload(preset)
            for preset in DEMO_PRESETS.values()
            if not _missing_demo_preset_files(preset)
        ]

    @app.get("/relation-memory/llm/status", include_in_schema=False)
    async def relation_memory_llm_status():
        reachable = _is_llm_reachable()
        return {
            "provider": settings.LLM_PROVIDER,
            "base_url": settings.llm_base_url,
            "model": settings.LLM_MODEL,
            "reachable": reachable,
            "answer_narration_enabled": settings.RELATION_MEMORY_LLM_NARRATOR_ENABLED,
            "question_planning_enabled": settings.RELATION_MEMORY_LLM_ANSWER_ENABLED,
            "external_context_enabled": settings.RELATION_MEMORY_EXTERNAL_CONTEXT_LLM_ENABLED,
            "semantic_resolver_enabled": settings.RELATION_MEMORY_OLLAMA_ENABLED,
        }

    @app.post("/relation-memory/demo/presets/{preset_id}/sessions", response_model=RelationMemorySessionResponse, include_in_schema=False)
    async def create_relation_memory_demo_preset_session(preset_id: str):
        preset = DEMO_PRESETS.get(preset_id)
        if not preset:
            raise HTTPException(status_code=404, detail=f"Unknown demo preset: {preset_id}")
        payloads = []
        missing_files = _missing_demo_preset_files(preset)
        if missing_files:
            raise HTTPException(
                status_code=404,
                detail="Demo preset is unavailable because files are missing: "
                + ", ".join(str(path) for path in missing_files),
            )
        for path in preset["files"]:
            payloads.append(UploadedFilePayload(filename=path.name, content=path.read_bytes()))
        try:
            return app.state.relation_memory_session_service.create_session(payloads)
        except IngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/relation-memory/sessions", response_model=RelationMemorySessionResponse)
    async def create_relation_memory_session(files: list[UploadFile] = File(...)):
        try:
            payloads = [
                UploadedFilePayload(filename=file.filename or "upload", content=await file.read())
                for file in files
            ]
            if not payloads:
                raise HTTPException(status_code=400, detail="At least one file is required.")
            return app.state.relation_memory_session_service.create_session(payloads)
        except IngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/relation-memory/sessions/{session_id}/files", response_model=RelationMemorySessionResponse)
    async def add_files_to_relation_memory_session(session_id: str, files: list[UploadFile] = File(...)):
        try:
            payloads = [
                UploadedFilePayload(filename=file.filename or "upload", content=await file.read())
                for file in files
            ]
            if not payloads:
                raise HTTPException(status_code=400, detail="At least one file is required.")
            return app.state.relation_memory_session_service.add_files_to_session(session_id, payloads)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except IngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/relation-memory/sessions/{session_id}", response_model=RelationMemorySessionResponse)
    async def get_relation_memory_session(session_id: str):
        try:
            return app.state.relation_memory_session_service.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/relation-memory/sessions/{session_id}/rebuild", response_model=RelationMemorySessionResponse)
    async def rebuild_relation_memory_session(session_id: str):
        try:
            return app.state.relation_memory_session_service.rebuild_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/relation-memory/sessions/{session_id}/messages", response_model=ChatMessageResponse)
    async def post_relation_memory_message(session_id: str, request: ChatMessageRequest):
        service = app.state.relation_memory_session_service
        try:
            assistant_message, pending_confirmations, warnings = service.handle_message(
                session_id,
                request.message,
            )
            debug_trace = service.get_last_debug_trace(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ChatMessageResponse(
            session_id=session_id,
            assistant_message=assistant_message,
            pending_confirmations=pending_confirmations,
            warnings=warnings,
            debug_trace=debug_trace,
        )

    @app.post("/relation-memory/sessions/{session_id}/chat", response_model=RelationMemoryChatResponse)
    async def post_relation_memory_chat_message(session_id: str, request: RelationMemoryChatRequest):
        try:
            return app.state.relation_memory_session_service.handle_chat_message(
                session_id,
                request.message,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/relation-memory/sessions/{session_id}/chat/trace")
    async def stream_relation_memory_chat_message(session_id: str, request: RelationMemoryChatRequest):
        service = app.state.relation_memory_session_service

        def stream_events():
            events: queue.Queue[dict[str, object] | None] = queue.Queue()

            def trace_sink(event: dict[str, object]) -> None:
                events.put({"type": "trace", "trace": jsonable_encoder(event)})

            def run() -> None:
                try:
                    payload = service.handle_chat_message(
                        session_id,
                        request.message,
                        trace_sink=trace_sink,
                    )
                    events.put({"type": "final", "payload": jsonable_encoder(payload)})
                except KeyError as exc:
                    events.put({"type": "error", "status": 404, "message": str(exc)})
                except Exception as exc:  # pragma: no cover - defensive stream boundary
                    events.put({"type": "error", "status": 500, "message": str(exc)})
                finally:
                    events.put(None)

            threading.Thread(target=run, daemon=True).start()
            while True:
                item = events.get()
                if item is None:
                    break
                yield json.dumps(item, ensure_ascii=False) + "\n"

        return StreamingResponse(stream_events(), media_type="application/x-ndjson")

    @app.post("/relation-memory/sessions/{session_id}/questions", response_model=GraphQuestionResponse)
    async def ask_relation_memory_graph_question(session_id: str, request: GraphQuestionRequest):
        try:
            return app.state.relation_memory_session_service.answer_graph_question(
                session_id,
                request.question,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/relation-memory/sessions/{session_id}/metric-candidates", response_model=MetricCandidatesResponse)
    async def list_relation_memory_metric_candidates(session_id: str):
        try:
            return MetricCandidatesResponse(
                session_id=session_id,
                metric_candidates=app.state.relation_memory_session_service.list_metric_candidates(session_id),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/relation-memory/sessions/{session_id}/metric-candidates/{candidate_id}/confirmations",
        response_model=MetricCandidateConfirmationResponse,
    )
    async def confirm_relation_memory_metric_candidate(
        session_id: str,
        candidate_id: str,
        request: ConfirmationRequest,
    ):
        try:
            return app.state.relation_memory_session_service.confirm_metric_candidate(
                session_id,
                candidate_id,
                request.decision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/relation-memory/sessions/{session_id}/confirmations/{candidate_id}",
        response_model=ConfirmationResponse,
    )
    async def confirm_relation_memory_candidate(
        session_id: str,
        candidate_id: str,
        request: ConfirmationRequest,
    ):
        try:
            return app.state.relation_memory_session_service.confirm_candidate(
                session_id,
                candidate_id,
                request.decision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/relation-memory/memory/relations", response_model=MemoryRelationsResponse)
    async def list_relation_memory_relations():
        return MemoryRelationsResponse(relations=app.state.relation_memory_session_service.list_memory_relations())

    _install_swagger_file_upload_schema(app)
    return app


def _install_swagger_file_upload_schema(app: FastAPI) -> None:
    """Patch file upload schema so Swagger UI renders a file picker for arrays."""

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
        )
        request_schema = (
            openapi_schema.get("paths", {})
            .get("/relation-memory/sessions", {})
            .get("post", {})
            .get("requestBody", {})
            .get("content", {})
            .get("multipart/form-data", {})
            .get("schema", {})
        )
        schema_ref = request_schema.get("$ref", "").rsplit("/", 1)[-1]
        body_schema = openapi_schema.get("components", {}).get("schemas", {}).get(schema_ref)
        if body_schema:
            body_schema.setdefault("properties", {})["files"] = {
                "title": "Files",
                "type": "array",
                "items": {"type": "string", "format": "binary"},
                "description": "Upload one or more .xlsx, .pdf, or .txt files.",
            }
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi


app = create_app()
