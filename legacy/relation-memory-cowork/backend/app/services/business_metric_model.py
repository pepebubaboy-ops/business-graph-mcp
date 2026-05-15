from __future__ import annotations

import ast
import gzip
import itertools
import json
import math
import operator
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


MetricKind = Literal["observed", "derived", "external", "latent", "adjustment"]
RelationKind = Literal["formula", "driver", "inverse_driver", "allocation", "constraint", "correlation"]
RelationOutputMode = Literal["level", "delta"]
RelationStatus = Literal["draft", "active", "deprecated"]
InputValueMode = Literal["level", "difference", "pct_change"]


def _normalize_text(value: str) -> str:
    return value.strip()


def _clip(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


ALLOWED_CALLS = {
    "abs": abs,
    "max": max,
    "min": min,
    "round": round,
    "clip": _clip,
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
    "sum_": lambda *args: sum(args),
}

ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}

ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

ALLOWED_COMPARISONS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


class BusinessMetricModelError(ValueError):
    pass


class SourceReference(BaseModel):
    kind: str = "manual"
    workbook: str = ""
    sheet: str = ""
    row_label: str = ""
    note: str = ""


class ModelMetadata(BaseModel):
    key: str
    name: str
    version: str = "1"
    description: str = ""


class MetricDefinition(BaseModel):
    code: str
    name: str
    description: str = ""
    unit: str = ""
    kind: MetricKind = "observed"
    category: str = ""
    time_grain: str = "monthly"
    scope_tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(default_factory=list)
    active: bool = True

    @model_validator(mode="after")
    def validate_metric(self) -> "MetricDefinition":
        self.code = _normalize_text(self.code)
        self.name = _normalize_text(self.name)
        if not self.code:
            raise BusinessMetricModelError("Metric code cannot be empty.")
        if not self.name:
            raise BusinessMetricModelError(f"Metric {self.code} must have a name.")
        return self


class RelationInputBinding(BaseModel):
    metric_code: str
    value_mode: InputValueMode = "level"
    lag_periods: int = 0
    required: bool = True
    slice_overrides: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_binding(self) -> "RelationInputBinding":
        self.metric_code = _normalize_text(self.metric_code)
        if not self.metric_code:
            raise BusinessMetricModelError("Relation input metric_code cannot be empty.")
        if self.lag_periods < 0:
            raise BusinessMetricModelError(f"lag_periods must be >= 0 for {self.metric_code}.")
        return self


