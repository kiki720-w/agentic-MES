import json
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any

from autonomous_mes.domain.equipment import EquipmentState
from autonomous_mes.domain.errors import Forbidden
from autonomous_mes.domain.work_order import WorkOrderStatus

from .ports import MesStore
from .scheduling import GenerateScheduleCommand, SchedulingApplicationService

SCHEDULING_AGENT_ID = "scheduling-agent-l3-v1"


@dataclass(frozen=True)
class SchedulingAgentCommand:
    workshop_id: str
    horizon_start: date
    horizon_days: int
    use_overtime: bool
    default_minutes_per_unit: float
    operation_rates: dict[str, float]


class SchedulingAgent:
    """Bounded L3 agent that may create and submit plans, but never publish them."""

    def __init__(
        self,
        store: MesStore,
        *,
        enabled: bool,
        auto_submit: bool,
        agent_id: str = SCHEDULING_AGENT_ID,
        agent_level: str = "L3_BOUNDED",
        publication_authority: str = "HUMAN_SUPERVISOR_ONLY",
    ) -> None:
        self._store = store
        self._scheduling = SchedulingApplicationService(store)
        self._enabled = enabled
        self._auto_submit = auto_submit
        self._agent_id = agent_id
        self._agent_level = agent_level
        self._publication_authority = publication_authority

    def analyze(self, command: SchedulingAgentCommand) -> dict[str, Any]:
        if not self._enabled:
            raise Forbidden("L3 scheduling agent is disabled")
        fingerprint, reasons = self.snapshot_fingerprint(command)
        existing = next(
            (
                item
                for item in self._scheduling.list_plans(command.workshop_id, 100)
                if item["status"] != "WITHDRAWN"
                and item["generationParameters"].get("inputFingerprint") == fingerprint
            ),
            None,
        )
        if existing is not None:
            return self._result(existing, reasons, reused=True)

        plan = self._scheduling.generate(
            GenerateScheduleCommand(
                command.workshop_id,
                command.horizon_start,
                command.horizon_days,
                command.use_overtime,
                command.default_minutes_per_unit,
                command.operation_rates,
                self._agent_id,
                fingerprint,
                {
                    "generatedBy": self._agent_id,
                    "agentLevel": self._agent_level,
                    "inputFingerprint": fingerprint,
                    "triggerReasons": reasons,
                    "publicationAuthority": self._publication_authority,
                },
            )
        )
        if self._auto_submit and plan["assignments"]:
            plan = self._scheduling.submit(
                str(plan["planId"]),
                int(plan["recordVersion"]),
                self._agent_id,
            )
        return self._result(plan, reasons, reused=False)

    def snapshot_fingerprint(self, command: SchedulingAgentCommand) -> tuple[str, list[str]]:
        external_snapshot = self._store.get_latest_scheduling_snapshot(command.workshop_id)
        resources = self._store.list_planning_resources(command.workshop_id)
        active_plans = self._store.list_schedule_plans(command.workshop_id, 100)
        if external_snapshot is not None:
            work_orders = external_snapshot.payload["workOrders"]
            external_resources = external_snapshot.payload["resources"]
            external_reasons: list[str] = []
            if not any(item.status.value == "PUBLISHED" for item in active_plans):
                external_reasons.append("NO_PUBLISHED_PLAN")
            if any(str(item.get("status", "")).upper() == "SUSPENDED" for item in work_orders):
                external_reasons.append("SUSPENDED_WORK_ORDER")
            if any(
                str(item.get("state", "")).upper() in {"DOWN", "ALARM", "OFFLINE"}
                for item in external_resources
            ):
                external_reasons.append("EQUIPMENT_CONSTRAINT_CHANGED")
            if any(not item.get("operations") for item in work_orders):
                external_reasons.append("MISSING_FROZEN_ROUTE")
            if any(item.get("materialReady") is False for item in work_orders):
                external_reasons.append("MATERIAL_NOT_READY")
            if any(item.get("qualityHold") is True for item in work_orders):
                external_reasons.append("QUALITY_HOLD")
            if not external_reasons:
                external_reasons.append("INPUT_SNAPSHOT_CHANGED")
            snapshot = {
                "workshopId": command.workshop_id,
                "horizonStart": command.horizon_start.isoformat(),
                "horizonDays": command.horizon_days,
                "useOvertime": command.use_overtime,
                "defaultMinutesPerUnit": command.default_minutes_per_unit,
                "operationRates": command.operation_rates,
                "externalSnapshot": {
                    "id": external_snapshot.snapshot_id,
                    "checksum": external_snapshot.checksum,
                    "sourceRevision": external_snapshot.source_revision,
                },
                "planningResources": [
                    {"id": item.resource_id, "version": item.version, "active": item.active}
                    for item in resources
                ],
            }
            encoded = json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            return sha256(encoded.encode()).hexdigest(), external_reasons

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
        equipment = [
            item
            for item in self._store.list_equipment(limit=10_000)
            if item.workshop_id == command.workshop_id
        ]
        reasons: list[str] = []
        if not any(item.status.value == "PUBLISHED" for item in active_plans):
            reasons.append("NO_PUBLISHED_PLAN")
        if any(item.status is WorkOrderStatus.SUSPENDED for item in orders):
            reasons.append("SUSPENDED_WORK_ORDER")
        if any(
            item.state in {EquipmentState.DOWN, EquipmentState.ALARM, EquipmentState.OFFLINE}
            for item in equipment
        ):
            reasons.append("EQUIPMENT_CONSTRAINT_CHANGED")
        if any(not item.operations for item in orders):
            reasons.append("MISSING_FROZEN_ROUTE")
        if not reasons:
            reasons.append("INPUT_SNAPSHOT_CHANGED")
        snapshot = {
            "workshopId": command.workshop_id,
            "horizonStart": command.horizon_start.isoformat(),
            "horizonDays": command.horizon_days,
            "useOvertime": command.use_overtime,
            "defaultMinutesPerUnit": command.default_minutes_per_unit,
            "operationRates": command.operation_rates,
            "orders": [
                {
                    "id": item.work_order_id,
                    "version": item.version,
                    "status": item.status.value,
                    "dueAt": item.due_at.isoformat(),
                    "priority": item.priority,
                    "operations": [
                        {
                            "sequence": operation.sequence,
                            "status": operation.status.value,
                            "good": operation.good_quantity,
                            "scrap": operation.scrap_quantity,
                        }
                        for operation in item.operations
                    ],
                }
                for item in orders
            ],
            "equipment": [
                {"id": item.equipment_id, "version": item.version, "state": item.state.value}
                for item in equipment
            ],
            "resources": [
                {"id": item.resource_id, "version": item.version, "active": item.active}
                for item in resources
            ],
        }
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode()).hexdigest(), reasons

    def _result(self, plan: dict[str, Any], reasons: list[str], *, reused: bool) -> dict[str, Any]:
        return {
            "agentId": self._agent_id,
            "agentLevel": self._agent_level,
            "decision": (
                "REUSED_EXISTING_PROPOSAL"
                if reused
                else "SUBMITTED_FOR_APPROVAL"
                if plan["status"] == "PENDING_APPROVAL"
                else "DRAFT_REQUIRES_DATA"
            ),
            "publicationAuthority": self._publication_authority,
            "triggerReasons": reasons,
            "reused": reused,
            "plan": plan,
        }
