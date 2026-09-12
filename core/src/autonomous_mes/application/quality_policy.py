from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from autonomous_mes.domain.errors import NotFound, ValidationError
from autonomous_mes.domain.quality_policy import (
    QualityPolicyScope,
    QualityPolicyStatus,
    QualityRiskPolicy,
)

from .ports import QualityPolicyStore
from .quality_risk import validate_quality_risk_configuration


@dataclass(frozen=True)
class CreateQualityPolicyCommand:
    name: str
    scope: str
    product_revision_id: str | None
    operation_code: str | None
    configuration: dict[str, Any]
    change_reason: str
    actor_id: str
    rollback_from_policy_id: str | None = None


class QualityPolicyApplicationService:
    def __init__(self, store: QualityPolicyStore) -> None:
        self._store = store

    def create_draft(self, command: CreateQualityPolicyCommand) -> dict[str, Any]:
        validate_quality_risk_configuration(command.configuration)
        try:
            scope = QualityPolicyScope(command.scope.strip().upper())
        except ValueError as exc:
            raise ValidationError("unsupported quality policy scope") from exc
        key_parts = {
            "GLOBAL": (None, None),
            "PRODUCT": (command.product_revision_id, None),
            "OPERATION": (None, command.operation_code),
            "PRODUCT_OPERATION": (command.product_revision_id, command.operation_code),
        }[scope.value]
        product = key_parts[0].strip().upper() if key_parts[0] else None
        operation = key_parts[1].strip().upper() if key_parts[1] else None
        key = f"{scope.value}:{product or '*'}:{operation or '*'}"
        version = self._store.next_quality_policy_version(key)
        policy, event = QualityRiskPolicy.create_draft(
            version=version,
            name=command.name,
            scope=scope,
            product_revision_id=product,
            operation_code=operation,
            configuration=command.configuration,
            change_reason=command.change_reason,
            actor_id=command.actor_id,
            correlation_id=str(uuid4()),
            rollback_from_policy_id=command.rollback_from_policy_id,
        )
        self._store.add_quality_policy_atomically(policy, event)
        return serialize_quality_policy(policy)

    def submit(self, policy_id: str, expected_version: int, actor_id: str) -> dict[str, Any]:
        current = self._require(policy_id)
        changed, event = current.submit(expected_version, actor_id, str(uuid4()))
        self._store.update_quality_policy_atomically(changed, current.record_version, event)
        return serialize_quality_policy(changed)

    def approve(
        self,
        policy_id: str,
        expected_version: int,
        actor_id: str,
        reason: str,
        effective_from: datetime,
    ) -> dict[str, Any]:
        current = self._require(policy_id)
        changed, event = current.approve(
            expected_version, actor_id, reason, effective_from, str(uuid4())
        )
        self._store.update_quality_policy_atomically(changed, current.record_version, event)
        return serialize_quality_policy(changed)

    def rollback_draft(
        self, policy_id: str, actor_id: str, reason: str
    ) -> dict[str, Any]:
        target = self._require(policy_id)
        if target.status is not QualityPolicyStatus.APPROVED:
            raise ValidationError("rollback source must be an approved policy")
        return self.create_draft(
            CreateQualityPolicyCommand(
                name=f"{target.name} · 回滚副本 v{target.version}",
                scope=target.scope.value,
                product_revision_id=target.product_revision_id,
                operation_code=target.operation_code,
                configuration=target.configuration,
                change_reason=reason,
                actor_id=actor_id,
                rollback_from_policy_id=target.policy_id,
            )
        )

    def list(self, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise ValidationError("limit must be between 1 and 500")
        items = self._store.list_quality_policies(limit)
        return {"items": [serialize_quality_policy(item) for item in items], "count": len(items)}

    def _require(self, policy_id: str) -> QualityRiskPolicy:
        item = self._store.get_quality_policy(policy_id)
        if item is None:
            raise NotFound("quality risk policy not found")
        return item


def serialize_quality_policy(item: QualityRiskPolicy) -> dict[str, Any]:
    return {
        "policyId": item.policy_id,
        "policyKey": item.policy_key,
        "version": item.version,
        "recordVersion": item.record_version,
        "status": item.status.value,
        "name": item.name,
        "scope": item.scope.value,
        "productRevisionId": item.product_revision_id,
        "operationCode": item.operation_code,
        "configuration": item.configuration,
        "changeReason": item.change_reason,
        "createdBy": item.created_by,
        "submittedBy": item.submitted_by,
        "approvedBy": item.approved_by,
        "approvalReason": item.approval_reason,
        "effectiveFrom": item.effective_from.isoformat() if item.effective_from else None,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }
