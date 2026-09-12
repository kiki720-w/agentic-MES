from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from autonomous_mes.domain.equipment import EquipmentState
from autonomous_mes.domain.errors import NotFound, ValidationError
from autonomous_mes.domain.scheduling import PlanningResource, SchedulePlan
from autonomous_mes.domain.work_order import OperationStatus, WorkOrder, WorkOrderStatus

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
class GenerateScheduleCommand:
    workshop_id: str
    horizon_start: date
    horizon_days: int
    use_overtime: bool
    default_minutes_per_unit: float
    operation_rates: dict[str, float]
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

    def register_resource(
        self, command: RegisterPlanningResourceCommand
    ) -> dict[str, Any]:
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

    def generate(self, command: GenerateScheduleCommand) -> dict[str, Any]:
        if command.default_minutes_per_unit <= 0:
            raise ValidationError("default minutes per unit must be greater than zero")
        if not 1 <= command.horizon_days <= 31:
            raise ValidationError("schedule horizon must be between 1 and 31 working days")
        if any(value <= 0 for value in command.operation_rates.values()):
            raise ValidationError("operation rates must be greater than zero")

        days = _working_days(command.horizon_start, command.horizon_days)
        resources = self._candidates(command.workshop_id)
        orders = [
            item
            for item in self._store.list_work_orders(limit=10_000, include_test=False)
            if item.workshop_id == command.workshop_id
            and item.status
            in {
                WorkOrderStatus.DRAFT,
                WorkOrderStatus.RELEASED,
                WorkOrderStatus.IN_PROGRESS,
                WorkOrderStatus.SUSPENDED,
            }
        ]
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
            earliest_index = 0
            for operation in order.operations:
                remaining_quantity = max(
                    0,
                    operation.planned_quantity
                    - operation.good_quantity
                    - operation.scrap_quantity,
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

                rate = _rate_for(order, operation.operation_code, command)
                required = remaining_quantity * rate
                candidates = _matching_candidates(resources, operation.work_center_id, operation.operation_code)
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
                        value for (resource_id, _), value in loads.items()
                        if resource_id == candidate.resource_id
                    )
                    options.append(
                        (remainder, finish_index, existing, candidate, candidate_plan)
                    )
                remainder, finish_index, _, selected, allocation = min(
                    options, key=lambda item: (item[0], item[1], item[2], item[3].code)
                )
                for day_index, minutes in allocation:
                    production_day = days[day_index]
                    loads[(selected.resource_id, production_day)] = (
                        loads.get((selected.resource_id, production_day), 0) + minutes
                    )
                    is_late = production_day > order.due_at.date()
                    if is_late:
                        late_orders.add(order.work_order_id)
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
                            "plannedQuantity": round(minutes / rate, 3),
                            "dueAt": order.due_at.isoformat(),
                            "deliveryStatus": "LATE" if is_late else "ON_TIME",
                            "rationale": "能力匹配；候选资源中优先完整排入并最早完成",
                        }
                    )
                if allocation:
                    scheduled_orders.add(order.work_order_id)
                    earliest_index = min(finish_index + 1, len(days))
                if remainder > 0.001:
                    shortages.append(
                        _shortage(
                            order,
                            operation,
                            round(remainder / rate, 3),
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
        }
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
        if other_load + float(assignment["plannedWorkMinutes"]) > target.capacity(
            current.use_overtime
        ) + 0.001:
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

    def approve(
        self, plan_id: str, version: int, actor_id: str, reason: str
    ) -> dict[str, Any]:
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

    def withdraw(
        self, plan_id: str, version: int, actor_id: str, reason: str
    ) -> dict[str, Any]:
        current = self._require(plan_id)
        changed, event = current.withdraw(version, actor_id, reason, str(uuid4()))
        self._store.update_schedule_plan_atomically(changed, current.record_version, event)
        return serialize_schedule_plan(changed)

    def _require(self, plan_id: str) -> SchedulePlan:
        plan = self._store.get_schedule_plan(plan_id)
        if plan is None:
            raise NotFound("schedule plan not found")
        return plan

    def _candidates(self, workshop_id: str) -> list[_Candidate]:
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
        return resources + equipment


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
        and (not item.capabilities or "*" in item.capabilities or operation_code.upper() in item.capabilities)
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
