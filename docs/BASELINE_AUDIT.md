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
  - domain enums for node, relation, relation status, and evidence quality
  - Pydantic models for evidence, nodes, relations, candidates, analysis results,
    graph summaries, and pipeline proposals
  - validation that confirmed relations require at least one evidence reference
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
- `business_graph_core.services.analyzer`
  - local-file analyzer MVP for Excel dependency rules
- `business_graph_api.main`
  - `GET /health`
  - `POST /api/v1/analyses/local-files`
  - `GET /api/v1/graph/summary`
  - `GET /api/v1/relations`
  - API key guard for non-health endpoints
- `business_graph_mcp.server`
  - local FastMCP stdio server
  - health, analyze files, graph summary, and relation listing tools

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

- `python -m pytest`: passed, 7 tests collected.
- `python -m ruff check .`: passed.

Ruff is configured to lint the active scaffold and tests while excluding
`legacy/relation-memory-cowork/`. The legacy package was formatted for
readability, but it is not lint-enforced in this PR because it has not been
migrated into the active architecture yet.

## Immediate Risks Found

- The active API still accepts local filesystem paths for the MVP analyzer.
  This is acceptable for local development, but production-facing file handling
  should move to workspace-scoped file IDs and a file registry.
- The graph repository is in-memory only. State is process-local and shared
  through module-level service instances in the current API/MCP adapters.
- The MCP adapter has no dedicated smoke test yet; current tests focus on
  core extraction and FastAPI behavior.
- Legacy code still contains local-only assumptions, direct path usage,
  old `app.*` imports, and external service assumptions. It should remain
  reference-only until a focused migration PR.
- Docker Compose defines Neo4j, Postgres, Redis, and MinIO, but unit tests do not
  require those services. Integration tests should stay optional unless a CI
  service matrix is added.
- There is no CI workflow in the scaffold yet, so the baseline checks are local
  rather than enforced by GitHub.

## Recommended Next PR

Create a focused CI and adapter-smoke baseline PR:

1. Add a GitHub Actions workflow that runs `python -m pytest` and
   `python -m ruff check .` on Python 3.11 and 3.12.
2. Add a minimal MCP adapter smoke test or documented manual MCP Inspector check.
3. Keep Neo4j, Postgres, Redis, and MinIO out of required unit tests.
4. Do not migrate legacy modules in that PR.