class RelationDefinition(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    target_metric_code: str
    relation_kind: RelationKind
    output_mode: RelationOutputMode = "level"
    expression: str
    inputs: dict[str, RelationInputBinding]
    applies_to: dict[str, str] = Field(default_factory=dict)
    confidence: float = 1.0
    status: RelationStatus = "active"
    source_refs: list[SourceReference] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_relation(self) -> "RelationDefinition":
        self.id = _normalize_text(self.id)
        self.target_metric_code = _normalize_text(self.target_metric_code)
        self.expression = _normalize_text(self.expression)
        if not self.id:
            raise BusinessMetricModelError("Relation id cannot be empty.")
        if not self.target_metric_code:
            raise BusinessMetricModelError(f"Relation {self.id} must declare target_metric_code.")
        if not self.expression:
            raise BusinessMetricModelError(f"Relation {self.id} must declare a quantitative expression.")
        if not self.inputs:
            raise BusinessMetricModelError(f"Relation {self.id} must declare at least one input.")
        self.applies_to = {
            _normalize_text(str(key)): _normalize_text(str(value))
            for key, value in self.applies_to.items()
            if _normalize_text(str(key))
        }
        if not 0.0 <= self.confidence <= 1.0:
            raise BusinessMetricModelError(f"Relation {self.id} confidence must be between 0 and 1.")
        return self


class ObservationRecord(BaseModel):
    metric_code: str
    period: str
    scenario: str = "fact"
    slice: dict[str, str] = Field(default_factory=dict)
    value: float
    source_refs: list[SourceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_observation(self) -> "ObservationRecord":
        self.metric_code = _normalize_text(self.metric_code)
        self.period = _normalize_text(self.period)
        self.scenario = _normalize_text(self.scenario) or "fact"
        if not self.metric_code:
            raise BusinessMetricModelError("Observation metric_code cannot be empty.")
        if not self.period:
            raise BusinessMetricModelError(f"Observation for {self.metric_code} must declare period.")
        return self

    @property
    def slice_key(self) -> str:
        return canonical_slice_key(self.slice)

    @property
    def id(self) -> str:
        return f"{self.metric_code}|{self.period}|{self.scenario}|{self.slice_key}"


class BusinessMetricModelBundle(BaseModel):
    model: ModelMetadata
    metrics: list[MetricDefinition]
    relations: list[RelationDefinition] = Field(default_factory=list)
    observations: list[ObservationRecord] = Field(default_factory=list)


def canonical_slice_key(value: dict[str, str] | None) -> str:
    if not value:
        return "{}"
    normalized = {str(key): str(item) for key, item in sorted(value.items())}
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def parse_slice_args(pairs: list[str] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise BusinessMetricModelError(f"Invalid --slice value {item!r}. Expected key=value.")
        key, value = item.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def shift_period(period: str, lag_periods: int) -> str:
    if lag_periods == 0:
        return period
    if len(period) == 7:
        year = int(period[:4])
        month = int(period[5:7])
        total = year * 12 + (month - 1) - lag_periods
        shifted_year = total // 12
        shifted_month = total % 12 + 1
        return f"{shifted_year:04d}-{shifted_month:02d}"
    if len(period) == 10:
        parsed = date.fromisoformat(period)
        shifted_month = parsed.month - lag_periods
        shifted_year = parsed.year
        while shifted_month <= 0:
            shifted_year -= 1
            shifted_month += 12
        day = min(parsed.day, _days_in_month(shifted_year, shifted_month))
        return date(shifted_year, shifted_month, day).isoformat()
    raise BusinessMetricModelError(f"Unsupported period format for lag handling: {period!r}")


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    next_month = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    current_month = date(year, month, 1)
    return (next_month - current_month).days


class ExpressionProgram:
    def __init__(self, expression: str):
        self.expression = expression
        try:
            parsed = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise BusinessMetricModelError(f"Invalid expression {expression!r}: {exc.msg}") from exc
        self._validate(parsed)
        self._tree = parsed
        self.names = tuple(sorted(self._collect_names(parsed)))

    def evaluate(self, variables: dict[str, float]) -> float:
        return self._as_number(self._eval_node(self._tree.body, variables))

    def _as_number(self, value: Any) -> float:
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        if value in (None, "", " "):
            return 0.0
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0.0
            return float(stripped)
        raise BusinessMetricModelError(
            f"Expression {self.expression!r} evaluated to unsupported value {value!r}."
        )

    def _validate(self, node: ast.AST) -> None:
        allowed_nodes = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.Call,
            ast.BoolOp,
            ast.Compare,
            ast.Name,
            ast.Load,
            ast.Constant,
            ast.And,
            ast.Or,
            ast.Eq,
            ast.NotEq,
            ast.Lt,
            ast.LtE,
            ast.Gt,
            ast.GtE,
            *tuple(ALLOWED_BINOPS.keys()),
            *tuple(ALLOWED_UNARYOPS.keys()),
        )
        for item in ast.walk(node):
            if not isinstance(item, allowed_nodes):
                raise BusinessMetricModelError(
                    f"Expression {self.expression!r} contains unsupported syntax: {type(item).__name__}."
                )
            if isinstance(item, ast.BinOp) and type(item.op) not in ALLOWED_BINOPS:
                raise BusinessMetricModelError(
                    f"Expression {self.expression!r} uses unsupported operator: {type(item.op).__name__}."
                )
            if isinstance(item, ast.UnaryOp) and type(item.op) not in ALLOWED_UNARYOPS:
                raise BusinessMetricModelError(
                    f"Expression {self.expression!r} uses unsupported unary operator: {type(item.op).__name__}."
                )
            if isinstance(item, ast.Call):
                if not isinstance(item.func, ast.Name) or item.func.id not in {
                    *ALLOWED_CALLS.keys(),
                    "iferror",
                    "if_",
                }:
                    raise BusinessMetricModelError(
                        f"Expression {self.expression!r} uses unsupported function call."
                    )
            if isinstance(item, ast.Compare):
                for op in item.ops:
                    if type(op) not in ALLOWED_COMPARISONS:
                        raise BusinessMetricModelError(
                            f"Expression {self.expression!r} uses unsupported comparison operator: {type(op).__name__}."
                        )

    def _collect_names(self, node: ast.AST) -> set[str]:
        names: set[str] = set()
        reserved_names = {*ALLOWED_CALLS.keys(), "iferror", "if_"}
        for item in ast.walk(node):
            if isinstance(item, ast.Name) and item.id not in reserved_names:
                names.add(item.id)
        return names

    def _eval_node(self, node: ast.AST, variables: dict[str, float]) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, str, bool)) or node.value is None:
                return node.value
            raise BusinessMetricModelError(f"Unsupported constant in expression {self.expression!r}.")
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise BusinessMetricModelError(
                    f"Expression {self.expression!r} requires variable {node.id!r}, which is missing."
                )
            return variables[node.id]
        if isinstance(node, ast.BinOp):
            left = self._as_number(self._eval_node(node.left, variables))
            right = self._as_number(self._eval_node(node.right, variables))
            return ALLOWED_BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            operand = self._as_number(self._eval_node(node.operand, variables))
            return ALLOWED_UNARYOPS[type(node.op)](operand)
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result = True
                for value in node.values:
                    result = bool(self._eval_node(value, variables))
                    if not result:
                        return False
                return result
            if isinstance(node.op, ast.Or):
                for value in node.values:
                    result = bool(self._eval_node(value, variables))
                    if result:
                        return True
                return False
        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left, variables)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval_node(comparator, variables)
                if not ALLOWED_COMPARISONS[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            fn_name = node.func.id
            if fn_name == "iferror":
                if len(node.args) != 2:
                    raise BusinessMetricModelError(f"iferror() expects exactly two arguments in {self.expression!r}.")
                try:
                    return self._eval_node(node.args[0], variables)
                except Exception:
                    return self._eval_node(node.args[1], variables)
            if fn_name == "if_":
                if len(node.args) != 3:
                    raise BusinessMetricModelError(f"if_() expects exactly three arguments in {self.expression!r}.")
                condition = self._eval_node(node.args[0], variables)
                return self._eval_node(node.args[1], variables) if condition else self._eval_node(node.args[2], variables)
            fn = ALLOWED_CALLS[fn_name]
            args = [self._as_number(self._eval_node(arg, variables)) for arg in node.args]
            return fn(*args)
        raise BusinessMetricModelError(f"Unsupported expression node: {type(node).__name__}.")


class BusinessMetricModelLoader:
    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)

    def load(self) -> BusinessMetricModelBundle:
        model_data = self._read_yaml("model.yaml")
        metrics_data = self._read_yaml("metrics.yaml")
        relations_data = self._read_yaml("relations.yaml", required=False) or []
        observations_data = self._read_yaml("observations.yaml", required=False) or []

        metrics = metrics_data.get("metrics", metrics_data) if isinstance(metrics_data, dict) else metrics_data
        relations = (
            relations_data.get("relations", relations_data)
            if isinstance(relations_data, dict)
            else relations_data
        )
        observations = (
            observations_data.get("observations", observations_data)
            if isinstance(observations_data, dict)
            else observations_data
        )
        bundle = BusinessMetricModelBundle(
            model=ModelMetadata.model_validate(model_data),
            metrics=[MetricDefinition.model_validate(item) for item in metrics],
            relations=[RelationDefinition.model_validate(item) for item in relations],
            observations=[ObservationRecord.model_validate(item) for item in observations],
        )
        BusinessMetricModelRuntime(bundle)
        return bundle

    def _read_yaml(self, file_name: str, *, required: bool = True) -> Any:
        path = self.model_dir / file_name
        gzip_path = path.with_name(f"{path.name}.gz")
        source_path = path if path.exists() else gzip_path
        if not source_path.exists():
            if required:
                raise BusinessMetricModelError(f"Missing required model file: {path}")
            return None
        if source_path.suffix == ".gz":
            with gzip.open(source_path, "rt", encoding="utf-8") as file:
                loaded = yaml.safe_load(file)
        else:
            loaded = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        return loaded or {}


