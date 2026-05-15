# CI and Smoke Tests

## CI Checks

GitHub Actions runs on:

- every pull request
- every push to `master`

The workflow uses Python 3.11 and 3.12. Each matrix job installs the project
with development dependencies and runs:

```bash
python -m pytest
python -m ruff check .
python scripts/check_long_lines.py
```

These commands are also available locally through:

```bash
make check
```

## MCP Smoke Test

`tests/test_mcp_adapter_smoke.py` verifies the lowest stable MCP adapter
surface:

- `business_graph_mcp.server` can be imported.
- The module exposes the expected public tool functions.
- `business_healthcheck()` returns the expected local health payload.
- The default adapter service uses `InMemoryGraphRepository`.
- The FastMCP instance has the expected tool names registered.

The smoke test does not call `mcp.run()` and does not start a stdio server.

## Intentionally Not Tested Yet

The CI baseline intentionally does not require:

- Claude, Open WebUI, or MCP Inspector
- Docker
- Neo4j
- Postgres
- Redis
- MinIO
- network access

Integration tests for these systems should be added later behind explicit
markers or a separate CI service matrix. Unit tests must stay runnable on a
plain Python checkout.
