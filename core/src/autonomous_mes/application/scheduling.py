from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from autonomous_mes.domain.equipment import EquipmentState
from autonomous_mes.domain.errors import IdempotencyConflict, NotFound, ValidationError
from autonomous_mes.domain.scheduling import (
    PlanningResource,
    SchedulePlan,
    SchedulingSnapshot,
)
from autonomous_mes.domain.work_order import (
    FrozenRevisions,
    OperationStatus,
    ProductionOperation,
    WorkOrder,
    WorkOrderStatus,
)

from .ports import SchedulingStore


@dataclass(frozen=True)
class RegisterPlanningResourceCommand:
    code: str
    name: str
    resource_type: str
    workshop_id: str
    work_center_id: str
    daily_capacity_minutes: float
    overtime_capacity_minutes: float
    capability_codes: list[str]
    actor_id: str
    correlation_id: str


@dataclass(frozen=True)
class UpdatePlanningResourceCommand:
    resource_id: str
    expected_version: int
    name: str
    work_center_id: str
    daily_capacity_minutes: float
    overtime_capacity_minutes: float
    capability_codes: list[str]
    active: bool
    actor_id: str
    correlation_id: str


@dataclass(frozen=True)
class GenerateScheduleCommand:
    workshop_id: str
    horizon_start: date
    horizon_days: int
    use_overtime: bool
    default_minutes_per_unit: float
    operation_rates: dict[str, float]
    actor_id: str
    correlation_id: str
    trigger_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class IngestSchedulingSnapshotCommand:
    source_system: str
    workshop_id: str
    source_revision: str
    observed_at: datetime
    payload: dict[str, Any]
    actor_id: str
    correlation_id: str


@dataclass(frozen=True)
class _Candidate:
    resource_id: str
    code: str
    name: str
    resource_type: str
    work_center_id: str
    normal_capacity: float
    overtime_capacity: float
    capabilities: tuple[str, ...]

    def capacity(self, use_overtime: bool) -> float:
        return self.overtime_capacity if use_overtime else self.normal_capacity


