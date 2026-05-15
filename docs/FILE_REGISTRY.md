# File Registry

## Production Path

Production-facing clients should use a file-first workflow:

1. Upload or register a file in a workspace.
2. Receive a stable `file_id`.
3. Analyze with `workspace_id + file_ids`.
4. Inspect graph summary and relations through API or MCP adapters.

The main API path is:

```text
POST /api/v1/files
GET  /api/v1/files
POST /api/v1/analyses/files
GET  /api/v1/graph/summary
GET  /api/v1/relations
```

## Why File IDs

`file_ids` are the production path because remote clients should not be asked
to send arbitrary filesystem paths. A `file_id` gives the backend a stable,
workspace-scoped handle that can later point to local disk, object storage,
or another storage implementation without changing adapter contracts.

This is required for:

- Claude Cowork
- Open WebUI
- future remote MCP deployments
- future corporate UI workflows

These clients can upload files and pass identifiers back to tools without
knowing where bytes are stored on the server.

## Local Paths

`AnalysisRequest.local_paths` and `/api/v1/analyses/local-files` remain for
local development and smoke testing. They are not the intended production path.

Local paths are useful when a developer runs the API and sample files on the
same machine. They are unsafe as a general remote API contract because a client
could otherwise ask the server to read arbitrary paths.

## Storage Layout

The local storage implementation writes uploaded files under:

```text
.data/files/{workspace_id}/{file_id}/{original_filename}
```

The storage layer sanitizes filenames and does not trust client-provided paths
for the destination. The sanitized filename is kept only as display metadata.

## Workspace Isolation

The in-memory registry stores records by workspace. A file registered in one
workspace cannot be resolved from another workspace. Analysis by `file_ids`
must resolve every file through the registry before parsing.

## Current Limits

The registry and storage implementations are intentionally local and in-memory
for the MVP baseline. They do not require Neo4j, Postgres, Redis, MinIO,
Docker, or network services in unit tests.
