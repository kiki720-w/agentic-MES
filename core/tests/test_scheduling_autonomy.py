from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from autonomous_mes.application.scheduling import (
    IngestSchedulingSnapshotCommand,
    SchedulingApplicationService,
)
from autonomous_mes.application.scheduling_agent import SchedulingAgent, SchedulingAgentCommand
from autonomous_mes.application.scheduling_autonomy import (
    AutonomyMode,
    L4SchedulingPolicy,
    SchedulingAutonomyRuntime,
    SimulatorScheduleExecution,
)
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore

NOW = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


def runtime(mode=AutonomyMode.AUTONOMOUS, *, age_seconds=0, execution=None):
    store = InMemoryWorkOrderStore()
    scheduling = SchedulingApplicationService(store)
    observed = NOW - timedelta(seconds=age_seconds)
    scheduling.ingest_snapshot(IngestSchedulingSnapshotCommand(
        "cloud-mes", "WS-1", "rev-42", observed,
        {
            "workOrders": [{
                "externalId": "order-1", "code": "WO-L4-1", "productionOrderId": "PO-1",
                "quantity": 10, "dueAt": (NOW + timedelta(days=4)).isoformat(),
                "priority": 90, "productRevisionId": "PART-A", "routingRevisionId": "RT-A",
                "bomRevisionId": "BOM-A", "status": "RELEASED", "version": 7,
                "materialReady": True, "qualityHold": False,
                "operations": [{
                    "sequence": 10, "operationCode": "TURN", "operationName": "Turning",
                    "workCenterId": "WC-TURN", "plannedQuantity": 10, "status": "PENDING",
                    "minutesPerUnit": 12, "setupMinutes": 30,
                }],
            }],
            "resources": [{
                "externalId": "cell-1", "code": "CELL-1", "name": "Turning cell",
                "resourceType": "CELL", "workCenterId": "WC-TURN",
                "dailyCapacityMinutes": 480, "overtimeCapacityMinutes": 600,
                "capabilityCodes": ["TURN"], "active": True, "state": "RUNNING",
            }],
        },
        "connector:key-1", str(uuid4()),
    ))
    agent = SchedulingAgent(
        store, enabled=True, auto_submit=True, agent_id="scheduling-agent-l4-v1",
        agent_level="L4_TARGET", publication_authority="PREAUTHORIZED_POLICY_ENGINE",
    )
    policy = L4SchedulingPolicy(mode=mode)
    adapter = execution if execution is not None else SimulatorScheduleExecution()
    result = SchedulingAutonomyRuntime(store, agent, policy, adapter, clock=lambda: NOW)
    command = SchedulingAgentCommand("WS-1", date(2026, 9, 14), 6, False, 30, {})
    return store, result, command, adapter


def test_autonomous_loop_executes_verifies_and_deduplicates():
    store, agent_runtime, command, adapter = runtime()

    first = agent_runtime.run_once(command, "TEST_EVENT")
    second = agent_runtime.run_once(command, "TEST_EVENT")

    assert first["state"] == "VERIFIED"
    assert first["decision"] == "AUTONOMOUSLY_EXECUTED"
    assert first["publishedPlan"]["status"] == "PUBLISHED"
    assert first["policyDecision"]["authorized"] is True
    assert first["executionReceipt"]["sourceRevision"] == "rev-42"
    assert second["decision"] == "NO_CHANGE_ALREADY_PUBLISHED"
    assert len(adapter._applied) == 1
    events = [item["eventType"] for item in store.list_outbox()]
    assert events[-4:] == [
        "SchedulePlanGenerated", "SchedulePlanSubmitted",
        "SchedulePlanApproved", "SchedulePlanPublished",
    ]


def test_shadow_mode_runs_full_policy_without_execution():
    store, agent_runtime, command, adapter = runtime(AutonomyMode.SHADOW)

    result = agent_runtime.run_once(command)

    assert result["state"] == "SHADOW_COMPLETE"
    assert result["decision"] == "WOULD_EXECUTE"
    assert adapter._applied == {}
    assert store.list_schedule_plans("WS-1", 1)[0].status.value == "PENDING_APPROVAL"


def test_stale_snapshot_is_escalated_before_execution():
    _, agent_runtime, command, adapter = runtime(age_seconds=301)

    result = agent_runtime.run_once(command)

    assert result["state"] == "ESCALATED"
    assert result["decision"] == "BLOCKED_BY_POLICY"
    assert "SNAPSHOT_STALE" in result["policyDecision"]["blockers"]
    assert adapter._applied == {}


def test_kill_switch_stops_before_observation_and_supervisor_can_resume():
    _, agent_runtime, command, _ = runtime()
    agent_runtime.kill()

    stopped = agent_runtime.run_once(command)
    agent_runtime.resume()
    resumed = agent_runtime.run_once(command)

    assert stopped["decision"] == "KILL_SWITCH_ACTIVE"
    assert stopped["transitions"] == ["OBSERVING", "STOPPED"]
    assert resumed["decision"] == "AUTONOMOUSLY_EXECUTED"


def test_failed_remote_verification_rolls_back_and_never_publishes():
    class FailingVerification(SimulatorScheduleExecution):
        def verify(self, receipt, plan):
            return False

    adapter = FailingVerification()
    store, agent_runtime, command, _ = runtime(execution=adapter)

    result = agent_runtime.run_once(command)

    assert result["state"] == "ROLLED_BACK"
    assert result["rollbackVerified"] is True
    assert adapter._applied == {}
    assert store.list_schedule_plans("WS-1", 1)[0].status.value == "PENDING_APPROVAL"


def test_uncertain_execution_failure_is_critical_and_never_publishes():
    class FailingApply(SimulatorScheduleExecution):
        def apply(self, plan, idempotency_key, expected_source_revision):
            raise TimeoutError("uncertain external outcome")

    store, agent_runtime, command, _ = runtime(execution=FailingApply())

    result = agent_runtime.run_once(command)

    assert result["state"] == "CRITICAL"
    assert result["decision"] == "EXECUTION_APPLY_FAILED"
    assert result["error"] == "TimeoutError"
    assert store.list_schedule_plans("WS-1", 1)[0].status.value == "PENDING_APPROVAL"


def test_policy_rejects_demo_input_fallbacks_and_existing_plan_replacement():
    policy = L4SchedulingPolicy(mode=AutonomyMode.AUTONOMOUS)
    decision = policy.evaluate({
        "status": "PENDING_APPROVAL", "assignments": [{
            "workOrderId": "o-1", "demandSource": "FALLBACK_RATE",
        }],
        "metrics": {"lateOrderCount": 0, "shortageCount": 0},
        "generationParameters": {"inputSource": {"type": "SIMULATOR_PROJECTION"}},
        "useOvertime": False,
    }, ["MISSING_FROZEN_ROUTE"], now=NOW, has_other_published_plan=True)

    assert decision["authorized"] is False
    assert set(decision["blockers"]) >= {
        "PROCESS_STANDARD_REQUIRED", "EXTERNAL_SNAPSHOT_REQUIRED",
        "ATOMIC_PLAN_REPLACEMENT_REQUIRED", "FROZEN_ROUTE_INCOMPLETE",
    }