class SchedulingApplicationService:
    def __init__(self, store: SchedulingStore) -> None:
        self._store = store

    def register_resource(self, command: RegisterPlanningResourceCommand) -> dict[str, Any]:
        resource, event = PlanningResource.create(
            command.code,
            command.name,
            command.resource_type,
            command.workshop_id,
            command.work_center_id,
            command.daily_capacity_minutes,
            command.overtime_capacity_minutes,
            command.capability_codes,
            command.actor_id,
            command.correlation_id,
        )
        self._store.add_planning_resource_atomically(resource, event)
        return serialize_planning_resource(resource)

    def list_resources(self, workshop_id: str) -> list[dict[str, Any]]:
        return [
            serialize_planning_resource(item)
            for item in self._store.list_planning_resources(workshop_id)
        ]

    def update_resource(self, command: UpdatePlanningResourceCommand) -> dict[str, Any]:
        current = self._store.get_planning_resource(command.resource_id)
        if current is None:
            raise NotFound("planning resource not found")
        changed, event = current.update(
            command.expected_version,
            command.name,
            command.work_center_id,
            command.daily_capacity_minutes,
            command.overtime_capacity_minutes,
            command.capability_codes,
            command.active,
            command.actor_id,
            command.correlation_id,
        )
        self._store.update_planning_resource_atomically(changed, current.version, event)
        return serialize_planning_resource(changed)

    def generate(self, command: GenerateScheduleCommand) -> dict[str, Any]:
        if command.default_minutes_per_unit <= 0:
            raise ValidationError("default minutes per unit must be greater than zero")
        if not 1 <= command.horizon_days <= 31:
            raise ValidationError("schedule horizon must be between 1 and 31 working days")
        if any(value <= 0 for value in command.operation_rates.values()):
            raise ValidationError("operation rates must be greater than zero")

        days = _working_days(command.horizon_start, command.horizon_days)
        snapshot = self._store.get_latest_scheduling_snapshot(command.workshop_id)
        operation_demands = _snapshot_operation_demands(snapshot) if snapshot else {}
        resources = self._candidates(command.workshop_id, snapshot)
        orders, order_constraints = self._orders(command.workshop_id, snapshot)
        orders.sort(key=lambda item: (-item.priority, item.due_at, item.created_at))
        assignments: list[dict[str, Any]] = []
        shortages: list[dict[str, Any]] = []
        loads: dict[tuple[str, date], float] = {}
        scheduled_orders: set[str] = set()
        late_orders: set[str] = set()
        frozen_operations = 0

        for order in orders:
            if not order.operations:
                shortages.append(_missing_route_shortage(order))
                continue
            blocking_reason = order_constraints.get(order.work_order_id)
            if blocking_reason:
                shortages.extend(_blocked_order_shortages(order, command, blocking_reason))
                continue
            earliest_index = 0
            for operation in order.operations:
                remaining_quantity = max(
                    0,
                    operation.planned_quantity - operation.good_quantity - operation.scrap_quantity,
                )
                if operation.status is OperationStatus.COMPLETED or remaining_quantity == 0:
                    continue
                if order.status is WorkOrderStatus.SUSPENDED:
                    shortages.append(
                        _shortage(order, operation, remaining_quantity, 0, "工单处于暂停状态")
                    )
                    continue
                if operation.status is not OperationStatus.PENDING:
                    frozen_operations += 1
                    earliest_index = min(earliest_index + 1, len(days))
                    continue

                rate, setup_minutes, demand_source = operation_demands.get(
                    (order.work_order_id, operation.sequence),
                    (_rate_for(order, operation.operation_code, command), 0.0, "FALLBACK_RATE"),
                )
                required = remaining_quantity * rate + setup_minutes
                candidates = _matching_candidates(
                    resources, operation.work_center_id, operation.operation_code
                )
                if not candidates:
                    shortages.append(
                        _shortage(
                            order,
                            operation,
                            remaining_quantity,
                            required,
                            "没有可用且能力匹配的人员、工作单元或在线设备",
                        )
                    )
                    continue

                options: list[tuple[float, int, float, _Candidate, list[tuple[int, float]]]] = []
                for candidate in candidates:
                    candidate_plan, remainder, finish_index = _attempt(
                        candidate,
                        required,
                        earliest_index,
                        days,
                        loads,
                        command.use_overtime,
                    )
                    existing = sum(
                        value
                        for (resource_id, _), value in loads.items()
                        if resource_id == candidate.resource_id
                    )
                    options.append((remainder, finish_index, existing, candidate, candidate_plan))
                remainder, finish_index, _, selected, allocation = min(
                    options, key=lambda item: (item[0], item[1], item[2], item[3].code)
                )
                setup_remaining = setup_minutes
                productive_minutes_allocated = 0.0
                for day_index, minutes in allocation:
                    production_day = days[day_index]
                    loads[(selected.resource_id, production_day)] = (
                        loads.get((selected.resource_id, production_day), 0) + minutes
                    )
                    is_late = production_day > order.due_at.date()
                    if is_late:
                        late_orders.add(order.work_order_id)
                    setup_on_day = min(setup_remaining, minutes)
                    setup_remaining -= setup_on_day
                    productive_minutes = max(0.0, minutes - setup_on_day)
                    productive_minutes_allocated += productive_minutes
                    assignments.append(
                        {
                            "assignmentId": str(uuid4()),
                            "workOrderId": order.work_order_id,
                            "workOrderCode": order.human_code,
                            "productRevisionId": order.revisions.product_revision_id,
                            "operationSequence": operation.sequence,
                            "operationCode": operation.operation_code,
                            "operationName": operation.operation_name,
                            "workCenterId": operation.work_center_id,
                            "resourceId": selected.resource_id,
                            "resourceCode": selected.code,
                            "resourceName": selected.name,
                            "resourceType": selected.resource_type,
                            "productionDate": production_day.isoformat(),
                            "plannedWorkMinutes": round(minutes, 3),
                            "plannedQuantity": round(productive_minutes / rate, 3),
                            "minutesPerUnit": round(rate, 3),
                            "setupMinutes": round(setup_on_day, 3),
                            "demandSource": demand_source,
                            "dueAt": order.due_at.isoformat(),
                            "deliveryStatus": "LATE" if is_late else "ON_TIME",
                            "rationale": "能力匹配；候选资源中优先完整排入并最早完成",
                        }
                    )
                if allocation:
                    scheduled_orders.add(order.work_order_id)
                    earliest_index = min(finish_index + 1, len(days))
                if remainder > 0.001:
                    remaining_after_allocation = max(
                        0.0, remaining_quantity - productive_minutes_allocated / rate
                    )
                    shortages.append(
                        _shortage(
                            order,
                            operation,
                            round(remaining_after_allocation, 3),
                            remainder,
                            "滚动计划窗口内剩余产能不足",
                        )
                    )

        total_minutes = round(sum(item["plannedWorkMinutes"] for item in assignments), 3)
        available_capacity = round(
            sum(item.capacity(command.use_overtime) for item in resources) * len(days), 3
        )
        metrics = {
            "candidateOrderCount": len(orders),
            "scheduledOrderCount": len(scheduled_orders),
            "assignmentCount": len(assignments),
            "shortageCount": len(shortages),
            "lateOrderCount": len(late_orders),
            "frozenOperationCount": frozen_operations,
            "plannedWorkMinutes": total_minutes,
            "availableCapacityMinutes": available_capacity,
            "capacityUtilizationPercent": round(total_minutes / available_capacity * 100, 1)
            if available_capacity
            else 0,
        }
        parameters = {
            "algorithm": "FINITE_CAPACITY_EARLIEST_FINISH_V1",
            "workingCalendar": "MONDAY_TO_SATURDAY",
            "defaultMinutesPerUnit": command.default_minutes_per_unit,
            "operationRates": command.operation_rates,
            "equipmentFallbackCapacityMinutes": 480,
            "workDemandFormula": "setupMinutes + remainingQuantity × minutesPerUnit",
            "inputSource": (
                {
                    "type": "EXTERNAL_SCHEDULING_SNAPSHOT",
                    "snapshotId": snapshot.snapshot_id,
                    "sourceSystem": snapshot.source_system,
                    "sourceRevision": snapshot.source_revision,
                    "observedAt": snapshot.observed_at.isoformat(),
                    "checksum": snapshot.checksum,
                }
                if snapshot
                else {"type": "SIMULATOR_PROJECTION"}
            ),
        }
        if command.trigger_context:
            parameters.update(command.trigger_context)
        plan, event = SchedulePlan.create_draft(
            command.workshop_id,
            command.horizon_start,
            command.horizon_days,
            command.use_overtime,
            parameters,
            assignments,
            shortages,
            metrics,
            command.actor_id,
            command.correlation_id,
        )
        self._store.add_schedule_plan_atomically(plan, event)
        return serialize_schedule_plan(plan)

    def ingest_snapshot(self, command: IngestSchedulingSnapshotCommand) -> dict[str, Any]:
        snapshot, event = SchedulingSnapshot.create(
            command.source_system,
            command.workshop_id,
            command.source_revision,
            command.observed_at,
            command.payload,
            command.actor_id,
            command.correlation_id,
        )
        existing = self._store.get_scheduling_snapshot(
            snapshot.source_system, snapshot.workshop_id, snapshot.source_revision
        )
        if existing is not None:
            if existing.checksum != snapshot.checksum:
                raise IdempotencyConflict(
                    "snapshot source revision already exists with different content"
                )
            return {**serialize_scheduling_snapshot(existing), "reused": True}
        self._store.add_scheduling_snapshot_atomically(snapshot, event)
        return {**serialize_scheduling_snapshot(snapshot), "reused": False}

    def latest_snapshot(
        self, workshop_id: str, *, include_payload: bool = False
    ) -> dict[str, Any] | None:
        snapshot = self._store.get_latest_scheduling_snapshot(workshop_id)
        if snapshot is None:
            return None
        result = serialize_scheduling_snapshot(snapshot)
        if include_payload:
            result["payload"] = snapshot.payload
        return result

    def list_plans(self, workshop_id: str, limit: int = 30) -> list[dict[str, Any]]:
        return [
            serialize_schedule_plan(item)
            for item in self._store.list_schedule_plans(workshop_id, limit)
        ]

    def submit(self, plan_id: str, version: int, actor_id: str) -> dict[str, Any]:
        current = self._require(plan_id)
        changed, event = current.submit(version, actor_id, str(uuid4()))
        self._store.update_schedule_plan_atomically(changed, current.record_version, event)
        return serialize_schedule_plan(changed)

    def move_assignment(
        self,
        plan_id: str,
        assignment_id: str,
        version: int,
        target_resource_id: str,
        production_date: date,
        reason: str,
        actor_id: str,
    ) -> dict[str, Any]:
        current = self._require(plan_id)
        assignment = next(
            (item for item in current.assignments if item["assignmentId"] == assignment_id),
            None,
        )
        if assignment is None:
            raise ValidationError("schedule assignment not found")
        target = next(
            (
                item
                for item in self._candidates(current.workshop_id)
                if item.resource_id == target_resource_id
            ),
            None,
        )
        if target is None:
            raise ValidationError("target planning resource is unavailable")
        if target.work_center_id != assignment["workCenterId"]:
            raise ValidationError("target resource belongs to a different work center")
        if (
            target.capabilities
            and "*" not in target.capabilities
            and assignment["operationCode"].upper() not in target.capabilities
        ):
            raise ValidationError("target resource does not match operation capability")
        if production_date not in _working_days(current.horizon_start, current.horizon_days):
            raise ValidationError("target date is outside the plan working-day horizon")
        previous_dates = [
            date.fromisoformat(item["productionDate"])
            for item in current.assignments
            if item["workOrderId"] == assignment["workOrderId"]
            and item["operationSequence"] < assignment["operationSequence"]
        ]
        next_dates = [
            date.fromisoformat(item["productionDate"])
            for item in current.assignments
            if item["workOrderId"] == assignment["workOrderId"]
            and item["operationSequence"] > assignment["operationSequence"]
        ]
        if previous_dates and production_date <= max(previous_dates):
            raise ValidationError("target date would violate previous operation precedence")
        if next_dates and production_date >= min(next_dates):
            raise ValidationError("target date would violate next operation precedence")
        other_load = sum(
            float(item["plannedWorkMinutes"])
            for item in current.assignments
            if item["assignmentId"] != assignment_id
            and item["resourceId"] == target_resource_id
            and item["productionDate"] == production_date.isoformat()
        )
        if (
            other_load + float(assignment["plannedWorkMinutes"])
            > target.capacity(current.use_overtime) + 0.001
        ):
            raise ValidationError("target resource daily capacity would be exceeded")
        changed, event = current.move_assignment(
            version,
            assignment_id,
            {
                "resourceId": target.resource_id,
                "resourceCode": target.code,
                "resourceName": target.name,
                "resourceType": target.resource_type,
            },
            production_date,
            reason,
            actor_id,
            str(uuid4()),
        )
        self._store.update_schedule_plan_atomically(changed, current.record_version, event)
        return serialize_schedule_plan(changed)

    def approve(self, plan_id: str, version: int, actor_id: str, reason: str) -> dict[str, Any]:
        current = self._require(plan_id)
        changed, event = current.approve(version, actor_id, reason, str(uuid4()))
        self._store.update_schedule_plan_atomically(changed, current.record_version, event)
        return serialize_schedule_plan(changed)

    def publish(self, plan_id: str, version: int, actor_id: str) -> dict[str, Any]:
        current = self._require(plan_id)
        if any(
            item.plan_id != current.plan_id and item.status.value == "PUBLISHED"
            for item in self._store.list_schedule_plans(current.workshop_id, 100)
        ):
            raise ValidationError("withdraw the current published workshop plan before publishing")
        changed, event = current.publish(version, actor_id, str(uuid4()))
        self._store.update_schedule_plan_atomically(changed, current.record_version, event)
        return serialize_schedule_plan(changed)

    def withdraw(self, plan_id: str, version: int, actor_id: str, reason: str) -> dict[str, Any]:
        current = self._require(plan_id)
        changed, event = current.withdraw(version, actor_id, reason, str(uuid4()))
        self._store.update_schedule_plan_atomically(changed, current.record_version, event)
        return serialize_schedule_plan(changed)

    def _require(self, plan_id: str) -> SchedulePlan:
        plan = self._store.get_schedule_plan(plan_id)
        if plan is None:
            raise NotFound("schedule plan not found")
        return plan

    def _candidates(
        self, workshop_id: str, snapshot: SchedulingSnapshot | None = None
    ) -> list[_Candidate]:
        resources = [
            _Candidate(
                item.resource_id,
                item.code,
                item.name,
                item.resource_type.value,
                item.work_center_id,
                item.daily_capacity_minutes,
                item.overtime_capacity_minutes,
                item.capability_codes,
            )
            for item in self._store.list_planning_resources(workshop_id)
            if item.active
        ]
        equipment = [
            _Candidate(
                item.equipment_id,
                item.code,
                item.name,
                "EQUIPMENT",
                item.work_center_id,
                480,
                600,
                ("*",),
            )
            for item in self._store.list_equipment(limit=10_000)
            if item.workshop_id == workshop_id
            and item.state in {EquipmentState.IDLE, EquipmentState.RUNNING}
        ]
        external = _snapshot_resources(snapshot) if snapshot else []
        combined = {item.resource_id: item for item in resources + equipment}
        combined.update({item.resource_id: item for item in external})
        return list(combined.values())

    def _orders(
        self, workshop_id: str, snapshot: SchedulingSnapshot | None
    ) -> tuple[list[WorkOrder], dict[str, str]]:
        if snapshot is not None:
            return _snapshot_orders(snapshot)
        return (
            [
                item
                for item in self._store.list_work_orders(limit=10_000, include_test=False)
                if item.workshop_id == workshop_id
                and item.status
                in {
                    WorkOrderStatus.DRAFT,
                    WorkOrderStatus.RELEASED,
                    WorkOrderStatus.IN_PROGRESS,
                    WorkOrderStatus.SUSPENDED,
                }
            ],
            {},
        )


