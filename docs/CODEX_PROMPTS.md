# Codex prompts

Общий принцип: один промпт — один маленький PR.
Не давайте Codex сразу весь проект.

Codex умеет читать, редактировать и запускать код в своём окружении.
Используйте его как PR-агента, а не как генератор одного огромного файла.

## Перед первым промптом

1. Создайте публичный GitHub repo.
2. Загрузите этот scaffold.
3. Подключите repo к Codex.
4. Запускайте промпты по порядку.

## Общий guardrail для каждого промпта

Добавляйте в конец каждого задания:

```text
Constraints:
- Keep the PR small and focused.
- Do not rewrite unrelated files.
- Do not remove tests unless replacing them with better tests.
- Do not add secrets or credentials.
- Keep business logic in business_graph_core, not in the MCP adapter.
- MCP and API must call the same service layer.
- Confirmed relations must always include evidence references.
- LLM-inferred relations must be candidates by default.
- Run tests and include the command output in the PR summary.
```

---

## PR-001 — Audit current repo and create implementation map

```text
You are working in the business-graph-mcp repository.

Goal: audit the current scaffold and the legacy relation-memory-cowork
package, then create an implementation map for the migration.

Tasks:
1. Read README.md, docs/ARCHITECTURE.md, docs/PLAN.md, and legacy/relation-memory-cowork.
2. Create docs/MIGRATION_MAP.md.
3. In MIGRATION_MAP.md, map legacy modules to target modules:
   - mcp_server.py → MCP adapter
   - relation_memory_engine.py → relation mining service
   - relation_memory_neo4j.py → graph repository
   - ingestion/session/question modules → analyzer/session/QA services
   - dependency_rules.xlsx behavior → explicit rules extractor
4. Identify dead/demo-only code that should not be migrated.
5. Identify risks in the legacy implementation: workspace_root/raw paths,
   local-only assumptions, missing file registry, evidence gaps,
   API/MCP duplication.
6. Do not change runtime code in this PR.
7. Run tests.

Output: docs/MIGRATION_MAP.md only, plus any minimal test/format fixes if needed.
```

---

## PR-002 — Domain models

```text
Goal: implement the core domain models for the business graph.

Tasks:
1. Add/complete src/business_graph_core/models.py with Pydantic v2 models:
   - NodeType enum
   - RelationType enum
   - RelationStatus enum
   - EvidenceQuality enum
   - EvidenceRef
   - BusinessNode
   - BusinessRelation
   - RelationCandidate
   - AnalysisRequest
   - AnalysisResult
   - GraphSummary
   - PipelineStep
   - PipelineProposal
2. Include fields for workspace_id, source refs, confidence, strength, polarity, created_at.
3. Ensure confirmed relations require at least one evidence_ref.
4. Add tests in tests/test_models.py.
5. Run tests.
```

---

## PR-003 — Parser abstraction and Excel parser

```text
Goal: create file parser interfaces and an Excel parser that extracts sheets,
cells, formulas, tables and dependency_rules rows.

Tasks:
1. Add src/business_graph_core/parsers/base.py with ParsedFile, ParsedSheet, ParsedCell, ParsedTable models.
2. Add src/business_graph_core/parsers/excel.py using openpyxl.
3. Parse .xlsx files without executing macros.
4. Extract formulas as raw strings.
5. Detect a dependency_rules sheet or table with columns:
   - source_metric_code
   - target_metric_code
   - edge_type
   - reason
   - strength
6. Add tests using examples/sample-data/dependency_rules.xlsx.
7. Run tests.

Security:
- Do not accept arbitrary absolute paths in production-facing code.
- Parser function can accept Path internally for tests/service usage.
```

---

## PR-004 — Explicit rules relation extractor

```text
Goal: turn dependency_rules rows into confirmed BusinessRelation objects with evidence.

Tasks:
1. Add src/business_graph_core/extractors/explicit_rules.py.
2. Map legacy edge types to RelationType:
   - driver → DRIVES
   - inverse_driver → INVERSELY_DRIVES
   - component → COMPONENT_OF
   - dependency → DEPENDS_ON
3. Create Metric nodes for source and target metrics.
4. Create evidence refs with sheet name, row number, file id/name, and reason.
5. Mark explicit rules as confirmed.
6. Add tests that dependency_rules.xlsx yields confirmed relations with evidence_refs.
7. Run tests.
```

