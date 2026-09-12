from datetime import UTC, datetime

import pytest

from autonomous_mes.application.quality_policy import (
    CreateQualityPolicyCommand,
    QualityPolicyApplicationService,
)
from autonomous_mes.application.quality_risk import default_quality_risk_configuration
from autonomous_mes.domain.errors import ValidationError
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


def _draft(
    service: QualityPolicyApplicationService,
    *,
    scope: str = "GLOBAL",
    product: str | None = None,
    operation: str | None = None,
) -> dict[str, object]:
    return service.create_draft(
        CreateQualityPolicyCommand(
            name="Governed quality risk",
            scope=scope,
            product_revision_id=product,
            operation_code=operation,
            configuration=default_quality_risk_configuration(),
            change_reason="Controlled test change",
            actor_id="master-admin",
        )
    )


def _approve(
    service: QualityPolicyApplicationService, draft: dict[str, object]
) -> dict[str, object]:
    submitted = service.submit(
        str(draft["policyId"]), int(draft["recordVersion"]), "master-admin"
    )
    return service.approve(
        str(submitted["policyId"]),
        int(submitted["recordVersion"]),
        "quality-manager",
        "Validated against known lots",
        datetime.now(UTC),
    )


def test_policy_lifecycle_is_versioned_auditable_and_rollback_is_a_draft() -> None:
    store = InMemoryWorkOrderStore()
    service = QualityPolicyApplicationService(store)

    approved = _approve(service, _draft(service))
    assert approved["status"] == "APPROVED"
    assert approved["version"] == 1
    assert store.resolve_quality_risk_policy("ANY-PRODUCT", "ANY-OP", datetime.now(UTC))

    rollback = service.rollback_draft(
        str(approved["policyId"]), "master-admin", "Restore the validated policy"
    )
    assert rollback["status"] == "DRAFT"
    assert rollback["version"] == 2
    assert rollback["configuration"] == approved["configuration"]

    event_types = [item["eventType"] for item in store.list_outbox()]
    assert event_types == [
        "QualityRiskPolicyDraftCreated",
        "QualityRiskPolicySubmitted",
        "QualityRiskPolicyApproved",
        "QualityRiskPolicyDraftCreated",
    ]


def test_most_specific_effective_policy_wins() -> None:
    store = InMemoryWorkOrderStore()
    service = QualityPolicyApplicationService(store)
    global_policy = _approve(service, _draft(service))
    specific_policy = _approve(
        service,
        _draft(
            service,
            scope="PRODUCT_OPERATION",
            product="pr-shaft-a",
            operation="turn",
        ),
    )

    resolved = store.resolve_quality_risk_policy("PR-SHAFT-A", "TURN", datetime.now(UTC))
    assert resolved is not None
    assert resolved.policy_id == specific_policy["policyId"]
    other = store.resolve_quality_risk_policy("OTHER", "TURN", datetime.now(UTC))
    assert other is not None
    assert other.policy_id == global_policy["policyId"]


def test_invalid_policy_configuration_is_rejected() -> None:
    store = InMemoryWorkOrderStore()
    service = QualityPolicyApplicationService(store)
    invalid = default_quality_risk_configuration()
    invalid["bands"] = {"mediumMin": 80, "highMin": 20}

    with pytest.raises(ValidationError):
        service.create_draft(
            CreateQualityPolicyCommand(
                "Invalid",
                "GLOBAL",
                None,
                None,
                invalid,
                "test",
                "master-admin",
            )
        )
