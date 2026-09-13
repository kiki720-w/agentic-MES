"""Deterministic evidence for common manufacturing read-only questions."""

import math
import re
from decimal import Decimal
from typing import Any

VERSION = "manufacturing-grounding-v1"
SOURCE_NAMES = frozenset({"ERP", "MES", "WMS", "QMS"})


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return Decimal(str(value))


def _json_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _calculation(
    evidence_id: str, formula: str, inputs: dict[str, Any], result: Decimal
) -> dict[str, Any]:
    return {
        "id": evidence_id,
        "kind": "CALCULATION",
        "formula": formula,
        "inputs": inputs,
        "result": _json_number(result),
    }


def deterministic_evidence(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply an allowlisted formula registry; never evaluate model-provided expressions."""
    evidence: list[dict[str, Any]] = []
    summary = facts.get("summary")
    if isinstance(summary, dict):
        evidence.append({
            "id": "operational-snapshot-summary", "kind": "SNAPSHOT_SUMMARY",
            "result": summary,
            "rule": "Counts were produced by deterministic aggregation before model inference.",
        })
    setup = _number(facts.get("setupMinutes"))
    planned = _number(facts.get("plannedQuantity"))
    completed = _number(facts.get("completedQuantity"))
    per_unit = _number(facts.get("minutesPerUnit"))
    if None not in (setup, planned, completed, per_unit):
        remaining = max(Decimal(0), planned - completed)  # type: ignore[operator]
        evidence.append(_calculation(
            "remaining-demand-minutes",
            "setupMinutes + max(0, plannedQuantity - completedQuantity) * minutesPerUnit",
            {"setupMinutes": facts["setupMinutes"], "plannedQuantity": facts["plannedQuantity"],
             "completedQuantity": facts["completedQuantity"],
             "minutesPerUnit": facts["minutesPerUnit"]},
            setup + remaining * per_unit,  # type: ignore[operator]
        ))

    demand = _number(facts.get("demandMinutes"))
    capacity = _number(facts.get("normalCapacityMinutes"))
    if demand is not None and capacity is not None:
        evidence.append(_calculation(
            "normal-capacity-shortage-minutes", "max(0, demandMinutes - normalCapacityMinutes)",
            {"demandMinutes": facts["demandMinutes"],
             "normalCapacityMinutes": facts["normalCapacityMinutes"]},
            max(Decimal(0), demand - capacity),
        ))

    seconds = _number(facts.get("secondsPerUnit"))
    quantity = _number(facts.get("quantity"))
    if seconds is not None and quantity is not None:
        evidence.append(_calculation(
            "total-minutes-from-seconds", "secondsPerUnit * quantity / 60",
            {"secondsPerUnit": facts["secondsPerUnit"], "quantity": facts["quantity"]},
            seconds * quantity / Decimal(60),
        ))

    sources = {
        name: value for name, value in facts.items()
        if name.upper() in SOURCE_NAMES and isinstance(value, dict)
    }
    source_names = sorted(sources)
    conflicts: list[dict[str, Any]] = []
    for index, left_name in enumerate(source_names):
        for right_name in source_names[index + 1:]:
            left, right = sources[left_name], sources[right_name]
            identity_fields = [key for key in ("code", "id", "workOrderId") if key in left and key in right]
            if identity_fields and any(left[key] != right[key] for key in identity_fields):
                continue
            for field in sorted(left.keys() & right.keys() - set(identity_fields)):
                if left[field] != right[field]:
                    conflicts.append({
                        "field": field,
                        "sources": {left_name: left[field], right_name: right[field]},
                    })
    if conflicts:
        evidence.append({
            "id": "unresolved-source-conflicts", "kind": "SOURCE_CONFLICT",
            "result": "需核对来源", "conflicts": conflicts,
            "rule": "No source wins without an explicit authority/version policy.",
        })

    permissions = facts.get("permissions")
    if isinstance(permissions, list) and all(isinstance(value, str) for value in permissions):
        normalized = {value.upper() for value in permissions}
        evidence.append({
            "id": "explicit-permissions", "kind": "AUTHORIZATION",
            "result": {"canApprove": "APPROVE" in normalized,
                       "canPublish": "PUBLISH" in normalized},
            "rule": "Capabilities not explicitly listed are denied.",
        })
    return evidence


def _counts(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        value = str(item.get(field, "UNKNOWN"))
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def compact_operational_snapshot(
    question: str,
    orders: list[dict[str, Any]],
    equipment: list[dict[str, Any]],
    inspections: list[dict[str, Any]],
    *,
    retrieval_limit: int = 500,
) -> dict[str, Any]:
    """Aggregate the full retrieved set and send only question-relevant projections."""
    lowered = question.casefold()
    order_words = ("工单", "订单", "交期", "生产", "暂停", "挂起")
    equipment_words = ("设备", "机床", "报警", "故障", "停机", "主轴")
    quality_words = ("质量", "质检", "检验", "隔离", "缺陷", "返工")
    categories = {
        "orders": any(word in lowered for word in order_words),
        "equipment": any(word in lowered for word in equipment_words),
        "quality": any(word in lowered for word in quality_words),
    }
    if not any(categories.values()):
        categories = {key: True for key in categories}

    status_terms = {
        "SUSPENDED": ("暂停", "挂起"), "DRAFT": ("草稿",),
        "RELEASED": ("已下达", "已释放"), "IN_PROGRESS": ("进行中", "生产中"),
    }
    equipment_terms = {
        "ALARM": ("报警",), "DOWN": ("故障", "停机"), "OFFLINE": ("离线",),
        "RUNNING": ("运行",), "UNKNOWN": ("未知",),
    }
    quality_terms = {
        "QUARANTINED": ("隔离",), "OPEN": ("待检", "检验"),
        "FAILED": ("不合格", "失败"), "PASSED": ("合格", "通过"),
    }

    def selected(items: list[dict[str, Any]], terms: dict[str, tuple[str, ...]],
                 field: str, code_fields: tuple[str, ...]) -> list[dict[str, Any]]:
        explicit = [item for item in items if any(
            isinstance(item.get(key), str) and re.search(
                rf"(?<![A-Za-z0-9-]){re.escape(item[key])}(?![A-Za-z0-9-])",
                question,
                re.IGNORECASE,
            )
            for key in code_fields
        )]
        wanted = {state for state, words in terms.items() if any(word in lowered for word in words)}
        if field == "state" and "异常" in lowered:
            wanted.update({"ALARM", "DOWN", "OFFLINE"})
        filtered = explicit or ([item for item in items if item.get(field) in wanted] if wanted else items)
        return filtered[:12]

    chosen_orders = selected(orders, status_terms, "status", ("humanCode", "productionOrderId"))
    chosen_equipment = selected(equipment, equipment_terms, "state", ("code",))
    chosen_inspections = selected(inspections, quality_terms, "status", ("inspectionId",))

    def order_view(item: dict[str, Any]) -> dict[str, Any]:
        view = {key: item.get(key) for key in (
            "workOrderId", "humanCode", "productionOrderId", "quantity", "dueAt",
            "priority", "status", "version", "dataFreshness",
        )}
        operations = item.get("operations")
        view["operations"] = [
            {key: operation.get(key) for key in (
                "sequence", "operationCode", "operationName", "workCenterId",
                "plannedQuantity", "status", "assignedResourceId", "goodQuantity",
                "scrapQuantity",
            )}
            for operation in operations[:20]
            if isinstance(operation, dict)
        ] if isinstance(operations, list) else []
        return view

    def equipment_view(item: dict[str, Any]) -> dict[str, Any]:
        return {key: item.get(key) for key in (
            "equipmentId", "code", "name", "workCenterId", "state", "lastSeenAt",
            "spindleLoadPercent", "temperatureCelsius", "alarmCode", "downtimeReason",
        )}

    def inspection_view(item: dict[str, Any]) -> dict[str, Any]:
        return {key: item.get(key) for key in (
            "inspectionId", "workOrderId", "operationSequence", "status", "result",
            "defectCode", "gaugeId", "calibrationDueAt", "measurementRecordedAt",
        )}

    return {
        "summary": {
            "workOrders": {"retrieved": len(orders), "statusCounts": _counts(orders, "status")},
            "equipment": {"retrieved": len(equipment), "stateCounts": _counts(equipment, "state")},
            "qualityInspections": {
                "retrieved": len(inspections), "statusCounts": _counts(inspections, "status")
            },
            "retrievalLimitPerType": retrieval_limit,
            "possiblyTruncated": any(len(items) >= retrieval_limit
                                     for items in (orders, equipment, inspections)),
        },
        "workOrders": [order_view(item) for item in chosen_orders] if categories["orders"] else [],
        "equipment": [equipment_view(item) for item in chosen_equipment]
        if categories["equipment"] else [],
        "qualityInspections": [inspection_view(item) for item in chosen_inspections]
        if categories["quality"] else [],
        "selection": {"maximumRecordsPerType": 12, "questionRelevant": True},
    }


def grounded_request(question: str, facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": question,
        "observedSnapshot": facts,
        "deterministicEvidence": {
            "version": VERSION,
            "items": deterministic_evidence(facts),
        },
    }