class BusinessMetricModelWriter:
    def __init__(self, model_dir: Path, *, compress: bool = False):
        self.model_dir = Path(model_dir)
        self.compress = compress

    def write(self, bundle: BusinessMetricModelBundle) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._write_yaml("model.yaml", bundle.model.model_dump(mode="json"))
        self._write_yaml(
            "metrics.yaml",
            {"metrics": [item.model_dump(mode="json") for item in bundle.metrics]},
        )
        self._write_yaml(
            "relations.yaml",
            {"relations": [item.model_dump(mode="json") for item in bundle.relations]},
        )
        self._write_yaml(
            "observations.yaml",
            {"observations": [item.model_dump(mode="json") for item in bundle.observations]},
        )

    def _write_yaml(self, file_name: str, payload: Any) -> None:
        path = self.model_dir / file_name
        text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        if self.compress and file_name != "model.yaml":
            path.unlink(missing_ok=True)
            with gzip.open(path.with_name(f"{path.name}.gz"), "wt", encoding="utf-8") as file:
                file.write(text)
            return
        path.with_name(f"{path.name}.gz").unlink(missing_ok=True)
        path.write_text(text, encoding="utf-8")


class MetricValueResolution(BaseModel):
    metric_code: str
    period: str
    scenario: str
    slice_key: str
    value: float
    source: Literal["observation", "derived"]
    relation_id: str | None = None


