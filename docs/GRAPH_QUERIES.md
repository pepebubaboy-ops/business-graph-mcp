# Graph Queries

## Relationship Search

The graph query layer provides a read-only service for asking questions over
the relations already extracted into a workspace graph.

Use `POST /api/v1/relations/search` with `RelationSearchRequest` to search by:

- node id
- node name
- node aliases
- relation source or target id
- relation type
- relation status

If no statuses are provided, the search returns `confirmed` and `candidate`
relations. Rejected relations are excluded by default because they should not be
used as business facts unless a reviewer explicitly asks for them with
`include_rejected=true`.

Example:

```json
{
  "workspace_id": "default",
  "query": "gross margin",
  "limit": 50
}
```

This supports CEO-style questions such as:

- What is connected to revenue?
- What affects gross margin?
- List confirmed relations for this workspace.
- Find candidate relations that need review.

## Direct Relation Explanation

Use `GET /api/v1/relations/{relation_id}/explain` to retrieve:

- the relation
- the source node
- the target node
- evidence references
- the deterministic explanation text stored on the relation

This endpoint returns `404` when the relation does not exist in the requested
workspace.

## Path Explanation

Use `POST /api/v1/paths/explain` with `PathSearchRequest` to find a path between
two node ids.

Example:

```json
{
  "workspace_id": "default",
  "from_id": "metric:volume",
  "to_id": "metric:gross_margin",
  "max_depth": 4
}
```

The current implementation uses deterministic breadth-first search over the
in-memory graph. It respects `max_depth`, never crosses workspace boundaries,
and prefers confirmed relations before candidate relations when expanding
neighbors. Rejected relations are excluded unless explicitly requested.

Path confidence is a simple deterministic aggregate: the minimum confidence
across the relations in the returned path.

## API and MCP

The FastAPI adapter and MCP adapter both call
`business_graph_core.services.graph_query.GraphQueryService`. Query logic stays
inside `business_graph_core`; adapters only translate request and response
shapes.

MCP tools:

- `business_find_relations`
- `business_explain_relation`
- `business_explain_path`

These tools do not require Claude, Open WebUI, Docker, Neo4j, Postgres, Redis,
MinIO, or network access in unit tests.
