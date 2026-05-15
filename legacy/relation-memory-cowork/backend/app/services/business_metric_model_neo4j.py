from __future__ import annotations

import json
from typing import Any

from neo4j import GraphDatabase

from app.config import settings
from app.services.business_metric_model import (
    BusinessMetricModelBundle,
    MetricDefinition,
    ModelMetadata,
    ObservationRecord,
    RelationDefinition,
)


class BusinessMetricModelNeo4jClient:
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
            "CREATE CONSTRAINT business_model_key IF NOT EXISTS FOR (n:BusinessModel) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT business_metric_uid IF NOT EXISTS FOR (n:Metric) REQUIRE n.uid IS UNIQUE",
            "CREATE CONSTRAINT business_relation_uid IF NOT EXISTS FOR (n:Relation) REQUIRE n.uid IS UNIQUE",
            "CREATE CONSTRAINT business_observation_id IF NOT EXISTS FOR (n:Observation) REQUIRE n.id IS UNIQUE",
        ]
        with self.driver.session(database=self.database) as session:
            for query in queries:
                session.run(query).consume()

    def import_model(self, bundle: BusinessMetricModelBundle) -> dict[str, Any]:
        model_key = bundle.model.key
        self.ensure_constraints()
        with self.driver.session(database=self.database) as session:
            session.run(
                "MATCH (n) WHERE coalesce(n.model_key, '') = $model_key DETACH DELETE n",
                model_key=model_key,
            ).consume()
            session.run(
                "MATCH (m:BusinessModel {key: $model_key}) DETACH DELETE m",
                model_key=model_key,
            ).consume()
            session.run(
                "MERGE (m:BusinessModel {key: $key}) "
                "SET m.model_key = $key, m.name = $name, m.version = $version, m.description = $description",
                key=bundle.model.key,
                name=bundle.model.name,
                version=bundle.model.version,
                description=bundle.model.description,
            ).consume()
            self._load_metrics(session, bundle)
            self._load_relations(session, bundle)
            self._load_observations(session, bundle)
        return {
            "model_key": bundle.model.key,
            "metric_count": len(bundle.metrics),
            "relation_count": len(bundle.relations),
            "observation_count": len(bundle.observations),
        }

    def read_model(self, model_key: str) -> BusinessMetricModelBundle:
        with self.driver.session(database=self.database) as session:
            model_record = session.run(
                "MATCH (m:BusinessModel {key: $key}) "
                "RETURN m.key AS key, m.name AS name, m.version AS version, m.description AS description",
                key=model_key,
            ).single()
            if model_record is None:
                raise ValueError(f"Business model {model_key!r} was not found in Neo4j.")
            model_row = dict(model_record)
            metric_rows = list(
                session.run(
                    "MATCH (:BusinessModel {key: $key})-[:HAS_METRIC]->(m:Metric) "
                    "RETURN m.code AS code, m.name AS name, m.description AS description, "
                    "m.unit AS unit, m.kind AS kind, m.category AS category, m.time_grain AS time_grain, "
                    "coalesce(m.scope_tags, []) AS scope_tags, coalesce(m.aliases, []) AS aliases, "
                    "m.source_refs_json AS source_refs_json, m.active AS active "
                    "ORDER BY m.code",
                    key=model_key,
                )
            )
            relation_rows = list(
                session.run(
                    "MATCH (:BusinessModel {key: $key})-[:HAS_RELATION]->(r:Relation)-[:OUTPUT_TO]->(target:Metric) "
                    "OPTIONAL MATCH (source:Metric)-[ir:INPUT_TO]->(r) "
                    "RETURN r.id AS id, r.name AS name, r.description AS description, "
                    "target.code AS target_metric_code, r.relation_kind AS relation_kind, "
                    "r.output_mode AS output_mode, r.expression AS expression, r.confidence AS confidence, "
                    "r.status AS status, r.source_refs_json AS source_refs_json, "
                    "r.applies_to_json AS applies_to_json, "
                    "coalesce(r.tags, []) AS tags, "
                    "collect({alias: ir.alias, metric_code: source.code, value_mode: ir.value_mode, lag_periods: ir.lag_periods, required: ir.required, slice_overrides_json: ir.slice_overrides_json}) AS inputs "
                    "ORDER BY r.id",
                    key=model_key,
                )
            )
            observation_rows = list(
                session.run(
                    "MATCH (:BusinessModel {key: $key})-[:HAS_OBSERVATION]->(o:Observation) "
                    "RETURN o.metric_code AS metric_code, o.period AS period, o.scenario AS scenario, "
                    "o.slice_json AS slice_json, o.value AS value, o.source_refs_json AS source_refs_json "
                    "ORDER BY o.metric_code, o.period, o.scenario",
                    key=model_key,
                )
            )

        model = ModelMetadata.model_validate(model_row)
        metrics = [
            MetricDefinition.model_validate(
                {
                    **dict(row),
                    "source_refs": json.loads(row["source_refs_json"] or "[]"),
                }
            )
            for row in metric_rows
        ]
        relations: list[RelationDefinition] = []
        for row in relation_rows:
            inputs = {
                item["alias"]: {
                    "metric_code": item["metric_code"],
                    "value_mode": item["value_mode"],
                    "lag_periods": item["lag_periods"],
                    "required": item["required"],
                    "slice_overrides": json.loads(item["slice_overrides_json"] or "{}"),
                }
                for item in row["inputs"]
                if item["alias"] is not None and item["metric_code"] is not None
            }
            relations.append(
                RelationDefinition.model_validate(
                    {
                        **dict(row),
                        "applies_to": json.loads(row["applies_to_json"] or "{}"),
                        "source_refs": json.loads(row["source_refs_json"] or "[]"),
                        "inputs": inputs,
                    }
                )
            )
        observations = [
            ObservationRecord.model_validate(
                {
                    **dict(row),
                    "slice": json.loads(row["slice_json"] or "{}"),
                    "source_refs": json.loads(row["source_refs_json"] or "[]"),
                }
            )
            for row in observation_rows
        ]
        return BusinessMetricModelBundle(
            model=model,
            metrics=metrics,
            relations=relations,
            observations=observations,
        )

    def _load_metrics(self, session, bundle: BusinessMetricModelBundle) -> None:
        rows = [
            {
                "uid": f"{bundle.model.key}::{metric.code}",
                "model_key": bundle.model.key,
                "code": metric.code,
                "name": metric.name,
                "description": metric.description,
                "unit": metric.unit,
                "kind": metric.kind,
                "category": metric.category,
                "time_grain": metric.time_grain,
                "scope_tags": metric.scope_tags,
                "aliases": metric.aliases,
                "source_refs_json": json.dumps(
                    [item.model_dump(mode="json") for item in metric.source_refs],
                    ensure_ascii=False,
                ),
                "active": metric.active,
            }
            for metric in bundle.metrics
        ]
        session.run(
            "UNWIND $rows AS row "
            "MATCH (bm:BusinessModel {key: row.model_key}) "
            "MERGE (m:Metric {uid: row.uid}) "
            "SET m.model_key = row.model_key, m.code = row.code, m.name = row.name, "
            "m.description = row.description, m.unit = row.unit, m.kind = row.kind, "
            "m.category = row.category, m.time_grain = row.time_grain, m.scope_tags = row.scope_tags, "
            "m.aliases = row.aliases, m.source_refs_json = row.source_refs_json, m.active = row.active "
            "MERGE (bm)-[:HAS_METRIC]->(m)",
            rows=rows,
        ).consume()

    def _load_relations(self, session, bundle: BusinessMetricModelBundle) -> None:
        relation_rows = [
            {
                "uid": f"{bundle.model.key}::{relation.id}",
                "model_key": bundle.model.key,
                "id": relation.id,
                "name": relation.name,
                "description": relation.description,
                "target_uid": f"{bundle.model.key}::{relation.target_metric_code}",
                "relation_kind": relation.relation_kind,
                "output_mode": relation.output_mode,
                "expression": relation.expression,
                "applies_to_json": json.dumps(
                    relation.applies_to, ensure_ascii=False, sort_keys=True
                ),
                "confidence": relation.confidence,
                "status": relation.status,
                "tags": relation.tags,
                "source_refs_json": json.dumps(
                    [item.model_dump(mode="json") for item in relation.source_refs],
                    ensure_ascii=False,
                ),
            }
            for relation in bundle.relations
        ]
        session.run(
            "UNWIND $rows AS row "
            "MATCH (bm:BusinessModel {key: row.model_key}) "
            "MATCH (target:Metric {uid: row.target_uid}) "
            "MERGE (r:Relation {uid: row.uid}) "
            "SET r.model_key = row.model_key, r.id = row.id, r.name = row.name, r.description = row.description, "
            "r.relation_kind = row.relation_kind, r.output_mode = row.output_mode, r.expression = row.expression, "
            "r.applies_to_json = row.applies_to_json, r.confidence = row.confidence, r.status = row.status, r.tags = row.tags, "
            "r.source_refs_json = row.source_refs_json "
            "MERGE (bm)-[:HAS_RELATION]->(r) "
            "MERGE (r)-[:OUTPUT_TO]->(target)",
            rows=relation_rows,
        ).consume()

        input_rows = []
        for relation in bundle.relations:
            relation_uid = f"{bundle.model.key}::{relation.id}"
            for alias, binding in relation.inputs.items():
                input_rows.append(
                    {
                        "relation_uid": relation_uid,
                        "metric_uid": f"{bundle.model.key}::{binding.metric_code}",
                        "alias": alias,
                        "value_mode": binding.value_mode,
                        "lag_periods": binding.lag_periods,
                        "required": binding.required,
                        "slice_overrides_json": json.dumps(
                            binding.slice_overrides, ensure_ascii=False, sort_keys=True
                        ),
                    }
                )
        session.run(
            "UNWIND $rows AS row "
            "MATCH (m:Metric {uid: row.metric_uid}) "
            "MATCH (r:Relation {uid: row.relation_uid}) "
            "MERGE (m)-[rel:INPUT_TO {relation_uid: row.relation_uid, alias: row.alias}]->(r) "
            "SET rel.value_mode = row.value_mode, rel.lag_periods = row.lag_periods, rel.required = row.required, "
            "rel.slice_overrides_json = row.slice_overrides_json",
            rows=input_rows,
        ).consume()

    def _load_observations(self, session, bundle: BusinessMetricModelBundle) -> None:
        rows = [
            {
                "id": observation.id,
                "model_key": bundle.model.key,
                "metric_uid": f"{bundle.model.key}::{observation.metric_code}",
                "metric_code": observation.metric_code,
                "period": observation.period,
                "scenario": observation.scenario,
                "slice_json": json.dumps(observation.slice, ensure_ascii=False, sort_keys=True),
                "value": observation.value,
                "source_refs_json": json.dumps(
                    [item.model_dump(mode="json") for item in observation.source_refs],
                    ensure_ascii=False,
                ),
            }
            for observation in bundle.observations
        ]
        session.run(
            "UNWIND $rows AS row "
            "MATCH (bm:BusinessModel {key: row.model_key}) "
            "MATCH (m:Metric {uid: row.metric_uid}) "
            "MERGE (o:Observation {id: row.id}) "
            "SET o.model_key = row.model_key, o.metric_code = row.metric_code, o.period = row.period, "
            "o.scenario = row.scenario, o.slice_json = row.slice_json, o.value = row.value, "
            "o.source_refs_json = row.source_refs_json "
            "MERGE (bm)-[:HAS_OBSERVATION]->(o) "
            "MERGE (m)-[:HAS_OBSERVATION]->(o)",
            rows=rows,
        ).consume()
