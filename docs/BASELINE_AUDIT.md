# Baseline Audit

## Repository Structure Summary

The repository is organized around the target Business Graph MCP architecture:

- `src/business_graph_core/` contains reusable business graph domain models,
  parsers, extractors, graph repositories, settings, and services.
- `src/business_graph_api/` contains the FastAPI adapter.
- `src/business_graph_mcp/` contains the local MCP stdio adapter.
- `tests/` contains unit and adapter tests for the active scaffold.
- `docs/` contains architecture, implementation plan, review loop,
  Codex prompt sequencing, and this baseline audit.
- `codex/` contains project instructions, PR prompts, and a local Codex skill.
- `examples/sample-data/` contains the sample dependency rules workbook.
- `infra/` contains Neo4j constraint/index bootstrap Cypher.
- `legacy/relation-memory-cowork/` preserves the existing Relation Memory package
  as migration reference material.

## Current Implemented Modules

Active scaffold modules:

- `business_graph_core.models`
  - domain enums for node, relation, relation status, file status,
    and evidence quality
  - Pydantic models for evidence, nodes, relations, candidates, analysis results,
    graph summaries, file records, file upload results, and pipeline proposals
  - validation that confirmed relations require at least one evidence reference
- `business_graph_core.files.registry`
  - workspace-scoped file registry protocol
  - in-memory registry implementation for tests and local MVP runs
- `business_graph_core.files.storage`
  - local file storage under `.data/files/{workspace_id}/{file_id}/`
  - SHA-256 computation and filename sanitization
- `business_graph_core.parsers.base`
  - parser protocol and parsed file/sheet/cell models
- `business_graph_core.parsers.excel`
  - `openpyxl`-based workbook parser
  - formula preservation
  - dependency rule row extraction
- `business_graph_core.extractors.explicit_rules`
  - deterministic dependency rule to confirmed relation extraction
  - metric node creation
  - evidence references from sheet, row, file, and reason
- `business_graph_core.graph.memory_repo`
  - in-memory node/relation upsert and graph summary support
  - workspace-scoped node and relation lookup
- `business_graph_core.graph.repository`
  - graph repository protocol shared by services
- `business_graph_core.services.analyzer`
  - registered-file analyzer path using `workspace_id + file_ids`
  - local-file analyzer helper for dev/backward compatibility
- `business_graph_core.services.graph_query`
  - relation search by node, relation, type, and status
  - direct relation explanation and deterministic path explanation
- `business_graph_api.main`
  - `GET /health`
  - `POST /api/v1/files`
  - `GET /api/v1/files`
  - `POST /api/v1/analyses/files`
  - `POST /api/v1/analyses/local-files`
  - `POST /api/v1/relations/search`
  - `GET /api/v1/relations/{relation_id}/explain`
  - `POST /api/v1/paths/explain`
  - `GET /api/v1/graph/summary`
  - `GET /api/v1/relations`
  - API key guard for non-health endpoints
- `business_graph_mcp.server`
  - local FastMCP stdio server
  - health, registered-file analysis, local-file analysis, graph summary,
    relation search, relation explanation, and path explanation tools

Preserved legacy modules:

- `legacy/relation-memory-cowork/backend/app/`
  contains the previous FastAPI app, relation memory services, workbook parsing,
  candidate workflows, language helpers, and Neo4j persistence code.
- `legacy/relation-memory-cowork/backend/mcp_server.py`
  contains the previous local MCP entrypoint.
- `legacy/relation-memory-cowork/backend/data/`
  contains relation memory domain packs and optional business literature storage.

## Current Test Status

Verification was run in a local `.venv` created from Python 3.12.12.

- `python -m pytest`: passed, 15 tests collected.
- `python -m ruff check .`: passed.
- `python scripts/check_long_lines.py`: passed.
- `python scripts/check_changed_files_text_hygiene.py`: passed.

Ruff is configured to lint the active scaffold and tests while excluding
`legacy/relation-memory-cowork/`. The legacy package was formatted for
readability, but it is not lint-enforced in this PR because it has not been
migrated into the active architecture yet.

The text hygiene check scans tracked active repository files and flags lines
over 220 characters, raw carriage return bytes, byte order marks, Unicode line
separators, Unicode bidi controls, zero-width characters, non-breaking spaces,
soft hyphens, dangerous Unicode escape literals, unexpected control characters,
and any Unicode format characters. The changed-files hygiene check applies the
same rules to the active PR diff. These checks exclude `.git/`, `.venv/`,
`legacy/relation-memory-cowork/`, binary files, and packaged binary artifacts.

## Immediate Risks Found

- The active API still keeps a local filesystem path analyzer for development
  and backward compatibility. Production-facing clients should prefer the
  workspace-scoped file registry and `file_ids` flow.
- The graph repository is in-memory only. State is process-local and shared
  through module-level service instances in the current API/MCP adapters.
- The file registry and storage implementations are local MVP components.
  Durable persistence should be added later without changing API/MCP contracts.
- Legacy code still contains local-only assumptions, direct path usage,
  old `app.*` imports, and external service assumptions. It should remain
  reference-only until a focused migration PR.
- Docker Compose defines Neo4j, Postgres, Redis, and MinIO, but unit tests do not
  require those services. Integration tests should stay optional unless a CI
  service matrix is added.
- CI runs the baseline checks on Python 3.11 and 3.12. Integration tests for
  external services remain intentionally out of required unit tests.

## Recommended Next PR

Create a focused durable persistence design PR:

1. Define how file records will move from in-memory registry to durable metadata
   storage.
2. Keep unit tests independent of Neo4j, Postgres, Redis, MinIO, and Docker.
3. Preserve `workspace_id + file_ids` as the adapter contract.
4. Do not migrate legacy modules in that PR.
