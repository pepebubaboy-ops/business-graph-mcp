from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from business_graph_core.models import (
    BusinessNode,
    BusinessRelation,
    EvidenceQuality,
    EvidenceRef,
    NodeType,
    RelationStatus,
    RelationType,
)
from business_graph_core.parsers.base import ParsedFile

EDGE_TYPE_MAP = {
    "driver": RelationType.DRIVES,
    "inverse_driver": RelationType.INVERSELY_DRIVES,
    "component": RelationType.COMPONENT_OF,
    "dependency": RelationType.DEPENDS_ON,
    "depends_on": RelationType.DEPENDS_ON,
}

POLARITY_MAP = {
    RelationType.DRIVES: 1,
    RelationType.INVERSELY_DRIVES: -1,
    RelationType.COMPONENT_OF: 1,
    RelationType.DEPENDS_ON: 0,
}


@dataclass(frozen=True)
class ExtractedRelations:
    nodes: list[BusinessNode]
    relations: list[BusinessRelation]


class ExplicitRulesExtractor:
    """Extract confirmed metric relations from dependency_rules rows."""

    def extract(
        self, parsed_file: ParsedFile, *, workspace_id: str = "default"
    ) -> ExtractedRelations:
        nodes_by_id: dict[str, BusinessNode] = {}
        relations: list[BusinessRelation] = []

        for row in self._iter_rule_rows(parsed_file):
            source_code = str(row["source_metric_code"]).strip()
            target_code = str(row["target_metric_code"]).strip()
            edge_type_raw = str(row.get("edge_type") or "dependency").strip().lower()
            relation_type = EDGE_TYPE_MAP.get(edge_type_raw, RelationType.DEPENDS_ON)
            strength = self._safe_float(row.get("strength"), default=0.85)
            reason = str(row.get("reason") or "").strip() or None

            source_id = f"metric:{source_code}"
            target_id = f"metric:{target_code}"
            nodes_by_id.setdefault(
                source_id,
                BusinessNode(
                    id=source_id,
                    type=NodeType.METRIC,
                    name=source_code,
                    workspace_id=workspace_id,
                    source_refs=[parsed_file.file_id],
                ),
            )
            nodes_by_id.setdefault(
                target_id,
                BusinessNode(
                    id=target_id,
                    type=NodeType.METRIC,
                    name=target_code,
                    workspace_id=workspace_id,
                    source_refs=[parsed_file.file_id],
                ),
            )

            evidence = EvidenceRef(
                source_file_id=parsed_file.file_id,
                source_name=parsed_file.source_name,
                source_type=parsed_file.source_type,
                locator={"sheet": row.get("_sheet"), "row": row.get("_row")},
                method=EvidenceQuality.EXPLICIT_RULE,
                quote_or_value=reason,
            )
            relations.append(
                BusinessRelation(
                    from_id=source_id,
                    to_id=target_id,
                    type=relation_type,
                    workspace_id=workspace_id,
                    status=RelationStatus.CONFIRMED,
                    polarity=POLARITY_MAP.get(relation_type, 0),
                    strength=strength,
                    confidence=max(0.5, min(1.0, strength)),
                    evidence_refs=[evidence],
                    explanation=reason,
                )
            )

        return ExtractedRelations(nodes=list(nodes_by_id.values()), relations=relations)

    def _iter_rule_rows(self, parsed_file: ParsedFile) -> Iterable[dict[str, object]]:
        for sheet in parsed_file.sheets:
            yield from sheet.dependency_rule_rows

    def _safe_float(self, value: object, *, default: float) -> float:
        try:
            result = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, result))
