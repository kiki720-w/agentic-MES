import logging

from autonomous_mes.application.agent_runtime import IncidentResponseAgent
from autonomous_mes.application.outbox import EventPublisher, OutboxMessage
from autonomous_mes.application.ports import MesStore


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
