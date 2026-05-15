from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from neo4j import GraphDatabase

from app.config import settings


class RelationMemoryNeo4jClient:
    def __init__(
        self,
        uri: str | None = None,
        username: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ):
        self.uri = uri or settings.NEO4J_URI
        self.username = username or settings.NEO4J_USERNAME
        self.password = password or settings.NEO4J_PASSWORD
        self.database = database or settings.NEO4J_DATABASE
        self.driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password))

    def close(self) -> None:
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def ensure_constraints(self) -> None:
        queries = [
            "CREATE CONSTRAINT metric_code IF NOT EXISTS FOR (n:Metric) REQUIRE n.code IS UNIQUE",
            "CREATE CONSTRAINT dataset_key IF NOT EXISTS FOR (n:Dataset) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT formula_id IF NOT EXISTS FOR (n:Formula) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT dimension_key IF NOT EXISTS FOR (n:Dimension) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT entity_value_id IF NOT EXISTS FOR (n:EntityValue) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT lag_rule_id IF NOT EXISTS FOR (n:LagRule) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT verdict_rule_code IF NOT EXISTS FOR (n:VerdictRule) REQUIRE n.code IS UNIQUE",
            "CREATE CONSTRAINT metric_mapping_id IF NOT EXISTS FOR (n:MetricMapping) REQUIRE n.id IS UNIQUE",
        ]
        with self.driver.session(database=self.database) as session:
            for query in queries:
                session.run(query).consume()

    def rebuild_graph(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.ensure_constraints()
        confirmed_relations = self.list_confirmed_relations()
        with self.driver.session(database=self.database) as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
            self._load_datasets(session, snapshot["datasets"])
            self._load_metrics(session, snapshot["metrics"])
            self._load_formulas(session, snapshot["formulas"])
            self._load_dimensions(session, snapshot["dimensions"])
            self._load_entity_values(session, snapshot["entity_values"])
            self._load_dependencies(session, snapshot["dependencies"])
            self._load_lag_rules(session, snapshot["lag_rules"])
            self._load_verdict_rules(session, snapshot["verdict_rules"])
        for relation in confirmed_relations:
            self.save_confirmed_relation(
                source_metric_code=relation["source_metric_code"],
                target_metric_code=relation["target_metric_code"],
                edge_type=relation["edge_type"],
                lag_period=relation.get("lag_period"),
                note=relation.get("note") or "",
                source_session_id=relation.get("source_session_id"),
                source_document_id=relation.get("source_document_id"),
            )
        return {
            "dataset_count": len(snapshot["datasets"]),
            "metric_count": len(snapshot["metrics"]),
            "formula_count": len(snapshot["formulas"]),
            "dimension_count": len(snapshot["dimensions"]),
            "entity_value_count": len(snapshot["entity_values"]),
            "dependency_count": len(snapshot["dependencies"]),
            "lag_rule_count": len(snapshot["lag_rules"]),
            "verdict_rule_count": len(snapshot["verdict_rules"]),
        }

    def read_catalog(self) -> dict[str, Any]:
        with self.driver.session(database=self.database) as session:
            metrics = [
                dict(record)
                for record in session.run(
                    "MATCH (m:Metric) RETURN m.code AS code, m.label AS label, "
                    "m.description AS description, coalesce(m.aliases, []) AS aliases, "
                    "m.sensitivity_level AS sensitivity_level, coalesce(m.allow_roles, []) AS allow_roles, "
                    "m.preferred_dataset AS preferred_dataset, m.semantic_type AS semantic_type ORDER BY m.code"
                )
            ]
            metric_datasets_rows = session.run(
                "MATCH (m:Metric)-[:USES_FORMULA]->(:Formula)-[:FROM_DATASET]->(d:Dataset) "
                "RETURN m.code AS metric_code, collect(d.key) AS dataset_keys"
            )
            metric_datasets = {
                record["metric_code"]: record["dataset_keys"] for record in metric_datasets_rows
            }
            entity_values = [
                dict(record)
                for record in session.run(
                    "MATCH (:Dataset)-[:HAS_DIMENSION]->(dim:Dimension)-[:HAS_VALUE]->(v:EntityValue) "
                    "RETURN DISTINCT v.id AS id, dim.key AS dimension_key, v.value AS value, "
                    "v.label AS label, coalesce(v.aliases, []) AS aliases ORDER BY dim.key, v.value"
                )
            ]
            dependencies = [
                dict(record)
                for record in session.run(
                    "MATCH (src:Metric)-[r:DRIVES]->(dst:Metric) "
                    "RETURN src.code AS source_metric_code, dst.code AS target_metric_code, "
                    "r.edge_type AS edge_type, r.reason AS reason, r.strength AS strength "
                    "ORDER BY src.code, dst.code"
                )
            ]
        inbound_dependencies: dict[str, list[dict[str, Any]]] = {}
        for item in dependencies:
            inbound_dependencies.setdefault(item["target_metric_code"], []).append(item)
        metric_map = {item["code"]: item for item in metrics}
        return {
            "metrics": metrics,
            "metric_map": metric_map,
            "metric_datasets": metric_datasets,
            "entity_values": entity_values,
            "dependencies": dependencies,
            "inbound_dependencies": inbound_dependencies,
        }

    def find_upstream_metrics(
        self,
        target_metric_codes: list[str],
        *,
        max_hops: int = 3,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not target_metric_codes:
            return []
        max_hops = max(1, min(max_hops, 6))
        query = (
            "UNWIND $target_codes AS target_code "
            f"MATCH p=(src:Metric)-[:DRIVES*1..{max_hops}]->(dst:Metric {{code: target_code}}) "
            "WITH src.code AS source_metric_code, target_code AS target_metric_code, "
            "min(length(p)) AS min_hops, "
            "max(reduce(score = 0.0, rel IN relationships(p) | score + coalesce(rel.strength, 0.0))) AS total_strength "
            "RETURN source_metric_code, target_metric_code, min_hops, total_strength "
            "ORDER BY min_hops ASC, total_strength DESC, source_metric_code ASC "
            "LIMIT $limit"
        )
        with self.driver.session(database=self.database) as session:
            return [
                dict(record)
                for record in session.run(query, target_codes=target_metric_codes, limit=limit)
            ]

    def find_dependency_paths(
        self,
        source_metric_codes: list[str],
        target_metric_codes: list[str],
        *,
        max_hops: int = 4,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not source_metric_codes or not target_metric_codes:
            return []
        max_hops = max(1, min(max_hops, 8))
        query = (
            "UNWIND $source_codes AS source_code "
            "UNWIND $target_codes AS target_code "
            f"MATCH p=(src:Metric {{code: source_code}})-[:DRIVES*1..{max_hops}]->(dst:Metric {{code: target_code}}) "
            "WITH source_code, target_code, p, "
            "reduce(score = 0.0, rel IN relationships(p) | score + coalesce(rel.strength, 0.0)) AS total_strength "
            "RETURN source_code, target_code, [node IN nodes(p) | node.code] AS metric_codes, "
            "length(p) AS hops, total_strength "
            "ORDER BY hops ASC, total_strength DESC, source_code ASC, target_code ASC "
            "LIMIT $limit"
        )
        with self.driver.session(database=self.database) as session:
            return [
                dict(record)
                for record in session.run(
                    query,
                    source_codes=source_metric_codes,
                    target_codes=target_metric_codes,
                    limit=limit,
                )
            ]

    def save_confirmed_relation(
        self,
        *,
        source_metric_code: str,
        target_metric_code: str,
        edge_type: str,
        lag_period: str | None = None,
        note: str = "",
        source_session_id: str | None = None,
        source_document_id: str | None = None,
        source_label: str | None = None,
        target_label: str | None = None,
        evidence_text: str = "",
        evidence_type: str = "",
        source_file: str = "",
        source_sheet: str = "",
        source_range: str = "",
        confidence: float | None = None,
        auto_saved: bool = False,
        source: str = "user_confirmed",
    ) -> dict[str, Any]:
        self.ensure_constraints()
        confirmed_at = datetime.now(UTC).isoformat()
        payload = {
            "source_metric_code": source_metric_code,
            "target_metric_code": target_metric_code,
            "edge_type": edge_type,
            "lag_period": lag_period,
            "note": note,
            "source_session_id": source_session_id,
            "source_document_id": source_document_id,
            "source_label": source_label or source_metric_code,
            "target_label": target_label or target_metric_code,
            "evidence_text": evidence_text,
            "evidence_type": evidence_type,
            "source_file": source_file,
            "source_sheet": source_sheet,
            "source_range": source_range,
            "confidence": confidence,
            "auto_saved": auto_saved,
            "confirmed_at": confirmed_at,
            "source": source,
        }
        query = (
            "MERGE (src:Metric {code: $source_metric_code}) "
            "ON CREATE SET src.label = $source_label, src.aliases = [$source_metric_code], "
            "src.sensitivity_level = 'internal', src.allow_roles = ['admin', 'analyst'], "
            "src.preferred_dataset = 'user_confirmed_memory', src.source = 'user_confirmed' "
            "MERGE (dst:Metric {code: $target_metric_code}) "
            "ON CREATE SET dst.label = $target_label, dst.aliases = [$target_metric_code], "
            "dst.sensitivity_level = 'internal', dst.allow_roles = ['admin', 'analyst'], "
            "dst.preferred_dataset = 'user_confirmed_memory', dst.source = 'user_confirmed' "
            "MERGE (src)-[r:CONFIRMED_RELATION]->(dst) "
            "SET r.edge_type = $edge_type, r.lag_period = $lag_period, r.note = $note, "
            "r.source_session_id = $source_session_id, r.source_document_id = $source_document_id, "
            "r.evidence_text = $evidence_text, r.evidence_type = $evidence_type, "
            "r.source_file = $source_file, r.source_sheet = $source_sheet, r.source_range = $source_range, "
            "r.confidence = $confidence, r.auto_saved = $auto_saved, "
            "r.confirmed_at = $confirmed_at, r.source = $source "
            "RETURN src.code AS source_metric_code, dst.code AS target_metric_code, "
            "r.edge_type AS edge_type, r.lag_period AS lag_period, r.note AS note, "
            "r.source_session_id AS source_session_id, r.source_document_id AS source_document_id, "
            "r.evidence_text AS evidence_text, r.evidence_type AS evidence_type, "
            "r.source_file AS source_file, r.source_sheet AS source_sheet, r.source_range AS source_range, "
            "r.confidence AS confidence, r.auto_saved AS auto_saved, "
            "r.confirmed_at AS confirmed_at, r.source AS source"
        )
        with self.driver.session(database=self.database) as session:
            record = session.run(query, **payload).single()
        return dict(record) if record else payload

    def list_confirmed_relations(self) -> list[dict[str, Any]]:
        query = (
            "MATCH (src:Metric)-[r:CONFIRMED_RELATION]->(dst:Metric) "
            "RETURN src.code AS source_metric_code, dst.code AS target_metric_code, "
            "r.edge_type AS edge_type, r.lag_period AS lag_period, r.note AS note, "
            "r.source_session_id AS source_session_id, r.source_document_id AS source_document_id, "
            "coalesce(r.evidence_text, '') AS evidence_text, coalesce(r.evidence_type, '') AS evidence_type, "
            "coalesce(r.source_file, '') AS source_file, coalesce(r.source_sheet, '') AS source_sheet, "
            "coalesce(r.source_range, '') AS source_range, r.confidence AS confidence, "
            "coalesce(r.auto_saved, false) AS auto_saved, "
            "r.confirmed_at AS confirmed_at, r.source AS source "
            "ORDER BY src.code, dst.code"
        )
        with self.driver.session(database=self.database) as session:
            return [dict(record) for record in session.run(query)]

    def save_metric_mapping(
        self,
        *,
        canonical_code: str,
        raw_label: str,
        label: str = "",
        aliases: list[str] | None = None,
        unit: str = "",
        department: str = "",
        source_sheet: str = "",
        section_path: str = "",
        semantic_type: str = "unknown",
        aggregation: str = "sum",
        confidence: float = 0.0,
        evidence: str = "",
        source_session_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_constraints()
        confirmed_at = datetime.now(UTC).isoformat()
        mapping_id = f"{department}:{source_sheet}:{raw_label}:{canonical_code}".lower()
        payload = {
            "id": mapping_id,
            "canonical_code": canonical_code,
            "raw_label": raw_label,
            "label": label or raw_label,
            "aliases": aliases or [],
            "unit": unit,
            "department": department,
            "source_sheet": source_sheet,
            "section_path": section_path,
            "semantic_type": semantic_type,
            "aggregation": aggregation,
            "confidence": confidence,
            "evidence": evidence,
            "source_session_id": source_session_id,
            "confirmed_at": confirmed_at,
        }
        query = (
            "MERGE (m:Metric {code: $canonical_code}) "
            "ON CREATE SET m.label = $label, m.aliases = $aliases, m.sensitivity_level = 'internal', "
            "m.allow_roles = ['admin', 'analyst'], m.preferred_dataset = 'dynamic_metric_registry', "
            "m.source = 'approved_metric_mapping' "
            "MERGE (mapping:MetricMapping {id: $id}) "
            "SET mapping.raw_label = $raw_label, mapping.label = $label, mapping.aliases = $aliases, "
            "mapping.unit = $unit, mapping.department = $department, mapping.source_sheet = $source_sheet, "
            "mapping.section_path = $section_path, mapping.semantic_type = $semantic_type, "
            "mapping.aggregation = $aggregation, mapping.confidence = $confidence, mapping.evidence = $evidence, "
            "mapping.source_session_id = $source_session_id, mapping.confirmed_at = $confirmed_at "
            "MERGE (mapping)-[:MAPS_TO]->(m) "
            "RETURN mapping.id AS id, m.code AS canonical_code, mapping.raw_label AS raw_label, "
            "mapping.confirmed_at AS confirmed_at"
        )
        with self.driver.session(database=self.database) as session:
            record = session.run(query, **payload).single()
        return dict(record) if record else payload

    def get_memory_priors(self) -> list[dict[str, Any]]:
        return [
            {
                **relation,
                "strength": 1.0,
                "reason": relation.get("note") or "Confirmed user memory prior.",
            }
            for relation in self.list_confirmed_relations()
        ]

    def get_metric_mapping_priors(self) -> list[dict[str, Any]]:
        query = (
            "MATCH (mapping:MetricMapping)-[:MAPS_TO]->(m:Metric) "
            "RETURN mapping.id AS id, "
            "m.code AS canonical_code, "
            "mapping.raw_label AS raw_label, "
            "coalesce(mapping.label, m.label, mapping.raw_label) AS label, "
            "coalesce(mapping.aliases, []) AS aliases, "
            "coalesce(mapping.unit, '') AS unit, "
            "coalesce(mapping.department, '') AS department, "
            "coalesce(mapping.source_sheet, '') AS source_sheet, "
            "coalesce(mapping.section_path, '') AS section_path, "
            "coalesce(mapping.semantic_type, m.semantic_type, 'unknown') AS semantic_type, "
            "coalesce(mapping.aggregation, 'sum') AS aggregation, "
            "coalesce(mapping.confidence, 1.0) AS confidence, "
            "coalesce(mapping.evidence, 'approved metric mapping memory') AS evidence, "
            "mapping.source_session_id AS source_session_id, "
            "mapping.confirmed_at AS confirmed_at "
            "ORDER BY m.code, mapping.raw_label"
        )
        with self.driver.session(database=self.database) as session:
            return [dict(record) for record in session.run(query)]

    def _load_datasets(self, session, datasets: list[dict[str, Any]]) -> None:
        session.run(
            "UNWIND $rows AS row "
            "MERGE (d:Dataset {key: row.key}) "
            "SET d.filename = row.filename, d.path = row.path, d.row_count = row.row_count, "
            "d.grain = row.grain, d.dimensions = row.dimensions, d.metrics = row.metrics, d.kind = row.kind, "
            "d.label = row.label, d.display_name = row.display_name, d.name = row.name",
            rows=datasets,
        ).consume()

    def _load_metrics(self, session, metrics: list[dict[str, Any]]) -> None:
        session.run(
            "UNWIND $rows AS row "
            "MERGE (m:Metric {code: row.code}) "
            "SET m.label = row.label, m.description = row.description, m.aliases = row.aliases, "
            "m.sensitivity_level = row.sensitivity_level, m.allow_roles = row.allow_roles, "
            "m.preferred_dataset = row.preferred_dataset, m.semantic_type = row.semantic_type, m.source = row.source, "
            "m.display_name = row.display_name, m.name = row.name",
            rows=metrics,
        ).consume()

    def _load_formulas(self, session, formulas: list[dict[str, Any]]) -> None:
        session.run(
            "UNWIND $rows AS row "
            "MATCH (m:Metric {code: row.metric_code}) "
            "MATCH (d:Dataset {key: row.dataset_key}) "
            "MERGE (f:Formula {id: row.id}) "
            "SET f.version = row.version, f.expression = row.expression, f.notes = row.notes, "
            "f.label = row.label, f.display_name = row.display_name, f.name = row.name "
            "MERGE (m)-[rf:USES_FORMULA]->(f) "
            "SET rf.label = 'использует формулу', rf.display_name = 'использует формулу', rf.name = 'использует формулу' "
            "MERGE (f)-[rd:FROM_DATASET]->(d) "
            "SET rd.label = 'из датасета', rd.display_name = 'из датасета', rd.name = 'из датасета'",
            rows=formulas,
        ).consume()

    def _load_dimensions(self, session, dimensions: list[dict[str, Any]]) -> None:
        session.run(
            "UNWIND $rows AS row "
            "MERGE (dim:Dimension {key: row.key}) "
            "SET dim.label = row.label, dim.aliases = row.aliases, "
            "dim.display_name = row.display_name, dim.name = row.name",
            rows=dimensions,
        ).consume()

    def _load_entity_values(self, session, entity_values: list[dict[str, Any]]) -> None:
        session.run(
            "UNWIND $rows AS row "
            "MATCH (dim:Dimension {key: row.dimension_key}) "
            "MERGE (v:EntityValue {id: row.id}) "
            "SET v.value = row.value, v.label = row.label, v.aliases = row.aliases, v.dataset_keys = row.dataset_keys, "
            "v.display_name = row.display_name, v.name = row.name "
            "MERGE (dim)-[r:HAS_VALUE]->(v) "
            "SET r.label = 'имеет значение', r.display_name = 'имеет значение', r.name = 'имеет значение'",
            rows=entity_values,
        ).consume()
        session.run(
            "UNWIND $rows AS row "
            "MATCH (d:Dataset {key: row.dataset_key}) "
            "MATCH (dim:Dimension {key: row.dimension_key}) "
            "MERGE (d)-[r:HAS_DIMENSION]->(dim) "
            "SET r.label = 'имеет измерение', r.display_name = 'имеет измерение', r.name = 'имеет измерение'",
            rows=[
                {"dataset_key": dataset_key, "dimension_key": row["dimension_key"]}
                for row in entity_values
                for dataset_key in row.get("dataset_keys", [])
            ],
        ).consume()

    def _load_dependencies(self, session, dependencies: list[dict[str, Any]]) -> None:
        session.run(
            "UNWIND $rows AS row "
            "MATCH (src:Metric {code: row.source_metric_code}) "
            "MATCH (dst:Metric {code: row.target_metric_code}) "
            "MERGE (src)-[r:DRIVES]->(dst) "
            "SET r.edge_type = row.edge_type, r.reason = row.reason, r.strength = row.strength, r.source = row.source, "
            "r.label = CASE row.edge_type "
            "  WHEN 'component' THEN 'входит в состав' "
            "  WHEN 'inverse_driver' THEN 'снижает' "
            "  ELSE 'влияет на' "
            "END, "
            "r.display_name = CASE row.edge_type "
            "  WHEN 'component' THEN 'входит в состав' "
            "  WHEN 'inverse_driver' THEN 'снижает' "
            "  ELSE 'влияет на' "
            "END, "
            "r.name = CASE row.edge_type "
            "  WHEN 'component' THEN 'входит в состав' "
            "  WHEN 'inverse_driver' THEN 'снижает' "
            "  ELSE 'влияет на' "
            "END",
            rows=dependencies,
        ).consume()

    def _load_lag_rules(self, session, lag_rules: list[dict[str, Any]]) -> None:
        session.run(
            "UNWIND $rows AS row "
            "MATCH (m:Metric {code: row.metric_code}) "
            "MERGE (r:LagRule {id: row.id}) "
            "SET r.metric_code = row.metric_code, r.lag_period = row.lag_period, r.severity = row.severity, "
            "r.rule_text = row.rule_text, r.is_active = row.is_active, r.source = row.source, "
            "r.label = row.label, r.display_name = row.display_name, r.name = row.name "
            "MERGE (m)-[rel:HAS_LAG_RULE]->(r) "
            "SET rel.label = 'имеет правило лага', rel.display_name = 'имеет правило лага', rel.name = 'имеет правило лага'",
            rows=lag_rules,
        ).consume()

    def _load_verdict_rules(self, session, verdict_rules: list[dict[str, Any]]) -> None:
        session.run(
            "UNWIND $rows AS row "
            "MERGE (r:VerdictRule {code: row.code}) "
            "SET r.label = row.label, r.metric_codes = row.metric_codes, r.verdict_text = row.verdict_text, "
            "r.priority = row.priority, r.is_active = row.is_active, r.source = row.source, "
            "r.display_name = row.display_name, r.name = row.name",
            rows=verdict_rules,
        ).consume()
        session.run(
            "UNWIND $rows AS row "
            "MATCH (r:VerdictRule {code: row.code}) "
            "UNWIND row.metric_codes AS metric_code "
            "MATCH (m:Metric {code: metric_code}) "
            "MERGE (r)-[rel:APPLIES_TO]->(m) "
            "SET rel.label = 'применяется к', rel.display_name = 'применяется к', rel.name = 'применяется к'",
            rows=verdict_rules,
        ).consume()