def _snapshot_orders(
    snapshot: SchedulingSnapshot,
) -> tuple[list[WorkOrder], dict[str, str]]:
    orders: list[WorkOrder] = []
    constraints: dict[str, str] = {}
    for source in snapshot.payload["workOrders"]:
        work_order_id = str(source["externalId"])
        order_status = WorkOrderStatus(str(source.get("status", "RELEASED")).upper())
        if order_status not in {
            WorkOrderStatus.DRAFT,
            WorkOrderStatus.RELEASED,
            WorkOrderStatus.IN_PROGRESS,
            WorkOrderStatus.SUSPENDED,
        }:
            continue
        operations = [
            ProductionOperation(
                int(operation["sequence"]),
                str(operation["operationCode"]),
                str(operation["operationName"]),
                str(operation["workCenterId"]),
                int(operation.get("plannedQuantity", source["quantity"])),
                OperationStatus(str(operation.get("status", "PENDING")).upper()),
                operation.get("assignedResourceId"),
                int(operation.get("goodQuantity", 0)),
                int(operation.get("scrapQuantity", 0)),
            )
            for operation in source.get("operations", [])
        ]
        created_at = datetime.fromisoformat(
            str(source.get("createdAt") or snapshot.observed_at.isoformat())
        )
        updated_at = datetime.fromisoformat(
            str(source.get("updatedAt") or snapshot.observed_at.isoformat())
        )
        orders.append(
            WorkOrder(
                work_order_id,
                str(source["code"]),
                str(source["productionOrderId"]),
                snapshot.workshop_id,
                int(source["quantity"]),
                datetime.fromisoformat(str(source["dueAt"])),
                int(source.get("priority", 50)),
                FrozenRevisions(
                    str(source["productRevisionId"]),
                    str(source["routingRevisionId"]),
                    str(source["bomRevisionId"]),
                    [str(item) for item in source.get("drawingRevisionIds", [])],
                ),
                order_status,
                int(source.get("version", 1)),
                created_at,
                updated_at,
                operations,
                [],
            )
        )
        if source.get("materialReady") is False:
            constraints[work_order_id] = "WMS 显示物料未齐套"
        elif source.get("qualityHold") is True:
            constraints[work_order_id] = "QMS 质量冻结尚未解除"
    return orders, constraints


