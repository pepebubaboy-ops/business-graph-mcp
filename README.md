# Business Graph MCP

Универсальный backend-core для анализа бизнес-взаимосвязей в файлах
с MCP/OpenAPI-адаптерами.

Цель проекта: принимать файлы и описания процессов, строить evidence-backed
business graph, отвечать на вопросы руководства и предлагать пайплайны
автоматизаций без привязки к одному UI или LLM-клиенту.

## Основная идея

```text
Claude / Claude Cowork / Open WebUI / свой UI
        ↓
MCP или OpenAPI adapter
        ↓
Policy Gateway
        ↓
Business Graph Core
        ↓
File Intelligence + Relation Mining + Graph QA + Pipeline Planner
        ↓
Neo4j + Postgres + Object Storage
```

MCP — это не ядро продукта. MCP — внешний адаптер.
Ядро должно быть переиспользуемым для Claude, Open WebUI
и собственного интерфейса.

## Быстрый старт локально

```bash
python -m venv .venv
source .venv/bin/activate
make install
make test
make run
```

API:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/openapi.json
```

File-first analysis flow:

```bash
export API_KEY=dev-api-key-change-me

curl -X POST "http://localhost:8000/api/v1/files?workspace_id=default" \
  -H "X-API-Key: ${API_KEY}" \
  -F "file=@examples/sample-data/dependency_rules.xlsx"

curl -X POST http://localhost:8000/api/v1/analyses/files \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"default","file_ids":["file:replace-with-uploaded-id"]}'

curl "http://localhost:8000/api/v1/graph/summary?workspace_id=default" \
  -H "X-API-Key: ${API_KEY}"

curl "http://localhost:8000/api/v1/relations?workspace_id=default" \
  -H "X-API-Key: ${API_KEY}"
```

Graph query flow:

```bash
curl -X POST http://localhost:8000/api/v1/relations/search \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"default","query":"gross margin"}'

curl -X POST http://localhost:8000/api/v1/paths/explain \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"default","from_id":"metric:volume","to_id":"metric:gross_margin"}'
```

MCP stdio server для локального теста:

```bash
make mcp
```

Инфраструктура:

```bash
cp .env.example .env
make docker-up
```

## Проверки

```bash
make check
```

`make check` runs the same baseline checks as CI:

```bash
python -m pytest
python -m ruff check .
python scripts/check_long_lines.py
```

GitHub Actions runs these checks on every pull request and on pushes to
`master` with Python 3.11 and 3.12. Unit tests do not require Docker,
Neo4j, Postgres, Redis, MinIO, Claude, Open WebUI, or network access.

## Репозиторий

Публичный репозиторий уже создан:

- <https://github.com/pepebubaboy-ops/business-graph-mcp>

```bash
git clone git@github.com:pepebubaboy-ops/business-graph-mcp.git
cd business-graph-mcp
```

## Документы

- `docs/PLAN.md` — план разработки по этапам.
- `docs/ARCHITECTURE.md` — целевая архитектура.
- `docs/CODEX_PROMPTS.md` — последовательность промптов для Codex.
- `docs/REVIEW_LOOP.md` — как ревьюить результат после каждого промпта.
- `docs/FILE_REGISTRY.md` — file-id production path и local-path dev flow.
- `docs/GRAPH_QUERIES.md` — relation search, explanations and path queries.
- `docs/CI_AND_SMOKE_TESTS.md` — CI baseline и adapter smoke tests.
- `legacy/relation-memory-cowork/` — текущий MCPB/Relation Memory пакет
  как источник для миграции.

## MVP tools

```text
business.analyze_files
business.get_graph_summary
business.find_relations
business.explain_relation
business.explain_path
business.find_bottlenecks
business.find_data_gaps
business.ask_graph
business.generate_executive_brief
relations.list_candidates
relations.approve_candidate
relations.reject_candidate
pipeline.propose
pipeline.simulate
```