class RelationExplanation(BaseModel):
    relation_id: str
    relation_kind: RelationKind
    output_mode: RelationOutputMode
    target_metric_code: str
    explained_delta: float
    predicted_current: float
    predicted_baseline: float
    confidence: float
    input_contributions: list[dict[str, Any]]
    missing_inputs: list[str] = Field(default_factory=list)


class MetricChangeExplanation(BaseModel):
    target_metric_code: str
    period: str
    baseline_period: str
    scenario: str
    slice: dict[str, str]
    current_value: float | None
    baseline_value: float | None
    delta: float | None
    primary_formula_relations: list[RelationExplanation]
    secondary_driver_relations: list[RelationExplanation]
    upstream_paths: list[dict[str, Any]]


class RelationAuditSample(BaseModel):
    period: str
    scenario: str
    slice: dict[str, str]
    observed_value: float
    predicted_value: float
    absolute_error: float


class RelationAuditSummary(BaseModel):
    relation_id: str
    relation_kind: RelationKind
    output_mode: RelationOutputMode
    target_metric_code: str
    applies_to: dict[str, str]
    sample_count: int
    evaluated_count: int
    missing_sample_count: int
    mean_absolute_error: float | None
    max_absolute_error: float | None
    worst_samples: list[RelationAuditSample] = Field(default_factory=list)


