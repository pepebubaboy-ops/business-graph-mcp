# Project instructions for Codex

You are contributing to Business Graph MCP.

Core rule: do not put business logic in MCP or API adapters. Adapters call services. Services call parsers, extractors and repositories.

## Non-negotiables

- Confirmed relations must have evidence refs.
- LLM-inferred relations are candidates by default.
- The production API should prefer workspace_id + file_id over raw paths.
- No arbitrary code execution.
- No secrets in code, tests or docs.
- Keep PRs small.
- Add tests for changed behavior.
- Run tests before finalizing a PR.

## Target module boundaries

```text
business_graph_core.models         domain models
business_graph_core.parsers        file parsing
business_graph_core.extractors     relation extraction
business_graph_core.graph          repositories
business_graph_core.services       use cases
business_graph_api                 FastAPI adapter
business_graph_mcp                 MCP adapter
```