def _snapshot_resources(snapshot: SchedulingSnapshot) -> list[_Candidate]:
    resources: list[_Candidate] = []
    for source in snapshot.payload["resources"]:
        resource_type = str(source["resourceType"]).upper()
        if not source.get("active", True):
            continue
        if resource_type == "EQUIPMENT" and str(source.get("state", "UNKNOWN")).upper() not in {
            "IDLE",
            "RUNNING",
        }:
            continue
        resources.append(
            _Candidate(
                str(source["externalId"]),
                str(source["code"]),
                str(source["name"]),
                resource_type,
                str(source["workCenterId"]),
                float(source["dailyCapacityMinutes"]),
                float(source.get("overtimeCapacityMinutes", source["dailyCapacityMinutes"])),
                tuple(str(item).upper() for item in source.get("capabilityCodes", [])),
            )
        )
    return resources


def _snapshot_operation_demands(
    snapshot: SchedulingSnapshot,
) -> dict[tuple[str, int], tuple[float, float, str]]:
    demands: dict[tuple[str, int], tuple[float, float, str]] = {}
    for order in snapshot.payload["workOrders"]:
        for operation in order.get("operations", []):
            minutes_per_unit = operation.get("minutesPerUnit")
            if minutes_per_unit is None:
                continue
            demands[(str(order["externalId"]), int(operation["sequence"]))] = (
                float(minutes_per_unit),
                float(operation.get("setupMinutes", 0)),
                "SNAPSHOT_PROCESS_STANDARD",
            )
    return demands