---

## PR-005 — In-memory graph repository

```text
Goal: add a graph repository interface and an in-memory implementation for tests and early API use.

Tasks:
1. Add src/business_graph_core/graph/repository.py with protocol/interface methods:
   - upsert_node
   - upsert_relation
   - list_relations
   - get_summary
   - find_paths
   - list_candidates
   - approve_candidate
   - reject_candidate
2. Add src/business_graph_core/graph/memory_repo.py.
3. Ensure idempotent upserts.
4. Add tests for summary and candidate lifecycle.
5. Run tests.
```

---

## PR-006 — Analyzer service MVP

```text
Goal: build the first service that analyzes a package of files and saves extracted relations into the graph repository.

Tasks:
1. Add src/business_graph_core/services/analyzer.py.
2. Analyzer input should be workspace_id + file paths for local MVP,
   but structure code so production can switch to file_ids.
3. For each xlsx file:
   - parse with ExcelParser
   - extract explicit rules
   - save nodes/relations
4. Return AnalysisResult with counts and graph summary.
5. Add tests with in-memory repo and sample xlsx.
6. Run tests.
```

---

## PR-007 — FastAPI MVP

```text
Goal: expose the analyzer through FastAPI without duplicating business logic.

Tasks:
1. Implement src/business_graph_api/main.py.
2. Add endpoints:
   - GET /health
   - POST /api/v1/analyses/local-files
   - GET /api/v1/graph/summary
   - GET /api/v1/relations
3. Use AnalyzerService and InMemoryGraphRepository for MVP.
4. Add API key dependency for non-health endpoints. Use API_KEY from settings.
5. Keep /openapi.json available.
6. Add tests using FastAPI TestClient.
7. Run tests.
```

---

## PR-008 — MCP stdio adapter MVP

```text
Goal: add a local stdio MCP adapter that calls the same AnalyzerService as the API.

Tasks:
1. Implement src/business_graph_mcp/server.py using FastMCP.
2. Add tools:
   - business_analyze_files
   - business_get_graph_summary
   - business_find_relations
   - business_healthcheck
3. Do not duplicate analysis logic in the MCP layer.
4. Tool names should be stable and documented.
5. Add a small docs/MCP_LOCAL.md with local run instructions.
6. Add smoke tests if possible; otherwise document manual MCP Inspector test.
7. Run tests.
```

---

## PR-009 — Neo4j repository

```text
Goal: add Neo4j persistence for the business graph.

Tasks:
1. Add src/business_graph_core/graph/neo4j_repo.py.
2. Add schema constraints/indexes for BusinessNode and BusinessRelation identity.
3. Implement upsert_node, upsert_relation, list_relations, get_summary.
4. Store evidence refs as structured JSON or separate Evidence nodes.
   Prefer separate Evidence nodes if feasible in this PR; otherwise document
   the tradeoff.
5. Add integration test markers, but do not make CI require Neo4j unless available.
6. Add docs/NEO4J.md with setup and example Cypher.
7. Run tests.
```

---

## PR-010 — Candidate relation workflow

```text
Goal: support candidate relations, approval and rejection.

Tasks:
1. Add RelationCandidate lifecycle to repository and service layer.
2. Add API endpoints:
   - GET /api/v1/relations/candidates
   - POST /api/v1/relations/candidates/{candidate_id}/approve
   - POST /api/v1/relations/candidates/{candidate_id}/reject
3. Add MCP tools:
   - relations_list_candidates
   - relations_approve_candidate
   - relations_reject_candidate
4. Ensure approving a candidate creates a confirmed relation with evidence or requires explicit approval evidence.
5. Add tests.
6. Run tests.
```

---

## PR-011 — Business process extraction MVP

