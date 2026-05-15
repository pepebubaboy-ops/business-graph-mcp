# Business Graph MCP skill

Use this skill when modifying the Business Graph MCP repository.

## Workflow

1. Read the relevant prompt in `codex/prompts/`.
2. Inspect existing code before editing.
3. Make a focused change.
4. Add or update tests.
5. Run tests.
6. Summarize changed files, tests run and risks.

## Architecture rules

- API and MCP adapters must call the same service layer.
- Business logic belongs in `business_graph_core`.
- Confirmed relations require evidence.
- Candidate relations can exist without final approval but must include the reason and source if available.
- Never add credentials, tokens or secrets.
