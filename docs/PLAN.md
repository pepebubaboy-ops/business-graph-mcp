# План создания Business Graph MCP

## Phase 0 — Репозиторий и правила разработки

Результат:

- публичный GitHub repo;
- README;
- pyproject;
- Docker Compose;
- базовые тесты;
- Codex prompts;
- legacy package сохранён для миграции.

Критерий готовности:

```bash
make install
make test
make run
curl http://localhost:8000/health
```

## Phase 1 — Core domain model

Создать модели:

- `BusinessNode`;
- `BusinessRelation`;
- `EvidenceRef`;
- `AnalysisSession`;
- `GraphSnapshot`;
- `PipelineProposal`.

Критерий готовности:

- типы покрывают Metric, Process, Role, Department, System, Document, Dataset, Risk, Bottleneck, Automation, Decision;
- relation status: `confirmed`, `candidate`, `rejected`;
- tests validate serialization/deserialization.

## Phase 2 — File Intelligence MVP

Добавить парсеры:

- Excel через `openpyxl`;
- простые PDF/TXT/MD;
- optional Docling adapter later.

Критерий готовности:

- dependency_rules.xlsx извлекается в structured parsed file;
- формулы Excel сохраняются как evidence;
- нет raw path traversal.

## Phase 3 — Relation Mining Engine

Извлекать связи из:

- explicit dependency rules;
- Excel formulas;
- текстовых маркеров;
- legacy relation-memory engine.

Критерий готовности:

- explicit rules auto-confirmed;
- LLM/text inferred links are candidates;
- every relation has evidence_refs.

## Phase 4 — Graph persistence

Добавить Neo4j repository:

- constraints/indexes;
- upsert nodes;
- upsert relations;
- graph summary;
- path explanation.

Критерий готовности:

- tests with testcontainers or integration smoke;
- graceful fallback to in-memory repo for local tests.

## Phase 5 — API layer

Добавить FastAPI endpoints:

```text
GET  /health
POST /api/v1/workspaces
POST /api/v1/files
POST /api/v1/analyses
GET  /api/v1/analyses/{analysis_id}
GET  /api/v1/graph/summary
POST /api/v1/graph/questions
POST /api/v1/pipelines/propose
GET  /openapi.json
```

Критерий готовности:

- OpenAPI schema valid;
- upload limit enforced;
- API key auth for non-health endpoints.

## Phase 6 — MCP adapter

Добавить MCP tools:

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

Критерий готовности:

- MCP Inspector sees tools;
- tools call the same service layer as API;
- no duplicate logic in MCP server.

## Phase 7 — Executive Briefs and Artifacts

Добавить генерацию:

- `executive_brief.md`;
- `relations_review.xlsx`;
- `business_graph.json`;
- `pipeline_proposal.yaml`.

Критерий готовности:

- artifacts accessible by `artifact_id`;
- brief separates facts, assumptions, risks and next steps.

## Phase 8 — Pipeline Planner MVP

Не выполнять автоматизации. Только проектировать.

Результат:

- pipeline proposal;
- dry-run simulation;
- risk level;
- required approvals;
- required data access.

## Phase 9 — Open WebUI / Claude integration

Проверить:

- native HTTP MCP;
- OpenAPI tool server;
- local stdio MCP adapter;
- public gateway constraints.

## Phase 10 — Security baseline

Добавить:

- API key/JWT;
- workspace isolation;
- file type validation;
- max file size;
- audit log;
- readonly policy;
- no arbitrary code execution.