def _working_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() != 6:
            days.append(current)
        current += timedelta(days=1)
    return days


def _matching_candidates(
    candidates: list[_Candidate], work_center_id: str, operation_code: str
) -> list[_Candidate]:
    matching = [
        item
        for item in candidates
        if item.work_center_id == work_center_id
        and (
            not item.capabilities
            or "*" in item.capabilities
            or operation_code.upper() in item.capabilities
        )
    ]
    people = [item for item in matching if item.resource_type == "PERSON"]
    return people or matching


def _attempt(
    candidate: _Candidate,
    required: float,
    earliest_index: int,
    days: list[date],
    loads: dict[tuple[str, date], float],
    use_overtime: bool,
) -> tuple[list[tuple[int, float]], float, int]:
    remaining = required
    plan: list[tuple[int, float]] = []
    finish_index = len(days)
    capacity = candidate.capacity(use_overtime)
    for index in range(earliest_index, len(days)):
        available = max(0.0, capacity - loads.get((candidate.resource_id, days[index]), 0))
        if available <= 0:
            continue
        allocated = min(available, remaining)
        plan.append((index, allocated))
        remaining -= allocated
        finish_index = index
        if remaining <= 0.001:
            remaining = 0
            break
    return plan, remaining, finish_index


def _rate_for(order: WorkOrder, operation_code: str, command: GenerateScheduleCommand) -> float:
    specific = f"{order.revisions.product_revision_id}:{operation_code}".upper()
    return command.operation_rates.get(
        specific,
        command.operation_rates.get(operation_code.upper(), command.default_minutes_per_unit),
    )


