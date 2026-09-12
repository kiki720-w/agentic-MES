from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from hashlib import sha256
from json import dumps
from typing import Any
from uuid import uuid4

from .errors import InvalidTransition, ValidationError
from .events import DomainEvent, utc_now


class QualityPolicyStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"


class QualityPolicyScope(str, Enum):
    GLOBAL = "GLOBAL"
    PRODUCT = "PRODUCT"
    OPERATION = "OPERATION"
    PRODUCT_OPERATION = "PRODUCT_OPERATION"


@dataclass(frozen=True)
class QualityRiskPolicy:
    policy_id: str
    policy_key: str
    version: int
    record_version: int
    status: QualityPolicyStatus
    name: str
    scope: QualityPolicyScope
    product_revision_id: str | None
    operation_code: str | None
    configuration: dict[str, Any]
    change_reason: str
    created_by: str
    simulation_run_id: str | None
    simulation_summary: dict[str, Any] | None
    simulated_by: str | None
    simulated_at: datetime | None
    submitted_by: str | None
    approved_by: str | None
    approval_reason: str | None
    effective_from: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create_draft(
        cls,
        *,
        version: int,
        name: str,
        scope: QualityPolicyScope,
        product_revision_id: str | None,
        operation_code: str | None,
        configuration: dict[str, Any],
        change_reason: str,
        actor_id: str,
        correlation_id: str,
        rollback_from_policy_id: str | None = None,
    ) -> tuple["QualityRiskPolicy", DomainEvent]:
        product, operation = _normalize_scope(scope, product_revision_id, operation_code)
        if version < 1 or not name.strip() or not change_reason.strip() or not actor_id.strip():
            raise ValidationError("policy version, name, change reason and creator are required")
        now = utc_now()
        item = cls(
            str(uuid4()),
            _policy_key(scope, product, operation),
            version,
            1,
            QualityPolicyStatus.DRAFT,
            name.strip(),
            scope,
            product,
            operation,
            configuration,
            change_reason.strip(),
            actor_id,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            now,
            now,
        )
        event = item._event(
            "QualityRiskPolicyDraftCreated",
            actor_id,
            correlation_id,
            {"rollbackFromPolicyId": rollback_from_policy_id}
            if rollback_from_policy_id
            else {},
        )
        return item, event

    def record_simulation(
        self,
        expected_record_version: int,
        actor_id: str,
        run_id: str,
        summary: dict[str, Any],
        correlation_id: str,
    ) -> tuple["QualityRiskPolicy", DomainEvent]:
        self._require(QualityPolicyStatus.DRAFT, expected_record_version)
        if not actor_id.strip() or not run_id.strip():
            raise ValidationError("simulation actor and run id are required")
        now = utc_now()
        changed = replace(
            self,
            simulation_run_id=run_id,
            simulation_summary=summary,
            simulated_by=actor_id,
            simulated_at=now,
            record_version=self.record_version + 1,
            updated_at=now,
        )
        return changed, changed._event(
            "QualityRiskPolicySimulated", actor_id, correlation_id, {"simulation": summary}
        )

    def submit(
        self, expected_record_version: int, actor_id: str, correlation_id: str
    ) -> tuple["QualityRiskPolicy", DomainEvent]:
        self._require(QualityPolicyStatus.DRAFT, expected_record_version)
        if self.simulation_summary is None or self.simulated_at is None:
            raise InvalidTransition("policy must pass a recorded simulation before submission")
        changed = replace(
            self,
            status=QualityPolicyStatus.PENDING_APPROVAL,
            record_version=self.record_version + 1,
            submitted_by=actor_id,
            updated_at=utc_now(),
        )
        return changed, changed._event("QualityRiskPolicySubmitted", actor_id, correlation_id)

    def approve(
        self,
        expected_record_version: int,
        actor_id: str,
        reason: str,
        effective_from: datetime,
        correlation_id: str,
    ) -> tuple["QualityRiskPolicy", DomainEvent]:
        self._require(QualityPolicyStatus.PENDING_APPROVAL, expected_record_version)
        if not reason.strip() or effective_from.tzinfo is None:
            raise ValidationError("approval reason and timezone-aware effective time are required")
        if actor_id in {self.created_by, self.submitted_by}:
            raise InvalidTransition("maker-checker requires a different quality approver")
        changed = replace(
            self,
            status=QualityPolicyStatus.APPROVED,
            record_version=self.record_version + 1,
            approved_by=actor_id,
            approval_reason=reason.strip(),
            effective_from=effective_from,
            updated_at=utc_now(),
        )
        return changed, changed._event("QualityRiskPolicyApproved", actor_id, correlation_id)

    def _require(self, status: QualityPolicyStatus, expected_record_version: int) -> None:
        if self.status is not status:
            raise InvalidTransition(f"policy must be {status.value}")
        if self.record_version != expected_record_version:
            raise InvalidTransition("quality policy version changed")

    def _event(
        self,
        event_type: str,
        actor_id: str,
        correlation_id: str,
        extra: dict[str, Any] | None = None,
    ) -> DomainEvent:
        payload = {
            "policyKey": self.policy_key,
            "policyVersion": self.version,
            "recordVersion": self.record_version,
            "status": self.status.value,
            "scope": self.scope.value,
            "productRevisionId": self.product_revision_id,
            "operationCode": self.operation_code,
            "configurationHash": sha256(
                dumps(self.configuration, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "actorId": actor_id,
            "effectiveFrom": self.effective_from.isoformat() if self.effective_from else None,
            "simulationRunId": self.simulation_run_id,
            **(extra or {}),
        }
        return DomainEvent.create(
            event_type,
            "QualityRiskPolicy",
            self.policy_id,
            payload,
            correlation_id,
        )


def _normalize_scope(
    scope: QualityPolicyScope,
    product_revision_id: str | None,
    operation_code: str | None,
) -> tuple[str | None, str | None]:
    product = product_revision_id.strip().upper() if product_revision_id else None
    operation = operation_code.strip().upper() if operation_code else None
    if scope is QualityPolicyScope.GLOBAL:
        return None, None
    if scope is QualityPolicyScope.PRODUCT and product and not operation:
        return product, None
    if scope is QualityPolicyScope.OPERATION and operation and not product:
        return None, operation
    if scope is QualityPolicyScope.PRODUCT_OPERATION and product and operation:
        return product, operation
    raise ValidationError("policy selectors do not match its scope")


def _policy_key(
    scope: QualityPolicyScope, product_revision_id: str | None, operation_code: str | None
) -> str:
    return f"{scope.value}:{product_revision_id or '*'}:{operation_code or '*'}"
