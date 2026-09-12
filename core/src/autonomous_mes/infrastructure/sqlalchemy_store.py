from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, exists, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from autonomous_mes.application.connector_security import ConnectorReceipt
from autonomous_mes.application.ports import IdempotentResult
from autonomous_mes.domain.agent import AgentProposal, ProposalStatus
from autonomous_mes.domain.equipment import Equipment, EquipmentState, TelemetrySample
from autonomous_mes.domain.errors import IdempotencyConflict, InvalidTransition
from autonomous_mes.domain.events import DomainEvent
from autonomous_mes.domain.genealogy import (
    ExecutionSession,
    GenealogyLink,
    MaterialConsumption,
    ProductUnit,
)
from autonomous_mes.domain.master_data import ManufacturingResource
from autonomous_mes.domain.quality import InspectionStatus, QualityInspection
from autonomous_mes.domain.quality_policy import (
    QualityPolicyScope,
    QualityPolicyStatus,
    QualityRiskPolicy,
)
from autonomous_mes.domain.scheduling import (
    PlanningResource,
    PlanningResourceType,
    SchedulePlan,
    SchedulePlanStatus,
)
from autonomous_mes.domain.work_order import (
    FrozenRevisions,
    OperationStatus,
    ProductionOperation,
    WorkOrder,
    WorkOrderStatus,
)