def _shortage(
    order: WorkOrder,
    operation: Any,
    remaining_quantity: float,
    remaining_minutes: float,
    reason: str,
) -> dict[str, Any]:
    return {
        "workOrderId": order.work_order_id,
        "workOrderCode": order.human_code,
        "productRevisionId": order.revisions.product_revision_id,
        "operationSequence": operation.sequence,
        "operationCode": operation.operation_code,
        "operationName": operation.operation_name,
        "workCenterId": operation.work_center_id,
        "dueAt": order.due_at.isoformat(),
        "remainingQuantity": round(remaining_quantity, 3),
        "remainingWorkMinutes": round(remaining_minutes, 3),
        "reason": reason,
    }


def _missing_route_shortage(order: WorkOrder) -> dict[str, Any]:
    return {
        "workOrderId": order.work_order_id,
        "workOrderCode": order.human_code,
        "productRevisionId": order.revisions.product_revision_id,
        "operationSequence": 0,
        "operationCode": "",
        "operationName": "未配置工艺路线",
        "workCenterId": "",
        "dueAt": order.due_at.isoformat(),
        "remainingQuantity": round(order.quantity, 3),
        "remainingWorkMinutes": 0,
        "reason": "工单没有冻结工艺路线，无法计算有限产能计划",
    }


