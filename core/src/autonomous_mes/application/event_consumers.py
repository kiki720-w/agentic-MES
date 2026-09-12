import logging
from datetime import UTC, datetime
from typing import ClassVar

from autonomous_mes.application.agent_runtime import IncidentResponseAgent
from autonomous_mes.application.outbox import EventPublisher, OutboxMessage
from autonomous_mes.application.ports import MesStore
from autonomous_mes.application.scheduling_agent import (
    SchedulingAgent,
    SchedulingAgentCommand,
)


class QualityRecommendationEventPublisher:
    """Runs the internal quality Agent before forwarding manufacturing events."""

    def __init__(
        self,
        store: MesStore,
        downstream: EventPublisher,
        logger: logging.Logger | None = None,
    ) -> None:
        self._store = store
        self._agent = IncidentResponseAgent(store)
        self._downstream = downstream
        self._logger = logger or logging.getLogger("autonomous_mes.quality_agent")

    def publish(self, message: OutboxMessage) -> None:
        if message.event_type == "OperationCompleted":
            self._handle_operation_completed(message)
        self._downstream.publish(message)

    def _handle_operation_completed(self, message: OutboxMessage) -> None:
        if message.aggregate_type != "WorkOrder":
            raise ValueError("OperationCompleted must belong to a WorkOrder aggregate")
        sequence = message.payload.get("operationSequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise ValueError("OperationCompleted operationSequence must be a positive integer")
        if self._store.get_inspection_for_operation(message.aggregate_id, sequence):
            self._logger.info(
                "quality_recommendation_skipped_existing_inspection",
                extra={"event_id": message.event_id, "work_order_id": message.aggregate_id},
            )
            return
        proposal = self._agent.recommend_quality_inspection(
            message.aggregate_id,
            sequence,
            source_event_id=message.event_id,
            source_correlation_id=message.correlation_id,
        )
        self._logger.info(
            "quality_recommendation_observed",
            extra={
                "event_id": message.event_id,
                "proposal_id": proposal["proposalId"],
                "work_order_id": message.aggregate_id,
                "operation_sequence": sequence,
            },
        )


class SchedulingAgentEventPublisher:
    """Triggers bounded replanning without making event delivery depend on the Agent."""

    TRIGGERS: ClassVar[frozenset[str]] = frozenset(
        {
            "WorkOrderCreated",
            "WorkOrderReleased",
            "OperationProductionReported",
            "OperationCompleted",
            "OperationSuspendedByEquipmentIncident",
            "OperationResumed",
            "EquipmentTelemetryRecorded",
            "PlanningResourceRegistered",
            "ManufacturingResourceRegistered",
            "ManufacturingResourceStateChanged",
            "QualityInspectionResultRecorded",
            "SchedulingSnapshotIngested",
        }
    )

    def __init__(
        self,
        store: MesStore,
        downstream: EventPublisher,
        *,
        workshop_ids: tuple[str, ...],
        horizon_days: int,
        default_minutes_per_unit: float,
        use_overtime: bool,
        auto_submit: bool,
        logger: logging.Logger | None = None,
    ) -> None:
        self._agent = SchedulingAgent(store, enabled=True, auto_submit=auto_submit)
        self._downstream = downstream
        self._workshop_ids = workshop_ids
        self._horizon_days = horizon_days
        self._default_minutes_per_unit = default_minutes_per_unit
        self._use_overtime = use_overtime
        self._logger = logger or logging.getLogger("autonomous_mes.scheduling_agent")

    def publish(self, message: OutboxMessage) -> None:
        if message.event_type in self.TRIGGERS:
            for workshop_id in self._workshop_ids:
                self._analyze_isolated(workshop_id, message)
        self._downstream.publish(message)

    def _analyze_isolated(self, workshop_id: str, message: OutboxMessage) -> None:
        try:
            result = self._agent.analyze(
                SchedulingAgentCommand(
                    workshop_id,
                    datetime.now(UTC).date(),
                    self._horizon_days,
                    self._use_overtime,
                    self._default_minutes_per_unit,
                    {},
                )
            )
            self._logger.info(
                "scheduling_agent_observed",
                extra={
                    "trigger_event_id": message.event_id,
                    "workshop_id": workshop_id,
                    "decision": result["decision"],
                    "plan_id": result["plan"]["planId"],
                },
            )
        except Exception:
            self._logger.exception(
                "scheduling_agent_failed",
                extra={"trigger_event_id": message.event_id, "workshop_id": workshop_id},
            )