class BusinessMetricModelRuntime:
    def __init__(self, bundle: BusinessMetricModelBundle):
        self.bundle = bundle
        self.metric_by_code = {item.code: item for item in bundle.metrics}
        self.relation_by_id = {item.id: item for item in bundle.relations}
        self.relations_by_target: dict[str, list[RelationDefinition]] = defaultdict(list)
        self.input_relations_by_metric: dict[str, list[RelationDefinition]] = defaultdict(list)
        self.observation_by_key = {
            (item.metric_code, item.period, item.scenario, item.slice_key): item
            for item in bundle.observations
        }
        self._programs = {item.id: ExpressionProgram(item.expression) for item in bundle.relations}
        self._validate()

        for relation in bundle.relations:
            self.relations_by_target[relation.target_metric_code].append(relation)
            for binding in relation.inputs.values():
                self.input_relations_by_metric[binding.metric_code].append(relation)

    def summarize(self) -> dict[str, Any]:
        return {
            "model_key": self.bundle.model.key,
            "metric_count": len(self.bundle.metrics),
            "relation_count": len(self.bundle.relations),
            "observation_count": len(self.bundle.observations),
            "active_formula_count": len([item for item in self.bundle.relations if item.status == "active" and item.relation_kind == "formula"]),
        }

    def trace_upstream(
        self,
        target_metric_code: str,
        *,
        slice_filters: dict[str, str] | None = None,
        max_hops: int = 3,
    ) -> list[dict[str, Any]]:
        if target_metric_code not in self.metric_by_code:
            raise BusinessMetricModelError(f"Unknown metric: {target_metric_code}")
        root_slice = slice_filters or {}
        queue: list[tuple[str, dict[str, str], list[dict[str, Any]]]] = [(target_metric_code, root_slice, [])]
        paths: list[dict[str, Any]] = []
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        while queue:
            current_metric, current_slice, path = queue.pop(0)
            if len(path) >= max_hops:
                continue
            for relation in self._matching_relations(current_metric, current_slice):
                for alias, binding in relation.inputs.items():
                    next_slice = {**current_slice, **binding.slice_overrides}
                    next_path = [
                        *path,
                        {
                            "relation_id": relation.id,
                            "relation_kind": relation.relation_kind,
                            "relation_applies_to": relation.applies_to,
                            "from_metric": binding.metric_code,
                            "to_metric": current_metric,
                            "input_alias": alias,
                            "slice": next_slice,
                        },
                    ]
                    path_key = (
                        binding.metric_code,
                        canonical_slice_key(next_slice),
                        tuple(item["relation_id"] for item in next_path),
                    )
                    if path_key in seen:
                        continue
                    seen.add(path_key)
                    paths.append(
                        {
                            "source_metric_code": binding.metric_code,
                            "target_metric_code": target_metric_code,
                            "slice": next_slice,
                            "hops": len(next_path),
                            "path": next_path,
                        }
                    )
                    queue.append((binding.metric_code, next_slice, next_path))
        paths.sort(key=lambda item: (item["hops"], item["source_metric_code"]))
        return paths

    def explain_metric_change(
        self,
        target_metric_code: str,
        *,
        period: str,
        baseline_period: str,
        scenario: str = "fact",
        slice_filters: dict[str, str] | None = None,
        max_hops: int = 3,
    ) -> MetricChangeExplanation:
        slice_filters = slice_filters or {}
        current = self.resolve_metric_level(
            target_metric_code,
            period=period,
            scenario=scenario,
            slice_filters=slice_filters,
        )
        baseline = self.resolve_metric_level(
            target_metric_code,
            period=baseline_period,
            scenario=scenario,
            slice_filters=slice_filters,
        )
        primary_formula_relations: list[RelationExplanation] = []
        secondary_driver_relations: list[RelationExplanation] = []
        for relation in self.relations_by_target.get(target_metric_code, []):
            if relation.status != "active":
                continue
            if not self._relation_matches_slice(relation, slice_filters):
                continue
            explained = self._explain_relation_delta(
                relation,
                period=period,
                baseline_period=baseline_period,
                scenario=scenario,
                slice_filters=slice_filters,
            )
            if explained is None:
                continue
            if relation.relation_kind == "formula":
                primary_formula_relations.append(explained)
            else:
                secondary_driver_relations.append(explained)
        primary_formula_relations.sort(key=lambda item: abs(item.explained_delta), reverse=True)
        secondary_driver_relations.sort(key=lambda item: abs(item.explained_delta), reverse=True)
        return MetricChangeExplanation(
            target_metric_code=target_metric_code,
            period=period,
            baseline_period=baseline_period,
            scenario=scenario,
            slice=slice_filters,
            current_value=current.value if current else None,
            baseline_value=baseline.value if baseline else None,
            delta=(current.value - baseline.value) if current and baseline else None,
            primary_formula_relations=primary_formula_relations,
            secondary_driver_relations=secondary_driver_relations,
            upstream_paths=self.trace_upstream(target_metric_code, slice_filters=slice_filters, max_hops=max_hops),
        )

    def audit_relations(
        self,
        *,
        scenario: str | None = None,
        period: str | None = None,
        include_drivers: bool = True,
    ) -> dict[str, Any]:
        summaries: list[RelationAuditSummary] = []
        for relation in self.bundle.relations:
            if relation.status != "active" or relation.output_mode != "level":
                continue
            if not include_drivers and relation.relation_kind != "formula":
                continue
            target_observations = [
                item
                for item in self.bundle.observations
                if item.metric_code == relation.target_metric_code
                and (scenario is None or item.scenario == scenario)
                and (period is None or item.period == period)
                and self._relation_matches_slice(relation, item.slice)
            ]
            errors: list[RelationAuditSample] = []
            missing_count = 0
            for observation in target_observations:
                predicted_value = self._predict_relation_value(
                    relation,
                    period=observation.period,
                    scenario=observation.scenario,
                    slice_filters=observation.slice,
                )
                if predicted_value is None:
                    missing_count += 1
                    continue
                errors.append(
                    RelationAuditSample(
                        period=observation.period,
                        scenario=observation.scenario,
                        slice=observation.slice,
                        observed_value=float(observation.value),
                        predicted_value=predicted_value,
                        absolute_error=abs(predicted_value - float(observation.value)),
                    )
                )
            absolute_errors = [item.absolute_error for item in errors]
            summaries.append(
                RelationAuditSummary(
                    relation_id=relation.id,
                    relation_kind=relation.relation_kind,
                    output_mode=relation.output_mode,
                    target_metric_code=relation.target_metric_code,
                    applies_to=relation.applies_to,
                    sample_count=len(target_observations),
                    evaluated_count=len(errors),
                    missing_sample_count=missing_count,
                    mean_absolute_error=(
                        sum(absolute_errors) / len(absolute_errors) if absolute_errors else None
                    ),
                    max_absolute_error=max(absolute_errors) if absolute_errors else None,
                    worst_samples=sorted(
                        errors,
                        key=lambda item: item.absolute_error,
                        reverse=True,
                    )[:3],
                )
            )
        summaries.sort(
            key=lambda item: (
                item.max_absolute_error is None,
                -(item.max_absolute_error or 0.0),
                item.relation_id,
            )
        )
        return {
            "model_key": self.bundle.model.key,
            "relation_count": len(summaries),
            "audits": [item.model_dump(mode="json") for item in summaries],
        }

    def resolve_metric_level(
        self,
        metric_code: str,
        *,
        period: str,
        scenario: str,
        slice_filters: dict[str, str] | None = None,
        cache: dict[tuple[str, str, str, str], MetricValueResolution | None] | None = None,
        stack: tuple[tuple[str, str, str, str], ...] = (),
    ) -> MetricValueResolution | None:
        if metric_code not in self.metric_by_code:
            raise BusinessMetricModelError(f"Unknown metric: {metric_code}")
        slice_key = canonical_slice_key(slice_filters)
        cache = cache or {}
        cache_key = (metric_code, period, scenario, slice_key)
        if cache_key in cache:
            return cache[cache_key]
        if cache_key in stack:
            path = " -> ".join(
                f"{metric}[{period}|{scenario}|{slice_key}]"
                for metric, period, scenario, slice_key in (*stack, cache_key)
            )
            raise BusinessMetricModelError(f"Cyclic metric derivation detected: {path}")
        observation = self.observation_by_key.get((metric_code, period, scenario, slice_key))
        if observation is not None:
            resolved = MetricValueResolution(
                metric_code=metric_code,
                period=period,
                scenario=scenario,
                slice_key=slice_key,
                value=float(observation.value),
                source="observation",
                relation_id=None,
            )
            cache[cache_key] = resolved
            return resolved

        active_formulas = [
            relation
            for relation in self._matching_relations(metric_code, slice_filters)
            if relation.relation_kind == "formula" and relation.output_mode == "level"
        ]
        if not active_formulas:
            cache[cache_key] = None
            return None
        relation = self._select_formula_relation(metric_code, slice_filters, active_formulas)
        alias_values: dict[str, float] = {}
        for alias, binding in relation.inputs.items():
            if binding.value_mode != "level":
                cache[cache_key] = None
                return None
            bound_period = shift_period(period, binding.lag_periods)
            bound = self.resolve_metric_level(
                binding.metric_code,
                period=bound_period,
                scenario=scenario,
                slice_filters={**(slice_filters or {}), **binding.slice_overrides},
                cache=cache,
                stack=(*stack, cache_key),
            )
            if bound is None:
                cache[cache_key] = None
                return None
            alias_values[alias] = bound.value
        value = self._programs[relation.id].evaluate(alias_values)
        resolved = MetricValueResolution(
            metric_code=metric_code,
            period=period,
            scenario=scenario,
            slice_key=slice_key,
            value=value,
            source="derived",
            relation_id=relation.id,
        )
        cache[cache_key] = resolved
        return resolved

    def _alias_state_for_relation(
        self,
        relation: RelationDefinition,
        *,
        period: str,
        baseline_period: str,
        scenario: str,
        slice_filters: dict[str, str],
    ) -> tuple[dict[str, float], dict[str, float], list[str], dict[str, str]]:
        current_alias_values: dict[str, float] = {}
        baseline_alias_values: dict[str, float] = {}
        missing_inputs: list[str] = []
        alias_to_metric: dict[str, str] = {}
        cache: dict[tuple[str, str, str, str], MetricValueResolution | None] = {}
        for alias, binding in relation.inputs.items():
            alias_to_metric[alias] = binding.metric_code
            current_period = shift_period(period, binding.lag_periods)
            previous_period = shift_period(baseline_period, binding.lag_periods)
            current = self.resolve_metric_level(
                binding.metric_code,
                period=current_period,
                scenario=scenario,
                slice_filters={**slice_filters, **binding.slice_overrides},
                cache=cache,
            )
            baseline = self.resolve_metric_level(
                binding.metric_code,
                period=previous_period,
                scenario=scenario,
                slice_filters={**slice_filters, **binding.slice_overrides},
                cache=cache,
            )
            if current is None or baseline is None:
                if binding.required:
                    missing_inputs.append(alias)
                continue
            current_value = current.value
            baseline_value = baseline.value
            if binding.value_mode == "level":
                current_alias_values[alias] = current_value
                baseline_alias_values[alias] = baseline_value
            elif binding.value_mode == "difference":
                current_alias_values[alias] = current_value - baseline_value
                baseline_alias_values[alias] = 0.0
            else:
                if math.isclose(baseline_value, 0.0, abs_tol=1e-12):
                    if binding.required:
                        missing_inputs.append(alias)
                    continue
                current_alias_values[alias] = (current_value - baseline_value) / baseline_value
                baseline_alias_values[alias] = 0.0
        return current_alias_values, baseline_alias_values, missing_inputs, alias_to_metric

    def _explain_relation_delta(
        self,
        relation: RelationDefinition,
        *,
        period: str,
        baseline_period: str,
        scenario: str,
        slice_filters: dict[str, str],
    ) -> RelationExplanation | None:
        current_alias_values, baseline_alias_values, missing_inputs, alias_to_metric = self._alias_state_for_relation(
            relation,
            period=period,
            baseline_period=baseline_period,
            scenario=scenario,
            slice_filters=slice_filters,
        )
        if missing_inputs:
            return None
        program = self._programs[relation.id]
        predicted_current = program.evaluate(current_alias_values)
        predicted_baseline = program.evaluate(baseline_alias_values)
        alias_contributions = self._shapley_delta(program, baseline_alias_values, current_alias_values)
        grouped: dict[str, float] = defaultdict(float)
        for alias, contribution in alias_contributions.items():
            grouped[alias_to_metric[alias]] += contribution
        contributions = [
            {
                "metric_code": metric_code,
                "contribution": round(contribution, 10),
            }
            for metric_code, contribution in sorted(grouped.items(), key=lambda item: abs(item[1]), reverse=True)
        ]
        return RelationExplanation(
            relation_id=relation.id,
            relation_kind=relation.relation_kind,
            output_mode=relation.output_mode,
            target_metric_code=relation.target_metric_code,
            explained_delta=predicted_current - predicted_baseline,
            predicted_current=predicted_current,
            predicted_baseline=predicted_baseline,
            confidence=relation.confidence,
            input_contributions=contributions,
            missing_inputs=missing_inputs,
        )

    def _matching_relations(
        self,
        metric_code: str,
        slice_filters: dict[str, str] | None,
    ) -> list[RelationDefinition]:
        normalized_slice = slice_filters or {}
        matched = [
            relation
            for relation in self.relations_by_target.get(metric_code, [])
            if relation.status == "active" and self._relation_matches_slice(relation, normalized_slice)
        ]
        return sorted(matched, key=lambda item: (-len(item.applies_to), item.id))

    def _relation_matches_slice(
        self,
        relation: RelationDefinition,
        slice_filters: dict[str, str] | None,
    ) -> bool:
        normalized_slice = slice_filters or {}
        return all(normalized_slice.get(key) == value for key, value in relation.applies_to.items())

    def _select_formula_relation(
        self,
        metric_code: str,
        slice_filters: dict[str, str] | None,
        active_formulas: list[RelationDefinition] | None = None,
    ) -> RelationDefinition:
        candidates = active_formulas or [
            relation
            for relation in self._matching_relations(metric_code, slice_filters)
            if relation.relation_kind == "formula" and relation.output_mode == "level"
        ]
        if not candidates:
            raise BusinessMetricModelError(f"No active formula relation matched {metric_code}.")
        best_specificity = len(candidates[0].applies_to)
        best_candidates = [relation for relation in candidates if len(relation.applies_to) == best_specificity]
        if len(best_candidates) > 1:
            raise BusinessMetricModelError(
                f"Ambiguous active formula relations for {metric_code} and slice {slice_filters or {}}: "
                f"{[item.id for item in best_candidates]}"
            )
        return best_candidates[0]

    def _predict_relation_value(
        self,
        relation: RelationDefinition,
        *,
        period: str,
        scenario: str,
        slice_filters: dict[str, str],
    ) -> float | None:
        alias_values: dict[str, float] = {}
        cache: dict[tuple[str, str, str, str], MetricValueResolution | None] = {}
        for alias, binding in relation.inputs.items():
            if binding.value_mode != "level":
                return None
            bound = self.resolve_metric_level(
                binding.metric_code,
                period=shift_period(period, binding.lag_periods),
                scenario=scenario,
                slice_filters={**slice_filters, **binding.slice_overrides},
                cache=cache,
            )
            if bound is None:
                return None
            alias_values[alias] = bound.value
        return self._programs[relation.id].evaluate(alias_values)

    def _shapley_delta(
        self,
        program: ExpressionProgram,
        baseline_aliases: dict[str, float],
        current_aliases: dict[str, float],
    ) -> dict[str, float]:
        aliases = list(current_aliases)
        if not aliases:
            return {}
        baseline = {alias: baseline_aliases[alias] for alias in aliases}
        current = {alias: current_aliases[alias] for alias in aliases}
        if len(aliases) > 7:
            return {
                alias: program.evaluate({**baseline, alias: current[alias]}) - program.evaluate(baseline)
                for alias in aliases
            }
        contributions = {alias: 0.0 for alias in aliases}
        permutations = list(itertools.permutations(aliases))
        for ordering in permutations:
            state = dict(baseline)
            previous_value = program.evaluate(state)
            for alias in ordering:
                state[alias] = current[alias]
                next_value = program.evaluate(state)
                contributions[alias] += next_value - previous_value
                previous_value = next_value
        factor = 1.0 / len(permutations)
        return {alias: contribution * factor for alias, contribution in contributions.items()}

    def _validate(self) -> None:
        if len(self.metric_by_code) != len(self.bundle.metrics):
            raise BusinessMetricModelError("Metric codes must be unique.")
        if len(self.relation_by_id) != len(self.bundle.relations):
            raise BusinessMetricModelError("Relation ids must be unique.")
        formula_targets: dict[tuple[str, str], list[str]] = defaultdict(list)
        for relation in self.bundle.relations:
            if relation.target_metric_code not in self.metric_by_code:
                raise BusinessMetricModelError(
                    f"Relation {relation.id} targets unknown metric {relation.target_metric_code}."
                )
            program = self._programs[relation.id]
            alias_names = set(relation.inputs)
            unknown_names = set(program.names) - alias_names
            unused_bindings = alias_names - set(program.names)
            if unknown_names:
                raise BusinessMetricModelError(
                    f"Relation {relation.id} expression references undeclared inputs: {sorted(unknown_names)}."
                )
            if unused_bindings:
                raise BusinessMetricModelError(
                    f"Relation {relation.id} declares unused inputs: {sorted(unused_bindings)}."
                )
            for binding in relation.inputs.values():
                if binding.metric_code not in self.metric_by_code:
                    raise BusinessMetricModelError(
                        f"Relation {relation.id} references unknown metric {binding.metric_code}."
                    )
            if relation.status == "active" and relation.relation_kind == "formula":
                formula_targets[(relation.target_metric_code, canonical_slice_key(relation.applies_to))].append(relation.id)
        duplicated_targets = {
            key: value
            for key, value in formula_targets.items()
            if len(value) > 1
        }
        if duplicated_targets:
            raise BusinessMetricModelError(
                "At most one active formula relation is allowed per target metric and applicability scope: "
                f"{duplicated_targets}"
            )
        for observation in self.bundle.observations:
            if observation.metric_code not in self.metric_by_code:
                raise BusinessMetricModelError(
                    f"Observation references unknown metric {observation.metric_code}."
                )
        self._validate_formula_cycles()

    def _validate_formula_cycles(self) -> None:
        adjacency: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for relation in self.bundle.relations:
            if relation.status != "active" or relation.relation_kind != "formula":
                continue
            target_key = (relation.target_metric_code, canonical_slice_key(relation.applies_to))
            for binding in relation.inputs.values():
                source_slice = {**relation.applies_to, **binding.slice_overrides}
                source_key = (binding.metric_code, canonical_slice_key(source_slice))
                adjacency[target_key].append(source_key)

        visiting: set[tuple[str, str]] = set()
        visited: set[tuple[str, str]] = set()

        def visit(state_key: tuple[str, str]) -> None:
            if state_key in visited:
                return
            if state_key in visiting:
                raise BusinessMetricModelError(
                    f"Formula cycle detected around metric {state_key[0]} for slice {state_key[1]}."
                )
            visiting.add(state_key)
            for upstream in adjacency.get(state_key, []):
                visit(upstream)
            visiting.remove(state_key)
            visited.add(state_key)

        for state_key in adjacency:
            visit(state_key)


def bundle_to_dict(bundle: BusinessMetricModelBundle) -> dict[str, Any]:
    return bundle.model_dump(mode="json")