from .models import (
    AgentProposalRow,
    AgentToolAuditRow,
    ConnectorReceiptRow,
    EquipmentRow,
    EquipmentTelemetryRow,
    EventOutboxRow,
    ExecutionSessionRow,
    GenealogyLinkRow,
    IdempotencyRecordRow,
    ManufacturingResourceRow,
    MaterialConsumptionRow,
    PlanningResourceRow,
    ProductUnitRow,
    QualityInspectionRow,
    QualityRiskPolicyRow,
    SchedulePlanRow,
    WorkOrderOperationRow,
    WorkOrderRow,
)


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

    def list_work_orders(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
        include_test: bool = False,
    ) -> list[WorkOrder]:
        with self._sessions() as session:
            statement = select(WorkOrderRow)
            statement = self._filter_work_orders(statement, query, status, include_test)
            rows = session.scalars(
                statement.order_by(
                    WorkOrderRow.updated_at.desc(), WorkOrderRow.work_order_id.desc()
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [_to_domain(row) for row in rows]

    def count_work_orders(
        self,
        query: str | None = None,
        status: str | None = None,
        include_test: bool = False,
    ) -> int:
        with self._sessions() as session:
            statement = select(func.count()).select_from(WorkOrderRow)
            statement = self._filter_work_orders(statement, query, status, include_test)
            return int(session.scalar(statement) or 0)

    def summarize_work_orders(self, include_test: bool = False) -> dict[str, int]:
        with self._sessions() as session:
            statement = select(WorkOrderRow.status, func.count())
            statement = self._filter_work_orders(statement, None, None, include_test)
            rows = session.execute(statement.group_by(WorkOrderRow.status)).all()
            return {str(status): int(count) for status, count in rows}

    @staticmethod
    def _filter_work_orders(
        statement: Any,
        query: str | None,
        status: str | None,
        include_test: bool,
    ) -> Any:
        if not include_test:
            statement = statement.where(
                ~or_(
                    WorkOrderRow.human_code.startswith("WO-PG-TEST"),
                    WorkOrderRow.human_code.startswith("WO-PG-ROLLBACK"),
                )
            )
        if query:
            statement = statement.where(WorkOrderRow.human_code.icontains(query, autoescape=True))
        if status:
            statement = statement.where(WorkOrderRow.status == status)
        return statement

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
                    session.flush()
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
                    session.execute(
                        delete(WorkOrderOperationRow).where(
                            WorkOrderOperationRow.work_order_id == work_order.work_order_id
                        )
                    )

                session.add_all(_operation_rows(work_order))
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
                    "aggregateType": row.aggregate_type,
                    "aggregateId": row.aggregate_id,
                    "occurredAt": row.occurred_at.isoformat(),
                    "correlationId": row.correlation_id,
                    "causationId": row.causation_id,
                    "schemaVersion": row.schema_version,
                    "payload": row.payload,
                    "publishStatus": row.publish_status,
                }
                for row in rows
            ]

    def list_recent_outbox(
        self,
        limit: int = 100,
        offset: int = 0,
        before_occurred_at: datetime | None = None,
        before_event_id: str | None = None,
        query: str | None = None,
        publish_status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._sessions() as session:
            statement = select(EventOutboxRow)
            if query:
                pattern = f"%{query}%"
                statement = statement.where(
                    or_(
                        EventOutboxRow.event_id.ilike(pattern),
                        EventOutboxRow.event_type.ilike(pattern),
                        EventOutboxRow.aggregate_type.ilike(pattern),
                        EventOutboxRow.aggregate_id.ilike(pattern),
                        EventOutboxRow.correlation_id.ilike(pattern),
                    )
                )
            if publish_status:
                statement = statement.where(
                    EventOutboxRow.publish_status == publish_status
                )
            if before_occurred_at and before_event_id:
                statement = statement.where(
                    or_(
                        EventOutboxRow.occurred_at < before_occurred_at,
                        (
                            (EventOutboxRow.occurred_at == before_occurred_at)
                            & (EventOutboxRow.event_id < before_event_id)
                        ),
                    )
                )
            rows = session.scalars(
                statement.order_by(
                    EventOutboxRow.occurred_at.desc(), EventOutboxRow.event_id.desc()
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [
                {
                    "eventId": row.event_id,
                    "eventType": row.event_type,
                    "aggregateType": row.aggregate_type,
                    "aggregateId": row.aggregate_id,
                    "occurredAt": row.occurred_at.isoformat(),
                    "correlationId": row.correlation_id,
                    "causationId": row.causation_id,
                    "schemaVersion": row.schema_version,
                    "payload": row.payload,
                    "publishStatus": row.publish_status,
                }
                for row in rows
            ]

    def count_outbox(
        self, query: str | None = None, publish_status: str | None = None
    ) -> int:
        with self._sessions() as session:
            statement = select(func.count()).select_from(EventOutboxRow)
            if query:
                pattern = f"%{query}%"
                statement = statement.where(
                    or_(
                        EventOutboxRow.event_id.ilike(pattern),
                        EventOutboxRow.event_type.ilike(pattern),
                        EventOutboxRow.aggregate_type.ilike(pattern),
                        EventOutboxRow.aggregate_id.ilike(pattern),
                        EventOutboxRow.correlation_id.ilike(pattern),
                    )
                )
            if publish_status:
                statement = statement.where(
                    EventOutboxRow.publish_status == publish_status
                )
            return int(session.scalar(statement) or 0)

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

    def get_equipment(self, equipment_id: str) -> Equipment | None:
        with self._sessions() as session:
            row = session.get(EquipmentRow, equipment_id)
            return _equipment_to_domain(row) if row else None

    def get_equipment_by_code(self, code: str) -> Equipment | None:
        with self._sessions() as session:
            row = session.scalar(select(EquipmentRow).where(EquipmentRow.code == code))
            return _equipment_to_domain(row) if row else None

    def list_equipment(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        state: str | None = None,
    ) -> list[Equipment]:
        with self._sessions() as session:
            statement = select(EquipmentRow)
            if query:
                pattern = f"%{query}%"
                statement = statement.where(
                    or_(EquipmentRow.code.ilike(pattern), EquipmentRow.name.ilike(pattern))
                )
            if state:
                statement = statement.where(EquipmentRow.state == state)
            rows = session.scalars(
                statement.order_by(EquipmentRow.updated_at.desc(), EquipmentRow.equipment_id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [_equipment_to_domain(row) for row in rows]

    def count_equipment(self, query: str | None = None, state: str | None = None) -> int:
        with self._sessions() as session:
            statement = select(func.count()).select_from(EquipmentRow)
            if query:
                pattern = f"%{query}%"
                statement = statement.where(
                    or_(EquipmentRow.code.ilike(pattern), EquipmentRow.name.ilike(pattern))
                )
            if state:
                statement = statement.where(EquipmentRow.state == state)
            return int(session.scalar(statement) or 0)

    def summarize_equipment(self) -> dict[str, int]:
        with self._sessions() as session:
            rows = session.execute(
                select(EquipmentRow.state, func.count()).group_by(EquipmentRow.state)
            ).all()
            return {str(state): int(count) for state, count in rows}

    def telemetry_sample_exists(self, sample_id: str) -> bool:
        with self._sessions() as session:
            return session.get(EquipmentTelemetryRow, sample_id) is not None

    def add_equipment_atomically(self, equipment: Equipment, event: DomainEvent) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(EquipmentRow(**_equipment_values(equipment)))
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("equipment code already exists") from exc

    def record_telemetry_atomically(
        self,
        equipment: Equipment,
        expected_stored_version: int,
        sample: TelemetrySample,
        event: DomainEvent,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                result = session.execute(
                    update(EquipmentRow)
                    .where(
                        EquipmentRow.equipment_id == equipment.equipment_id,
                        EquipmentRow.version == expected_stored_version,
                    )
                    .values(**_equipment_values(equipment, include_id=False))
                )
                if getattr(result, "rowcount", 0) != 1:
                    raise InvalidTransition("optimistic lock conflict")
                session.add(
                    EquipmentTelemetryRow(
                        sample_id=sample.sample_id,
                        equipment_id=equipment.equipment_id,
                        observed_at=sample.observed_at,
                        state=sample.state.value,
                        spindle_load_percent=sample.spindle_load_percent,
                        temperature_celsius=sample.temperature_celsius,
                        alarm_code=sample.alarm_code,
                        downtime_reason=sample.downtime_reason,
                    )
                )
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("telemetry sample already exists") from exc

    def get_agent_proposal(self, proposal_id: str) -> AgentProposal | None:
        with self._sessions() as session:
            row = session.get(AgentProposalRow, proposal_id)
            return _proposal_to_domain(row) if row else None

    def get_agent_proposal_by_fingerprint(self, fingerprint: str) -> AgentProposal | None:
        with self._sessions() as session:
            row = session.scalar(
                select(AgentProposalRow).where(AgentProposalRow.fingerprint == fingerprint)
            )
            return _proposal_to_domain(row) if row else None

    def get_quality_recommendation(
        self, work_order_id: str, operation_sequence: int
    ) -> AgentProposal | None:
        with self._sessions() as session:
            row = session.scalar(
                select(AgentProposalRow)
                .where(
                    AgentProposalRow.action == "CREATE_QUALITY_INSPECTION",
                    AgentProposalRow.work_order_id == work_order_id,
                    AgentProposalRow.operation_sequence == operation_sequence,
                )
                .order_by(AgentProposalRow.created_at)
                .limit(1)
            )
            return _proposal_to_domain(row) if row else None

    def list_agent_proposals(self, limit: int = 100) -> list[AgentProposal]:
        with self._sessions() as session:
            rows = session.scalars(
                select(AgentProposalRow)
                .order_by(
                    AgentProposalRow.quality_risk_score.desc().nullslast(),
                    AgentProposalRow.updated_at.desc(),
                )
                .limit(limit)
            ).all()
            return [_proposal_to_domain(row) for row in rows]

    def add_agent_proposal_atomically(self, proposal: AgentProposal, event: DomainEvent) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(AgentProposalRow(**_proposal_values(proposal)))
                session.add_all(_event_rows([event]))
        except IntegrityError:
            return

    def quality_risk_facts(
        self,
        work_order_id: str,
        operation_sequence: int,
        equipment_id: str,
        lookback_days: int = 30,
    ) -> dict[str, Any]:
        with self._sessions() as session:
            history_base = (
                select(func.count())
                .select_from(QualityInspectionRow)
                .join(
                    WorkOrderOperationRow,
                    (WorkOrderOperationRow.work_order_id == QualityInspectionRow.work_order_id)
                    & (WorkOrderOperationRow.sequence == QualityInspectionRow.operation_sequence),
                )
                .where(
                    WorkOrderOperationRow.assigned_resource_id == equipment_id,
                    QualityInspectionRow.result.is_not(None),
                    ~(
                        (QualityInspectionRow.work_order_id == work_order_id)
                        & (QualityInspectionRow.operation_sequence == operation_sequence)
                    ),
                )
            )
            inspected = int(session.scalar(history_base) or 0)
            failed = int(
                session.scalar(history_base.where(QualityInspectionRow.result == "FAIL")) or 0
            )
            cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
            alarms = int(
                session.scalar(
                    select(func.count())
                    .select_from(EquipmentTelemetryRow)
                    .where(
                        EquipmentTelemetryRow.equipment_id == equipment_id,
                        EquipmentTelemetryRow.observed_at >= cutoff,
                        or_(
                            EquipmentTelemetryRow.alarm_code.is_not(None),
                            EquipmentTelemetryRow.state.in_(("ALARM", "DOWN")),
                        ),
                    )
                )
                or 0
            )
            contexts = session.scalars(
                select(ExecutionSessionRow.resource_context).where(
                    ExecutionSessionRow.work_order_id == work_order_id,
                    ExecutionSessionRow.operation_sequence == operation_sequence,
                )
            ).all()
            tool_lives = [
                float(resource["life_remaining_percent"])
                for context in contexts
                for resource in context
                if resource.get("resource_type") == "TOOL"
                and resource.get("life_remaining_percent") is not None
            ]
            return {
                "historicalInspections": inspected,
                "historicalFailures": failed,
                "recentAlarmCount": alarms,
                "minimumToolLifePercent": min(tool_lives) if tool_lives else None,
            }

    def get_quality_policy(self, policy_id: str) -> QualityRiskPolicy | None:
        with self._sessions() as session:
            row = session.get(QualityRiskPolicyRow, policy_id)
            return _quality_policy_to_domain(row) if row else None

    def list_quality_policies(self, limit: int = 100) -> list[QualityRiskPolicy]:
        with self._sessions() as session:
            rows = session.scalars(
                select(QualityRiskPolicyRow)
                .order_by(QualityRiskPolicyRow.updated_at.desc())
                .limit(limit)
            ).all()
            return [_quality_policy_to_domain(row) for row in rows]

    def next_quality_policy_version(self, policy_key: str) -> int:
        with self._sessions() as session:
            current = session.scalar(
                select(func.max(QualityRiskPolicyRow.version)).where(
                    QualityRiskPolicyRow.policy_key == policy_key
                )
            )
            return int(current or 0) + 1

    def resolve_quality_risk_policy(
        self, product_revision_id: str, operation_code: str, as_of: datetime
    ) -> QualityRiskPolicy | None:
        with self._sessions() as session:
            rows = list(
                session.scalars(
                    select(QualityRiskPolicyRow).where(
                    QualityRiskPolicyRow.status == QualityPolicyStatus.APPROVED.value,
                    QualityRiskPolicyRow.effective_from <= as_of,
                    or_(
                        QualityRiskPolicyRow.product_revision_id.is_(None),
                        QualityRiskPolicyRow.product_revision_id == product_revision_id,
                    ),
                    or_(
                        QualityRiskPolicyRow.operation_code.is_(None),
                        QualityRiskPolicyRow.operation_code == operation_code,
                    ),
                    )
                ).all()
            )
            if not rows:
                return None
            rows.sort(
                key=lambda row: (
                    int(row.product_revision_id is not None)
                    + int(row.operation_code is not None),
                    row.effective_from or row.created_at,
                    row.version,
                ),
                reverse=True,
            )
            return _quality_policy_to_domain(rows[0])

    def list_quality_policy_simulation_cases(
        self, policy: QualityRiskPolicy, lookback_days: int, limit: int
    ) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
        conditions = [
            WorkOrderOperationRow.status == "COMPLETED",
            WorkOrderOperationRow.assigned_resource_id.is_not(None),
            WorkOrderRow.updated_at >= cutoff,
        ]
        if policy.product_revision_id:
            conditions.append(WorkOrderRow.product_revision_id == policy.product_revision_id)
        if policy.operation_code:
            conditions.append(WorkOrderOperationRow.operation_code == policy.operation_code)
        with self._sessions() as session:
            rows = session.execute(
                select(WorkOrderRow, WorkOrderOperationRow)
                .join(
                    WorkOrderOperationRow,
                    WorkOrderOperationRow.work_order_id == WorkOrderRow.work_order_id,
                )
                .where(*conditions)
                .order_by(WorkOrderRow.updated_at.desc())
                .limit(limit)
            ).all()
            snapshots = [
                {
                    "workOrderId": order.work_order_id,
                    "humanCode": order.human_code,
                    "productRevisionId": order.product_revision_id,
                    "operationSequence": operation.sequence,
                    "operationCode": operation.operation_code,
                    "plannedQuantity": operation.planned_quantity,
                    "goodQuantity": operation.good_quantity,
                    "scrapQuantity": operation.scrap_quantity,
                    "equipmentId": operation.assigned_resource_id,
                }
                for order, operation in rows
            ]
        for case in snapshots:
            case["riskFacts"] = self.quality_risk_facts(
                str(case["workOrderId"]),
                int(case["operationSequence"]),
                str(case["equipmentId"]),
                lookback_days,
            )
        return snapshots

    def add_quality_policy_atomically(
        self, policy: QualityRiskPolicy, event: DomainEvent
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(QualityRiskPolicyRow(**_quality_policy_values(policy)))
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise InvalidTransition("quality policy version already exists") from exc

    def update_quality_policy_atomically(
        self,
        policy: QualityRiskPolicy,
        expected_record_version: int,
        event: DomainEvent,
    ) -> None:
        with self._sessions.begin() as session:
            result = session.execute(
                update(QualityRiskPolicyRow)
                .where(
                    QualityRiskPolicyRow.policy_id == policy.policy_id,
                    QualityRiskPolicyRow.record_version == expected_record_version,
                )
                .values(**_quality_policy_values(policy, include_id=False))
            )
            if getattr(result, "rowcount", 0) != 1:
                raise InvalidTransition("quality policy version changed")
            session.add_all(_event_rows([event]))

    def get_planning_resource(self, resource_id: str) -> PlanningResource | None:
        with self._sessions() as session:
            row = session.get(PlanningResourceRow, resource_id)
            return _planning_resource_to_domain(row) if row else None

    def list_planning_resources(self, workshop_id: str) -> list[PlanningResource]:
        with self._sessions() as session:
            rows = session.scalars(
                select(PlanningResourceRow)
                .where(PlanningResourceRow.workshop_id == workshop_id)
                .order_by(PlanningResourceRow.work_center_id, PlanningResourceRow.code)
            ).all()
            return [_planning_resource_to_domain(row) for row in rows]

    def add_planning_resource_atomically(
        self, resource: PlanningResource, event: DomainEvent
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(PlanningResourceRow(**_planning_resource_values(resource)))
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise InvalidTransition(
                "planning resource code already exists in workshop"
            ) from exc

    def get_schedule_plan(self, plan_id: str) -> SchedulePlan | None:
        with self._sessions() as session:
            row = session.get(SchedulePlanRow, plan_id)
            return _schedule_plan_to_domain(row) if row else None

    def list_schedule_plans(self, workshop_id: str, limit: int = 30) -> list[SchedulePlan]:
        with self._sessions() as session:
            rows = session.scalars(
                select(SchedulePlanRow)
                .where(SchedulePlanRow.workshop_id == workshop_id)
                .order_by(SchedulePlanRow.created_at.desc())
                .limit(limit)
            ).all()
            return [_schedule_plan_to_domain(row) for row in rows]

    def add_schedule_plan_atomically(self, plan: SchedulePlan, event: DomainEvent) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(SchedulePlanRow(**_schedule_plan_values(plan)))
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise InvalidTransition("schedule plan already exists") from exc

    def update_schedule_plan_atomically(
        self, plan: SchedulePlan, expected_record_version: int, event: DomainEvent
    ) -> None:
        with self._sessions.begin() as session:
            result = session.execute(
                update(SchedulePlanRow)
                .where(
                    SchedulePlanRow.plan_id == plan.plan_id,
                    SchedulePlanRow.record_version == expected_record_version,
                )
                .values(**_schedule_plan_values(plan, include_id=False))
            )
            if getattr(result, "rowcount", 0) != 1:
                raise InvalidTransition("schedule plan version changed")
            session.add_all(_event_rows([event]))

    def update_agent_proposal_atomically(
        self, proposal: AgentProposal, expected_status: ProposalStatus, event: DomainEvent
    ) -> None:
        with self._sessions.begin() as session:
            result = session.execute(
                update(AgentProposalRow)
                .where(
                    AgentProposalRow.proposal_id == proposal.proposal_id,
                    AgentProposalRow.status == expected_status.value,
                )
                .values(**_proposal_values(proposal, include_id=False))
            )
            if getattr(result, "rowcount", 0) != 1:
                raise InvalidTransition("agent proposal status changed")
            session.add_all(_event_rows([event]))

    def get_inspection(self, inspection_id: str) -> QualityInspection | None:
        with self._sessions() as session:
            row = session.get(QualityInspectionRow, inspection_id)
            return _inspection_to_domain(row) if row else None

    def get_inspection_for_operation(
        self, work_order_id: str, operation_sequence: int
    ) -> QualityInspection | None:
        with self._sessions() as session:
            row = session.scalar(
                select(QualityInspectionRow)
                .where(
                    QualityInspectionRow.work_order_id == work_order_id,
                    QualityInspectionRow.operation_sequence == operation_sequence,
                )
                .order_by(QualityInspectionRow.created_at)
                .limit(1)
            )
            return _inspection_to_domain(row) if row else None

    def list_inspections(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
    ) -> list[QualityInspection]:
        with self._sessions() as session:
            statement = select(QualityInspectionRow)
            if query:
                pattern = f"%{query}%"
                statement = statement.where(
                    or_(
                        QualityInspectionRow.work_order_id.ilike(pattern),
                        QualityInspectionRow.defect_code.ilike(pattern),
                    )
                )
            if status:
                statement = statement.where(QualityInspectionRow.status == status)
            rows = session.scalars(
                statement.order_by(
                    QualityInspectionRow.updated_at.desc(),
                    QualityInspectionRow.inspection_id.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [_inspection_to_domain(row) for row in rows]

    def count_inspections(self, query: str | None = None, status: str | None = None) -> int:
        with self._sessions() as session:
            statement = select(func.count()).select_from(QualityInspectionRow)
            if query:
                pattern = f"%{query}%"
                statement = statement.where(
                    or_(
                        QualityInspectionRow.work_order_id.ilike(pattern),
                        QualityInspectionRow.defect_code.ilike(pattern),
                    )
                )
            if status:
                statement = statement.where(QualityInspectionRow.status == status)
            return int(session.scalar(statement) or 0)

    def summarize_inspections(self) -> dict[str, int]:
        with self._sessions() as session:
            rows = session.execute(
                select(QualityInspectionRow.status, func.count()).group_by(
                    QualityInspectionRow.status
                )
            ).all()
            return {str(status): int(count) for status, count in rows}

    def list_eligible_quality_operations(
        self,
        limit: int = 30,
        offset: int = 0,
        query: str | None = None,
        workshop_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._sessions() as session:
            statement = self._eligible_quality_operations_statement(
                select(
                    WorkOrderOperationRow,
                    WorkOrderRow.human_code,
                    WorkOrderRow.version,
                ),
                query,
                workshop_id,
            )
            rows = session.execute(
                statement.order_by(
                    WorkOrderRow.updated_at.desc(),
                    WorkOrderOperationRow.work_order_id.desc(),
                    WorkOrderOperationRow.sequence,
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [
                {
                    "workOrderId": operation.work_order_id,
                    "humanCode": human_code,
                    "workOrderVersion": work_order_version,
                    "operationSequence": operation.sequence,
                    "operationCode": operation.operation_code,
                    "operationName": operation.operation_name,
                    "workCenterId": operation.work_center_id,
                    "plannedQuantity": operation.planned_quantity,
                }
                for operation, human_code, work_order_version in rows
            ]

    def count_eligible_quality_operations(
        self,
        query: str | None = None,
        workshop_id: str | None = None,
    ) -> int:
        with self._sessions() as session:
            statement = self._eligible_quality_operations_statement(
                select(func.count()).select_from(WorkOrderOperationRow),
                query,
                workshop_id,
            )
            return int(session.scalar(statement) or 0)

    @staticmethod
    def _eligible_quality_operations_statement(
        statement: Any,
        query: str | None,
        workshop_id: str | None,
    ) -> Any:
        inspection_exists = exists(
            select(QualityInspectionRow.inspection_id).where(
                QualityInspectionRow.work_order_id == WorkOrderOperationRow.work_order_id,
                QualityInspectionRow.operation_sequence == WorkOrderOperationRow.sequence,
            )
        )
        statement = statement.join(
            WorkOrderRow,
            WorkOrderRow.work_order_id == WorkOrderOperationRow.work_order_id,
        ).where(
            WorkOrderOperationRow.status == "COMPLETED",
            ~inspection_exists,
        )
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(
                    WorkOrderRow.human_code.ilike(pattern),
                    WorkOrderRow.production_order_id.ilike(pattern),
                    WorkOrderOperationRow.operation_code.ilike(pattern),
                    WorkOrderOperationRow.operation_name.ilike(pattern),
                    WorkOrderOperationRow.work_center_id.ilike(pattern),
                )
            )
        if workshop_id:
            statement = statement.where(WorkOrderRow.workshop_id == workshop_id)
        return statement

    def inspect_operation_projection(self) -> dict[str, int]:
        with self._sessions() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                row = session.execute(text(_POSTGRES_OPERATION_PROJECTION_AUDIT)).mappings().one()
                return {
                    "legacyCount": int(row["legacy_count"]),
                    "normalizedCount": int(row["normalized_count"]),
                    "mismatchCount": int(row["mismatch_count"]),
                    "extraCount": int(row["extra_count"]),
                }
            legacy_rows = session.execute(
                select(WorkOrderRow.work_order_id, WorkOrderRow.operations)
            ).all()
            normalized_rows = session.scalars(select(WorkOrderOperationRow)).all()
            legacy = {
                (work_order_id, int(operation["sequence"])): _operation_dict_signature(operation)
                for work_order_id, operations in legacy_rows
                for operation in operations
            }
            normalized = {
                (operation.work_order_id, operation.sequence): _operation_row_signature(operation)
                for operation in normalized_rows
            }
            return {
                "legacyCount": len(legacy),
                "normalizedCount": len(normalized),
                "mismatchCount": sum(
                    1 for key, value in legacy.items() if normalized.get(key) != value
                ),
                "extraCount": len(set(normalized) - set(legacy)),
            }

    def add_inspection_atomically(self, inspection: QualityInspection, event: DomainEvent) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(QualityInspectionRow(**_inspection_values(inspection)))
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise InvalidTransition("inspection already exists for this operation") from exc

    def update_inspection_atomically(
        self, inspection: QualityInspection, expected_version: int, event: DomainEvent
    ) -> None:
        with self._sessions.begin() as session:
            result = session.execute(
                update(QualityInspectionRow)
                .where(
                    QualityInspectionRow.inspection_id == inspection.inspection_id,
                    QualityInspectionRow.version == expected_version,
                )
                .values(**_inspection_values(inspection, include_id=False))
            )
            if getattr(result, "rowcount", 0) != 1:
                raise InvalidTransition("quality inspection version changed")
            session.add_all(_event_rows([event]))

    def create_inspection_from_proposal_atomically(
        self,
        inspection: QualityInspection,
        proposal: AgentProposal,
        expected_proposal_status: ProposalStatus,
        events: list[DomainEvent],
    ) -> None:
        try:
            with self._sessions.begin() as session:
                result = session.execute(
                    update(AgentProposalRow)
                    .where(
                        AgentProposalRow.proposal_id == proposal.proposal_id,
                        AgentProposalRow.status == expected_proposal_status.value,
                        AgentProposalRow.action == "CREATE_QUALITY_INSPECTION",
                    )
                    .values(**_proposal_values(proposal, include_id=False))
                )
                if getattr(result, "rowcount", 0) != 1:
                    raise InvalidTransition("agent proposal status changed")
                session.add(QualityInspectionRow(**_inspection_values(inspection)))
                session.add_all(_event_rows(events))
        except IntegrityError as exc:
            raise InvalidTransition("inspection already exists for this operation") from exc

    def get_product_unit(self, product_serial: str) -> ProductUnit | None:
        with self._sessions() as session:
            row = session.get(ProductUnitRow, product_serial)
            return _product_unit_to_domain(row) if row else None

    def list_genealogy_links(self, product_serial: str) -> list[GenealogyLink]:
        with self._sessions() as session:
            rows = session.scalars(
                select(GenealogyLinkRow)
                .where(GenealogyLinkRow.product_serial == product_serial)
                .order_by(GenealogyLinkRow.occurred_at, GenealogyLinkRow.link_id)
            ).all()
            return [_genealogy_link_to_domain(row) for row in rows]

    def add_product_unit_atomically(
        self, unit: ProductUnit, links: list[GenealogyLink], event: DomainEvent
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(
                    ProductUnitRow(
                        product_serial=unit.product_serial,
                        work_order_id=unit.work_order_id,
                        product_revision_id=unit.product_revision_id,
                        genealogy_status=unit.genealogy_status,
                        created_at=unit.created_at,
                    )
                )
                session.flush()
                session.add_all(
                    GenealogyLinkRow(
                        link_id=item.link_id,
                        product_serial=item.product_serial,
                        relation_type=item.relation_type,
                        object_type=item.object_type,
                        object_id=item.object_id,
                        operation_sequence=item.operation_sequence,
                        occurred_at=item.occurred_at,
                    )
                    for item in links
                )
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("product serial already exists") from exc

    def list_execution_sessions(self, product_serial: str) -> list[ExecutionSession]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ExecutionSessionRow)
                .where(ExecutionSessionRow.product_serial == product_serial)
                .order_by(ExecutionSessionRow.started_at)
            ).all()
            return [_execution_session_to_domain(row) for row in rows]

    def list_material_consumptions(self, session_id: str) -> list[MaterialConsumption]:
        with self._sessions() as session:
            rows = session.scalars(
                select(MaterialConsumptionRow)
                .where(MaterialConsumptionRow.session_id == session_id)
                .order_by(MaterialConsumptionRow.recorded_at)
            ).all()
            return [_material_consumption_to_domain(row) for row in rows]

    def add_execution_session_atomically(
        self,
        execution: ExecutionSession,
        materials: list[MaterialConsumption],
        links: list[GenealogyLink],
        event: DomainEvent,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(
                    ExecutionSessionRow(
                        session_id=execution.session_id,
                        product_serial=execution.product_serial,
                        work_order_id=execution.work_order_id,
                        operation_sequence=execution.operation_sequence,
                        operator_id=execution.operator_id,
                        equipment_id=execution.equipment_id,
                        started_at=execution.started_at,
                        ended_at=execution.ended_at,
                        created_at=execution.created_at,
                        resource_context=[
                            {
                                "resource_type": item.resource_type,
                                "resource_id": item.resource_id,
                                "revision": item.revision,
                                "status": item.status,
                                "life_remaining_percent": item.life_remaining_percent,
                            }
                            for item in execution.resources
                        ],
                    )
                )
                session.flush()
                session.add_all(
                    MaterialConsumptionRow(
                        consumption_id=item.consumption_id,
                        session_id=item.session_id,
                        material_lot=item.material_lot,
                        quantity=item.quantity,
                        unit=item.unit,
                        recorded_at=item.recorded_at,
                    )
                    for item in materials
                )
                session.add_all(
                    GenealogyLinkRow(
                        link_id=item.link_id,
                        product_serial=item.product_serial,
                        relation_type=item.relation_type,
                        object_type=item.object_type,
                        object_id=item.object_id,
                        operation_sequence=item.operation_sequence,
                        occurred_at=item.occurred_at,
                    )
                    for item in links
                )
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("execution session or genealogy fact already exists") from exc

    def get_manufacturing_resource(
        self, resource_type: str, resource_id: str, revision: str = ""
    ) -> ManufacturingResource | None:
        key = f"{resource_type.strip().upper()}:{resource_id.strip().upper()}:{revision.strip().upper()}"
        with self._sessions() as session:
            row = session.get(ManufacturingResourceRow, key)
            return _manufacturing_resource_to_domain(row) if row else None

    def list_manufacturing_resources(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
    ) -> list[ManufacturingResource]:
        with self._sessions() as session:
            statement = select(ManufacturingResourceRow)
            if query:
                pattern = f"%{query}%"
                statement = statement.where(
                    or_(
                        ManufacturingResourceRow.resource_id.ilike(pattern),
                        ManufacturingResourceRow.name.ilike(pattern),
                    )
                )
            if resource_type:
                statement = statement.where(ManufacturingResourceRow.resource_type == resource_type)
            if status:
                statement = statement.where(ManufacturingResourceRow.status == status)
            rows = session.scalars(
                statement.order_by(
                    ManufacturingResourceRow.updated_at.desc(),
                    ManufacturingResourceRow.resource_key.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [_manufacturing_resource_to_domain(row) for row in rows]

    def count_manufacturing_resources(
        self,
        query: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
    ) -> int:
        with self._sessions() as session:
            statement = select(func.count()).select_from(ManufacturingResourceRow)
            if query:
                pattern = f"%{query}%"
                statement = statement.where(
                    or_(
                        ManufacturingResourceRow.resource_id.ilike(pattern),
                        ManufacturingResourceRow.name.ilike(pattern),
                    )
                )
            if resource_type:
                statement = statement.where(ManufacturingResourceRow.resource_type == resource_type)
            if status:
                statement = statement.where(ManufacturingResourceRow.status == status)
            return int(session.scalar(statement) or 0)

    def summarize_manufacturing_resources(self) -> dict[str, int]:
        with self._sessions() as session:
            type_rows = session.execute(
                select(ManufacturingResourceRow.resource_type, func.count()).group_by(
                    ManufacturingResourceRow.resource_type
                )
            ).all()
            status_rows = session.execute(
                select(ManufacturingResourceRow.status, func.count()).group_by(
                    ManufacturingResourceRow.status
                )
            ).all()
            result = {
                "TOTAL": int(
                    session.scalar(select(func.count()).select_from(ManufacturingResourceRow)) or 0
                ),
                "SOURCE_COUNT": int(
                    session.scalar(
                        select(func.count(func.distinct(ManufacturingResourceRow.source_system)))
                    )
                    or 0
                ),
            }
            result.update({f"TYPE:{kind}": int(count) for kind, count in type_rows})
            result.update({f"STATUS:{status}": int(count) for status, count in status_rows})
            return result

    def add_manufacturing_resource_atomically(
        self, resource: ManufacturingResource, event: DomainEvent
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(
                    ManufacturingResourceRow(
                        resource_key=resource.key,
                        resource_type=resource.resource_type,
                        resource_id=resource.resource_id,
                        revision=resource.revision,
                        name=resource.name,
                        status=resource.status,
                        life_remaining_percent=resource.life_remaining_percent,
                        calibration_due_at=resource.calibration_due_at,
                        source_system=resource.source_system,
                        external_reference=resource.external_reference,
                        source_updated_at=resource.source_updated_at,
                        version=resource.version,
                        created_at=resource.created_at,
                        updated_at=resource.updated_at,
                    )
                )
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("manufacturing resource already exists") from exc

    def update_manufacturing_resource_atomically(
        self, resource: ManufacturingResource, expected_version: int, event: DomainEvent
    ) -> None:
        with self._sessions.begin() as session:
            result = session.execute(
                update(ManufacturingResourceRow)
                .where(
                    ManufacturingResourceRow.resource_key == resource.key,
                    ManufacturingResourceRow.version == expected_version,
                )
                .values(
                    status=resource.status,
                    life_remaining_percent=resource.life_remaining_percent,
                    calibration_due_at=resource.calibration_due_at,
                    source_updated_at=resource.source_updated_at,
                    version=resource.version,
                    updated_at=resource.updated_at,
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                raise InvalidTransition("manufacturing resource version changed")
            session.add_all(_event_rows([event]))

    def add_manufacturing_resources_atomically(
        self, resources: list[ManufacturingResource], events: list[DomainEvent]
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add_all(
                    ManufacturingResourceRow(
                        resource_key=item.key,
                        resource_type=item.resource_type,
                        resource_id=item.resource_id,
                        revision=item.revision,
                        name=item.name,
                        status=item.status,
                        life_remaining_percent=item.life_remaining_percent,
                        calibration_due_at=item.calibration_due_at,
                        source_system=item.source_system,
                        external_reference=item.external_reference,
                        source_updated_at=item.source_updated_at,
                        version=item.version,
                        created_at=item.created_at,
                        updated_at=item.updated_at,
                    )
                    for item in resources
                )
                session.add_all(_event_rows(events))
        except IntegrityError as exc:
            raise IdempotencyConflict(
                "manufacturing resource batch contains duplicate key"
            ) from exc

    def record_connector_receipt(self, receipt: ConnectorReceipt) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(
                    ConnectorReceiptRow(
                        nonce=receipt.nonce,
                        key_id=receipt.key_id,
                        request_digest=receipt.request_digest,
                        received_at=receipt.received_at,
                    )
                )
        except IntegrityError as exc:
            raise IdempotencyConflict("connector nonce was already used") from exc


_POSTGRES_OPERATION_PROJECTION_AUDIT = """
with legacy as (
    select
        work_order.work_order_id,
        (item.value ->> 'sequence')::integer as sequence,
        item.value ->> 'operationCode' as operation_code,
        item.value ->> 'operationName' as operation_name,
        item.value ->> 'workCenterId' as work_center_id,
        (item.value ->> 'plannedQuantity')::integer as planned_quantity,
        coalesce(item.value ->> 'status', 'PENDING') as status,
        item.value ->> 'assignedResourceId' as assigned_resource_id,
        coalesce((item.value ->> 'goodQuantity')::integer, 0) as good_quantity,
        coalesce((item.value ->> 'scrapQuantity')::integer, 0) as scrap_quantity
    from work_orders as work_order
    cross join lateral jsonb_array_elements(work_order.operations::jsonb) as item(value)
), compared as (
    select
        legacy.work_order_id as legacy_id,
        operation.work_order_id as normalized_id,
        legacy.operation_code is distinct from operation.operation_code
            or legacy.operation_name is distinct from operation.operation_name
            or legacy.work_center_id is distinct from operation.work_center_id
            or legacy.planned_quantity is distinct from operation.planned_quantity
            or legacy.status is distinct from operation.status
            or legacy.assigned_resource_id is distinct from operation.assigned_resource_id
            or legacy.good_quantity is distinct from operation.good_quantity
            or legacy.scrap_quantity is distinct from operation.scrap_quantity as differs
    from legacy
    full outer join work_order_operations as operation
        on operation.work_order_id = legacy.work_order_id
        and operation.sequence = legacy.sequence
)
select
    (select count(1) from legacy) as legacy_count,
    (select count(1) from work_order_operations) as normalized_count,
    count(1) filter (
        where legacy_id is not null and normalized_id is not null and differs
    ) as mismatch_count,
    count(1) filter (where legacy_id is null and normalized_id is not null) as extra_count
from compared
"""


def _operation_dict_signature(item: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(item["operationCode"]),
        str(item["operationName"]),
        str(item["workCenterId"]),
        int(item["plannedQuantity"]),
        str(item.get("status", "PENDING")),
        item.get("assignedResourceId"),
        int(item.get("goodQuantity", 0)),
        int(item.get("scrapQuantity", 0)),
    )


def _operation_row_signature(item: WorkOrderOperationRow) -> tuple[object, ...]:
    return (
        item.operation_code,
        item.operation_name,
        item.work_center_id,
        item.planned_quantity,
        item.status,
        item.assigned_resource_id,
        item.good_quantity,
        item.scrap_quantity,
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
        "operations": [_operation_to_dict(operation) for operation in item.operations],
        "status": item.status.value,
        "version": item.version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _to_row(item: WorkOrder) -> WorkOrderRow:
    return WorkOrderRow(work_order_id=item.work_order_id, **_row_values(item))


def _operation_rows(item: WorkOrder) -> list[WorkOrderOperationRow]:
    return [
        WorkOrderOperationRow(
            work_order_id=item.work_order_id,
            sequence=operation.sequence,
            operation_code=operation.operation_code,
            operation_name=operation.operation_name,
            work_center_id=operation.work_center_id,
            planned_quantity=operation.planned_quantity,
            status=operation.status.value,
            assigned_resource_id=operation.assigned_resource_id,
            good_quantity=operation.good_quantity,
            scrap_quantity=operation.scrap_quantity,
        )
        for operation in item.operations
    ]


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
        operations=[_operation_from_dict(item) for item in row.operations],
    )


def _operation_to_dict(item: ProductionOperation) -> dict[str, Any]:
    return {
        "sequence": item.sequence,
        "operationCode": item.operation_code,
        "operationName": item.operation_name,
        "workCenterId": item.work_center_id,
        "plannedQuantity": item.planned_quantity,
        "status": item.status.value,
        "assignedResourceId": item.assigned_resource_id,
        "goodQuantity": item.good_quantity,
        "scrapQuantity": item.scrap_quantity,
    }


def _operation_from_dict(item: dict[str, Any]) -> ProductionOperation:
    return ProductionOperation(
        sequence=int(item["sequence"]),
        operation_code=str(item["operationCode"]),
        operation_name=str(item["operationName"]),
        work_center_id=str(item["workCenterId"]),
        planned_quantity=int(item["plannedQuantity"]),
        status=OperationStatus(str(item.get("status", "PENDING"))),
        assigned_resource_id=item.get("assignedResourceId"),
        good_quantity=int(item.get("goodQuantity", 0)),
        scrap_quantity=int(item.get("scrapQuantity", 0)),
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


def _equipment_values(item: Equipment, *, include_id: bool = True) -> dict[str, Any]:
    values: dict[str, Any] = {
        "code": item.code,
        "name": item.name,
        "workshop_id": item.workshop_id,
        "work_center_id": item.work_center_id,
        "protocol": item.protocol,
        "state": item.state.value,
        "version": item.version,
        "last_seen_at": item.last_seen_at,
        "spindle_load_percent": item.spindle_load_percent,
        "temperature_celsius": item.temperature_celsius,
        "alarm_code": item.alarm_code,
        "downtime_reason": item.downtime_reason,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_id:
        values["equipment_id"] = item.equipment_id
    return values


def _equipment_to_domain(row: EquipmentRow) -> Equipment:
    return Equipment(
        equipment_id=row.equipment_id,
        code=row.code,
        name=row.name,
        workshop_id=row.workshop_id,
        work_center_id=row.work_center_id,
        protocol=row.protocol,
        state=EquipmentState(row.state),
        version=row.version,
        last_seen_at=row.last_seen_at,
        spindle_load_percent=row.spindle_load_percent,
        temperature_celsius=row.temperature_celsius,
        alarm_code=row.alarm_code,
        downtime_reason=row.downtime_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _proposal_values(item: AgentProposal, *, include_id: bool = True) -> dict[str, Any]:
    values: dict[str, Any] = {
        "fingerprint": item.fingerprint,
        "agent_id": item.agent_id,
        "action": item.action,
        "risk": item.risk,
        "status": item.status.value,
        "work_order_id": item.work_order_id,
        "work_order_version": item.work_order_version,
        "operation_sequence": item.operation_sequence,
        "equipment_id": item.equipment_id,
        "equipment_version": item.equipment_version,
        "diagnosis": item.diagnosis,
        "rationale": item.rationale,
        "narrative_source": item.narrative_source,
        "model_name": item.model_name,
        "quality_risk_score": item.quality_risk_score,
        "quality_risk_level": item.quality_risk_level,
        "recommended_sample_size": item.recommended_sample_size,
        "assessment_factors": list(item.assessment_factors),
        "assessment_ruleset": item.assessment_ruleset,
        "approved_by": item.approved_by,
        "approval_reason": item.approval_reason,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_id:
        values["proposal_id"] = item.proposal_id
    return values


def _proposal_to_domain(row: AgentProposalRow) -> AgentProposal:
    return AgentProposal(
        proposal_id=row.proposal_id,
        fingerprint=row.fingerprint,
        agent_id=row.agent_id,
        action=row.action,
        risk=row.risk,
        status=ProposalStatus(row.status),
        work_order_id=row.work_order_id,
        work_order_version=row.work_order_version,
        operation_sequence=row.operation_sequence,
        equipment_id=row.equipment_id,
        equipment_version=row.equipment_version,
        diagnosis=row.diagnosis,
        rationale=row.rationale,
        narrative_source=row.narrative_source,
        model_name=row.model_name,
        quality_risk_score=row.quality_risk_score,
        quality_risk_level=row.quality_risk_level,
        recommended_sample_size=row.recommended_sample_size,
        assessment_factors=tuple(row.assessment_factors),
        assessment_ruleset=row.assessment_ruleset,
        approved_by=row.approved_by,
        approval_reason=row.approval_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _inspection_values(item: QualityInspection, *, include_id: bool = True) -> dict[str, Any]:
    values = {
        "work_order_id": item.work_order_id,
        "operation_sequence": item.operation_sequence,
        "sample_size": item.sample_size,
        "status": item.status.value,
        "version": item.version,
        "result": item.result,
        "defect_code": item.defect_code,
        "notes": item.notes,
        "rework_route": list(item.rework_route),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "gauge_id": item.gauge_id,
        "calibration_due_at": item.calibration_due_at,
        "measurement_recorded_at": item.measurement_recorded_at,
    }
    if include_id:
        values["inspection_id"] = item.inspection_id
    return values


def _inspection_to_domain(row: QualityInspectionRow) -> QualityInspection:
    return QualityInspection(
        row.inspection_id,
        row.work_order_id,
        row.operation_sequence,
        row.sample_size,
        InspectionStatus(row.status),
        row.version,
        row.result,
        row.defect_code,
        row.notes,
        list(row.rework_route),
        row.created_at,
        row.updated_at,
        row.gauge_id,
        row.calibration_due_at,
        row.measurement_recorded_at,
    )


def _quality_policy_values(
    item: QualityRiskPolicy, *, include_id: bool = True
) -> dict[str, Any]:
    values = {
        "policy_key": item.policy_key,
        "version": item.version,
        "record_version": item.record_version,
        "status": item.status.value,
        "name": item.name,
        "scope": item.scope.value,
        "product_revision_id": item.product_revision_id,
        "operation_code": item.operation_code,
        "configuration": item.configuration,
        "change_reason": item.change_reason,
        "created_by": item.created_by,
        "simulation_run_id": item.simulation_run_id,
        "simulation_summary": item.simulation_summary,
        "simulated_by": item.simulated_by,
        "simulated_at": item.simulated_at,
        "submitted_by": item.submitted_by,
        "approved_by": item.approved_by,
        "approval_reason": item.approval_reason,
        "effective_from": item.effective_from,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_id:
        values["policy_id"] = item.policy_id
    return values


def _quality_policy_to_domain(row: QualityRiskPolicyRow) -> QualityRiskPolicy:
    return QualityRiskPolicy(
        row.policy_id,
        row.policy_key,
        row.version,
        row.record_version,
        QualityPolicyStatus(row.status),
        row.name,
        QualityPolicyScope(row.scope),
        row.product_revision_id,
        row.operation_code,
        row.configuration,
        row.change_reason,
        row.created_by,
        row.simulation_run_id,
        row.simulation_summary,
        row.simulated_by,
        row.simulated_at,
        row.submitted_by,
        row.approved_by,
        row.approval_reason,
        row.effective_from,
        row.created_at,
        row.updated_at,
    )


def _planning_resource_values(
    item: PlanningResource, *, include_id: bool = True
) -> dict[str, Any]:
    values = {
        "code": item.code,
        "name": item.name,
        "resource_type": item.resource_type.value,
        "workshop_id": item.workshop_id,
        "work_center_id": item.work_center_id,
        "daily_capacity_minutes": item.daily_capacity_minutes,
        "overtime_capacity_minutes": item.overtime_capacity_minutes,
        "capability_codes": list(item.capability_codes),
        "active": item.active,
        "version": item.version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_id:
        values["resource_id"] = item.resource_id
    return values


def _planning_resource_to_domain(row: PlanningResourceRow) -> PlanningResource:
    return PlanningResource(
        row.resource_id,
        row.code,
        row.name,
        PlanningResourceType(row.resource_type),
        row.workshop_id,
        row.work_center_id,
        row.daily_capacity_minutes,
        row.overtime_capacity_minutes,
        tuple(row.capability_codes),
        row.active,
        row.version,
        row.created_at,
        row.updated_at,
    )


def _schedule_plan_values(item: SchedulePlan, *, include_id: bool = True) -> dict[str, Any]:
    values = {
        "plan_number": item.plan_number,
        "workshop_id": item.workshop_id,
        "horizon_start": item.horizon_start,
        "horizon_days": item.horizon_days,
        "use_overtime": item.use_overtime,
        "generation_parameters": item.generation_parameters,
        "assignments": list(item.assignments),
        "shortages": list(item.shortages),
        "metrics": item.metrics,
        "status": item.status.value,
        "record_version": item.record_version,
        "created_by": item.created_by,
        "submitted_by": item.submitted_by,
        "approved_by": item.approved_by,
        "approval_reason": item.approval_reason,
        "published_by": item.published_by,
        "withdrawn_by": item.withdrawn_by,
        "withdrawal_reason": item.withdrawal_reason,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_id:
        values["plan_id"] = item.plan_id
    return values


def _schedule_plan_to_domain(row: SchedulePlanRow) -> SchedulePlan:
    return SchedulePlan(
        row.plan_id,
        row.plan_number,
        row.workshop_id,
        row.horizon_start,
        row.horizon_days,
        row.use_overtime,
        row.generation_parameters,
        tuple(row.assignments),
        tuple(row.shortages),
        row.metrics,
        SchedulePlanStatus(row.status),
        row.record_version,
        row.created_by,
        row.submitted_by,
        row.approved_by,
        row.approval_reason,
        row.published_by,
        row.withdrawn_by,
        row.withdrawal_reason,
        row.created_at,
        row.updated_at,
    )


def _product_unit_to_domain(row: ProductUnitRow) -> ProductUnit:
    return ProductUnit(
        row.product_serial,
        row.work_order_id,
        row.product_revision_id,
        row.genealogy_status,
        row.created_at,
    )


def _genealogy_link_to_domain(row: GenealogyLinkRow) -> GenealogyLink:
    return GenealogyLink(
        row.link_id,
        row.product_serial,
        row.relation_type,
        row.object_type,
        row.object_id,
        row.operation_sequence,
        row.occurred_at,
    )


def _execution_session_to_domain(row: ExecutionSessionRow) -> ExecutionSession:
    from autonomous_mes.domain.genealogy import ProcessResourceEvidence

    return ExecutionSession(
        row.session_id,
        row.product_serial,
        row.work_order_id,
        row.operation_sequence,
        row.operator_id,
        row.equipment_id,
        row.started_at,
        row.ended_at,
        row.created_at,
        tuple(ProcessResourceEvidence(**item) for item in row.resource_context),
    )


def _material_consumption_to_domain(row: MaterialConsumptionRow) -> MaterialConsumption:
    return MaterialConsumption(
        row.consumption_id,
        row.session_id,
        row.material_lot,
        row.quantity,
        row.unit,
        row.recorded_at,
    )


def _manufacturing_resource_to_domain(row: ManufacturingResourceRow) -> ManufacturingResource:
    return ManufacturingResource(
        row.resource_type,
        row.resource_id,
        row.revision,
        row.name,
        row.status,
        row.life_remaining_percent,
        row.calibration_due_at,
        row.source_system,
        row.external_reference,
        row.source_updated_at,
        row.version,
        row.created_at,
        row.updated_at,
    )
