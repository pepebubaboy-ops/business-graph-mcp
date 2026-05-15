# Architecture

## Target architecture

```mermaid
flowchart LR
    subgraph Clients["Clients / UI"]
        C1["Claude / Claude Cowork"]
        C2["Open WebUI"]
        C3["Custom corporate UI"]
        C4["Local dev / Claude Desktop"]
    end

    subgraph Adapters["Adapters"]
        MCP["/mcp\nRemote MCP HTTP"]
        API["/api + /openapi.json\nREST/OpenAPI"]
        STDIO["stdio / MCPB\nlocal mode"]
    end

    subgraph Gateway["Gateway layer"]
        AUTH["Auth / tenant routing"]
        POLICY["Tool policy\nreadonly / approvals / allowlist"]
        AUDIT["Audit log"]
    end

    subgraph Core["Business Intelligence Core"]
        FILES["File Intelligence\nparse / normalize / classify"]
        REL["Relation Mining Engine"]
        GRAPH["Business Graph Engine"]
        QA["Graph QA + Executive Briefs"]
        PIPE["Pipeline Planner"]
        ART["Artifact Generator"]
    end

    subgraph Storage["Storage"]
        OBJ[("Object storage\nraw files + artifacts")]
        PG[("Postgres\nsessions + jobs + metadata")]
        NEO[("Neo4j\nbusiness graph")]
        VEC[("Vector store\noptional")]
        LOG[("Audit / events")]
    end

    C1 --> MCP
    C2 --> MCP
    C2 --> API
    C3 --> API
    C4 --> STDIO

    MCP --> AUTH
    API --> AUTH
    STDIO --> AUTH

    AUTH --> POLICY
    POLICY --> AUDIT
    AUDIT --> FILES
    AUDIT --> REL
    AUDIT --> GRAPH
    AUDIT --> QA
    AUDIT --> PIPE
    AUDIT --> ART

    FILES --> OBJ
    FILES --> PG
    REL --> GRAPH
    GRAPH --> NEO
    QA --> VEC
    ART --> OBJ
    AUDIT --> LOG
```

## Design principles

1. **Core first, MCP second** — бизнес-логика не должна зависеть от Claude/Cowork/Open WebUI.
2. **Evidence-first** — каждая подтверждённая связь должна иметь источник: файл, лист, ячейка, цитата, формула или правило.
3. **Candidate workflow** — LLM-гипотезы не становятся фактами без подтверждения.
4. **Read-only by default** — MVP анализирует и проектирует, но не меняет внешние системы.
5. **File IDs over raw paths** — production API должен работать с `file_id`, не с произвольными путями.
6. **Small MCP toolset** — лучше 8–12 крупных инструментов, чем 50 мелких.

## Migration from legacy package

Current legacy package is stored in:

```text
legacy/relation-memory-cowork/
```

Use it as a reference for:

- existing FastMCP tools;
- relation memory engine;
- Neo4j persistence;
- Excel dependency rules;
- candidate approval flow.

Do not keep the final product named `relation-memory-cowork`. The target name is `business-graph-mcp`.