def _blocked_order_shortages(
    order: WorkOrder, command: GenerateScheduleCommand, reason: str
) -> list[dict[str, Any]]:
    shortages: list[dict[str, Any]] = []
    for operation in order.operations:
        if operation.status is OperationStatus.COMPLETED:
            continue
        remaining = max(
            0,
            operation.planned_quantity - operation.good_quantity - operation.scrap_quantity,
        )
        rate = _rate_for(order, operation.operation_code, command)
        shortages.append(_shortage(order, operation, remaining, remaining * rate, reason))
    return shortages


def serialize_planning_resource(item: PlanningResource) -> dict[str, Any]:
    return {
        "resourceId": item.resource_id,
        "code": item.code,
        "name": item.name,
        "resourceType": item.resource_type.value,
        "workshopId": item.workshop_id,
        "workCenterId": item.work_center_id,
        "dailyCapacityMinutes": item.daily_capacity_minutes,
        "overtimeCapacityMinutes": item.overtime_capacity_minutes,
        "capabilityCodes": list(item.capability_codes),
        "active": item.active,
        "version": item.version,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }


def serialize_schedule_plan(item: SchedulePlan) -> dict[str, Any]:
    return {
        "planId": item.plan_id,
        "planNumber": item.plan_number,
        "workshopId": item.workshop_id,
        "horizonStart": item.horizon_start.isoformat(),
        "horizonDays": item.horizon_days,
        "useOvertime": item.use_overtime,
        "generationParameters": item.generation_parameters,
        "assignments": list(item.assignments),
        "shortages": list(item.shortages),
        "metrics": item.metrics,
        "status": item.status.value,
        "recordVersion": item.record_version,
        "createdBy": item.created_by,
        "submittedBy": item.submitted_by,
        "approvedBy": item.approved_by,
        "approvalReason": item.approval_reason,
        "publishedBy": item.published_by,
        "withdrawnBy": item.withdrawn_by,
        "withdrawalReason": item.withdrawal_reason,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }


def serialize_scheduling_snapshot(item: SchedulingSnapshot) -> dict[str, Any]:
    return {
        "snapshotId": item.snapshot_id,
        "sourceSystem": item.source_system,
        "workshopId": item.workshop_id,
        "sourceRevision": item.source_revision,
        "observedAt": item.observed_at.isoformat(),
        "checksum": item.checksum,
        "workOrderCount": len(item.payload["workOrders"]),
        "resourceCount": len(item.payload["resources"]),
        "createdAt": item.created_at.isoformat(),
    }
