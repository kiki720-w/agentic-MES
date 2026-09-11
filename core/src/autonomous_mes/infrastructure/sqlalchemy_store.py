from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from autonomous_mes.application.ports import IdempotentResult
from autonomous_mes.domain.errors import IdempotencyConflict, InvalidTransition
from autonomous_mes.domain.events import DomainEvent
from autonomous_mes.domain.work_order import FrozenRevisions, WorkOrder, WorkOrderStatus

from .models import AgentToolAuditRow, EventOutboxRow, IdempotencyRecordRow, WorkOrderRow


class SqlAlchemyWorkOrderStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, work_order_id: str) -> WorkOrder | None:
        with self._sessions() as session:
            row = session.get(WorkOrderRow, work_order_id)
            return _to_domain(row) if row else None

    def get_by_human_code(self, human_code: str) -> WorkOrder | None:
        with self._sessions() as session:
            row = session.scalar(select(WorkOrderRow).where(WorkOrderRow.human_code == human_code))
            return _to_domain(row) if row else None

    def get_idempotent_result(self, idempotency_key: str) -> IdempotentResult | None:
        with self._sessions() as session:
            row = session.get(IdempotencyRecordRow, idempotency_key)
            if row is None:
                return None
            return IdempotentResult(
                row.operation, row.resource_id, row.resource_version, row.request_hash
            )

    def save_atomically(
        self,
        work_order: WorkOrder,
        expected_stored_version: int | None,
        events: list[DomainEvent],
        idempotency_key: str,
        idempotent_result: IdempotentResult,
    ) -> IdempotentResult:
        try:
            with self._sessions.begin() as session:
                if expected_stored_version is None:
                    session.add(_to_row(work_order))
                else:
                    result = session.execute(
                        update(WorkOrderRow)
                        .where(
                            WorkOrderRow.work_order_id == work_order.work_order_id,
                            WorkOrderRow.version == expected_stored_version,
                        )
                        .values(**_row_values(work_order))
                    )
                    if getattr(result, "rowcount", 0) != 1:
                        raise InvalidTransition("optimistic lock conflict")

                session.add_all(_event_rows(events))
                session.add(
                    IdempotencyRecordRow(
                        idempotency_key=idempotency_key,
                        operation=idempotent_result.operation,
                        resource_id=idempotent_result.resource_id,
                        resource_version=idempotent_result.resource_version,
                        request_hash=idempotent_result.request_hash,
                    )
                )
            return idempotent_result
        except IntegrityError as exc:
            raise IdempotencyConflict("unique or idempotency constraint conflict") from exc

    def list_outbox(self) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(
                select(EventOutboxRow).order_by(EventOutboxRow.occurred_at)
            ).all()
            return [
                {
                    "eventId": row.event_id,
                    "eventType": row.event_type,
                    "aggregateId": row.aggregate_id,
                    "payload": row.payload,
                    "publishStatus": row.publish_status,
                }
                for row in rows
            ]

    def record_tool_event(self, event: DomainEvent) -> None:
        payload = event.payload
        with self._sessions.begin() as session:
            session.add_all(_event_rows([event]))
            session.add(
                AgentToolAuditRow(
                    audit_id=str(uuid4()),
                    request_id=event.aggregate_id,
                    agent_id=payload["agentId"],
                    subject_id=payload["subjectId"],
                    purpose=payload["purpose"],
                    tool_name=payload["tool"],
                    tool_version=payload["toolVersion"],
                    risk=payload["risk"],
                    policy_decision=payload["policyDecision"],
                    object_type=payload["objectType"],
                    object_id=payload["objectId"],
                    detail={},
                )
            )


def _row_values(item: WorkOrder) -> dict[str, Any]:
    return {
        "human_code": item.human_code,
        "production_order_id": item.production_order_id,
        "workshop_id": item.workshop_id,
        "quantity": item.quantity,
        "due_at": item.due_at,
        "priority": item.priority,
        "product_revision_id": item.revisions.product_revision_id,
        "routing_revision_id": item.revisions.routing_revision_id,
        "bom_revision_id": item.revisions.bom_revision_id,
        "drawing_revision_ids": list(item.revisions.drawing_revision_ids),
        "status": item.status.value,
        "version": item.version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _to_row(item: WorkOrder) -> WorkOrderRow:
    return WorkOrderRow(work_order_id=item.work_order_id, **_row_values(item))


def _to_domain(row: WorkOrderRow) -> WorkOrder:
    return WorkOrder(
        work_order_id=row.work_order_id,
        human_code=row.human_code,
        production_order_id=row.production_order_id,
        workshop_id=row.workshop_id,
        quantity=row.quantity,
        due_at=row.due_at,
        priority=row.priority,
        revisions=FrozenRevisions(
            product_revision_id=row.product_revision_id,
            routing_revision_id=row.routing_revision_id,
            bom_revision_id=row.bom_revision_id,
            drawing_revision_ids=list(row.drawing_revision_ids),
        ),
        status=WorkOrderStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _event_rows(events: list[DomainEvent]) -> list[EventOutboxRow]:
    return [
        EventOutboxRow(
            event_id=event.event_id,
            event_type=event.event_type,
            schema_version=event.schema_version,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            occurred_at=event.occurred_at,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            payload=event.payload,
            publish_status="PENDING",
            attempts=0,
        )
        for event in events
    ]