```text
Goal: extend beyond metric relations and extract basic process/role/system links
from text files.

Tasks:
1. Add parser for .txt and .md files.
2. Add extractor for simple deterministic patterns:
   - "process X depends on Y"
   - "role X owns process Y"
   - "department X uses system Y"
   - Russian equivalents: "процесс X зависит от Y", "роль X отвечает за Y",
     "отдел X использует Y"
3. Extract these as candidate relations unless the sentence is explicit enough
   and has a direct quote evidence.
4. Add tests in Russian and English.
5. Run tests.
```

---

## PR-012 — Bottleneck and data gap detector

```text
Goal: detect operational risks and bottlenecks from the graph.

Tasks:
1. Add src/business_graph_core/services/diagnostics.py.
2. Detect:
   - process without owner
   - manual handoff
   - duplicate data source candidates
   - single-person dependency
   - missing evidence for important relations
3. Add API endpoint /api/v1/diagnostics/bottlenecks.
4. Add MCP tool business_find_bottlenecks.
5. Add tests.
6. Run tests.
```

---

## PR-013 — Executive brief generator

```text
Goal: generate a CEO-ready markdown brief from an AnalysisResult and graph summary.

Tasks:
1. Add src/business_graph_core/services/briefs.py.
2. Output sections:
   - Summary
   - Confirmed facts
   - Candidate assumptions
   - Bottlenecks
   - Risks
   - Automation opportunities
   - Recommended next steps
3. Include evidence references for facts.
4. Add API endpoint /api/v1/briefs/executive.
5. Add MCP tool business_generate_executive_brief.
6. Add tests with snapshot-like assertions.
7. Run tests.
```

---

## PR-014 — Pipeline proposal MVP

```text
Goal: propose automation pipelines based on detected relations and bottlenecks. Do not execute pipelines.

Tasks:
1. Add src/business_graph_core/services/pipeline_planner.py.
2. Add rules that propose pipelines for:
   - recurring report generation
   - manual data reconciliation
   - multi-file metric comparison
   - missing owner/process documentation
3. Return PipelineProposal with steps, inputs, outputs, risk_level, approvals_required.
4. Add API endpoint /api/v1/pipelines/propose.
5. Add MCP tool pipeline_propose.
6. Add tests.
7. Run tests.
```

---

## PR-015 — File registry MVP

```text
Goal: introduce file_id-based workflow instead of raw local paths.

Tasks:
1. Add FileRecord model and FileRegistry service.
2. Add local filesystem storage under data/raw for MVP.
3. Add API endpoints:
   - POST /api/v1/files
   - GET /api/v1/files/{file_id}
4. Add analysis endpoint that accepts file_ids.
5. Keep local-files endpoint as dev-only and clearly mark it deprecated.
6. Add tests for file upload, size limit, extension allowlist.
7. Run tests.
```

---

## PR-016 — Remote HTTP MCP / FastAPI-MCP exploration

```text
Goal: expose selected FastAPI operations as remote HTTP MCP tools.

Tasks:
1. Evaluate whether fastapi-mcp works cleanly with the existing FastAPI app.
2. If yes, mount /mcp and expose only selected operations as tools.
3. If no, create docs/REMOTE_MCP_DECISION.md explaining blockers and the fallback plan.
4. Ensure auth is not bypassed by MCP route.
5. Add docs/OPEN_WEBUI.md with native MCP and OpenAPI connection options.
6. Run tests.
```

---

## PR-017 — Security baseline

```text
Goal: harden the MVP.

Tasks:
1. Add request ID and structured audit events.
2. Add workspace_id isolation in services and repositories.
3. Validate file extensions and MIME guesses.
4. Add max file size checks.
5. Add tests for unauthorized API calls.
6. Ensure secrets are not logged.
7. Add docs/SECURITY.md.
8. Run tests.
```

---

## PR-018 — Package local MCPB adapter

```text
Goal: package a local MCPB adapter for Claude Desktop/local dev, based on the new core.

Tasks:
1. Create packaging/mcpb/manifest.json.
2. Entry point must use src/business_graph_mcp/server.py.
3. Keep user_config minimal:
   - workspace_root for local dev only
   - max_file_size_mb
   - neo4j uri/user/password optional
   - llm config optional
4. Add script scripts/build_mcpb.sh.
5. Add docs/MCPB.md.
6. Do not include secrets or large runtime data.
7. Run tests.
```
