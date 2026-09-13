"""Policy-bounded L4 scheduling control loop."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import Event, Lock, Thread
from typing import Any, Protocol
from uuid import uuid4

from autonomous_mes.domain.errors import DomainError

from .ports import MesStore
from .scheduling import SchedulingApplicationService
from .scheduling_agent import SchedulingAgent, SchedulingAgentCommand

POLICY_ACTOR = "scheduling-autonomy-policy-v1"
EXECUTION_ACTOR = "scheduling-autonomy-executor-v1"


class AutonomyMode(str, Enum):
    SHADOW = "SHADOW"
    SUPERVISED = "SUPERVISED"
    AUTONOMOUS = "AUTONOMOUS"


@dataclass(frozen=True)
class L4SchedulingPolicy:
    mode: AutonomyMode = AutonomyMode.SHADOW
    max_assignments: int = 50
    max_affected_orders: int = 20
    max_late_orders: int = 0
    max_shortages: int = 0
    max_snapshot_age_seconds: int = 300
    allow_overtime: bool = False
    require_external_snapshot: bool = True
    require_process_standards: bool = True

    def __post_init__(self) -> None:
        if min(self.max_assignments, self.max_affected_orders,
               self.max_late_orders, self.max_shortages) < 0:
            raise ValueError("autonomy limits cannot be negative")
        if self.max_snapshot_age_seconds < 1:
            raise ValueError("snapshot age limit must be positive")

    def evaluate(
        self,
        plan: dict[str, Any],
        trigger_reasons: list[str],
        *,
        now: datetime,
        has_other_published_plan: bool,
    ) -> dict[str, Any]:
        blockers: list[str] = []
        assignments = plan.get("assignments", [])
        metrics = plan.get("metrics", {})
        parameters = plan.get("generationParameters", {})
        source = parameters.get("inputSource", {})
        affected_orders = {
            item.get("workOrderId") for item in assignments if item.get("workOrderId")
        }

        if plan.get("status") not in {"PENDING_APPROVAL", "PUBLISHED"}:
            blockers.append("PLAN_NOT_SUBMITTED")
        if not assignments:
            blockers.append("NO_ASSIGNMENTS")
        if len(assignments) > self.max_assignments:
            blockers.append("ASSIGNMENT_LIMIT_EXCEEDED")
        if len(affected_orders) > self.max_affected_orders:
            blockers.append("AFFECTED_ORDER_LIMIT_EXCEEDED")
        if int(metrics.get("lateOrderCount", 0)) > self.max_late_orders:
            blockers.append("LATE_ORDER_LIMIT_EXCEEDED")
        if int(metrics.get("shortageCount", 0)) > self.max_shortages:
            blockers.append("SHORTAGE_LIMIT_EXCEEDED")
        if plan.get("useOvertime") and not self.allow_overtime:
            blockers.append("OVERTIME_NOT_AUTHORIZED")
        if self.require_process_standards and any(
            item.get("demandSource") != "SNAPSHOT_PROCESS_STANDARD" for item in assignments
        ):
            blockers.append("PROCESS_STANDARD_REQUIRED")
        if self.require_external_snapshot and source.get("type") != "EXTERNAL_SCHEDULING_SNAPSHOT":
            blockers.append("EXTERNAL_SNAPSHOT_REQUIRED")
        observed_at = source.get("observedAt")
        snapshot_age: float | None = None
        if source.get("type") == "EXTERNAL_SCHEDULING_SNAPSHOT":
            try:
                observed = datetime.fromisoformat(str(observed_at))
                if observed.tzinfo is None:
                    raise ValueError("timezone required")
                snapshot_age = (now.astimezone(UTC) - observed.astimezone(UTC)).total_seconds()
                if snapshot_age < 0 or snapshot_age > self.max_snapshot_age_seconds:
                    blockers.append("SNAPSHOT_STALE")
            except (TypeError, ValueError):
                blockers.append("SNAPSHOT_TIME_INVALID")
        if has_other_published_plan:
            blockers.append("ATOMIC_PLAN_REPLACEMENT_REQUIRED")
        if "MISSING_FROZEN_ROUTE" in trigger_reasons:
            blockers.append("FROZEN_ROUTE_INCOMPLETE")
        if "MATERIAL_NOT_READY" in trigger_reasons:
            blockers.append("MATERIAL_NOT_READY")
        if "QUALITY_HOLD" in trigger_reasons:
            blockers.append("QUALITY_HOLD")

        blockers = list(dict.fromkeys(blockers))
        return {
            "authorized": not blockers,
            "blockers": blockers,
            "evidence": {
                "assignmentCount": len(assignments),
                "affectedOrderCount": len(affected_orders),
                "lateOrderCount": int(metrics.get("lateOrderCount", 0)),
                "shortageCount": int(metrics.get("shortageCount", 0)),
                "useOvertime": bool(plan.get("useOvertime")),
                "inputSource": source.get("type"),
                "sourceRevision": source.get("sourceRevision"),
                "snapshotAgeSeconds": round(snapshot_age, 3) if snapshot_age is not None else None,
                "inputFingerprint": parameters.get("inputFingerprint"),
            },
        }


class ScheduleExecutionPort(Protocol):
    name: str

    def apply(self, plan: dict[str, Any], idempotency_key: str,
              expected_source_revision: str) -> dict[str, Any]: ...

    def verify(self, receipt: dict[str, Any], plan: dict[str, Any]) -> bool: ...

    def rollback(self, receipt: dict[str, Any]) -> bool: ...


class SimulatorScheduleExecution:
    """Explicit simulator adapter for closed-loop tests; never contacts an MES."""

    name = "LOCAL_SIMULATOR"

    def __init__(self) -> None:
        self._applied: dict[str, dict[str, Any]] = {}

    def apply(self, plan: dict[str, Any], idempotency_key: str,
              expected_source_revision: str) -> dict[str, Any]:
        reused = idempotency_key in self._applied
        receipt = self._applied.setdefault(idempotency_key, {
            "executionId": str(uuid4()), "idempotencyKey": idempotency_key,
            "planId": plan["planId"], "sourceRevision": expected_source_revision,
            "target": self.name,
        })
        return {**receipt, "reused": reused}

    def verify(self, receipt: dict[str, Any], plan: dict[str, Any]) -> bool:
        stored = self._applied.get(str(receipt.get("idempotencyKey")))
        return stored is not None and stored["planId"] == plan["planId"]

    def rollback(self, receipt: dict[str, Any]) -> bool:
        return self._applied.pop(str(receipt.get("idempotencyKey")), None) is not None


class SchedulingAutonomyRuntime:
    """Observe, plan, authorize, execute, verify, and escalate one scheduling domain."""

    def __init__(
        self,
        store: MesStore,
        agent: SchedulingAgent,
        policy: L4SchedulingPolicy,
        execution: ScheduleExecutionPort | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._agent = agent
        self._scheduling = SchedulingApplicationService(store)
        self._policy = policy
        self._execution = execution
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._run_lock = Lock()
        self._killed = False
        self._runs: list[dict[str, Any]] = []
        self._stop = Event()
        self._thread: Thread | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "level": "L4_TARGET", "domain": "FINITE_CAPACITY_SCHEDULING",
                "mode": self._policy.mode.value, "killSwitch": self._killed,
                "loopRunning": self._thread is not None and self._thread.is_alive(),
                "executionTarget": self._execution.name if self._execution else "NONE",
                "policy": {**asdict(self._policy), "mode": self._policy.mode.value},
                "lastRun": self._runs[0] if self._runs else None,
                "claim": "L4_RUNTIME_IMPLEMENTED_NOT_PRODUCTION_VALIDATED",
            }

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._runs)

    def kill(self) -> dict[str, Any]:
        with self._lock:
            self._killed = True
        return self.status()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            self._killed = False
        return self.status()

    def run_once(self, command: SchedulingAgentCommand, trigger: str = "MANUAL") -> dict[str, Any]:
        run: dict[str, Any] = {
            "runId": str(uuid4()), "level": "L4_TARGET", "domain": "SCHEDULING",
            "mode": self._policy.mode.value, "trigger": trigger,
            "startedAt": self._clock().isoformat(), "state": "OBSERVING",
            "transitions": ["OBSERVING"], "decision": None,
        }
        if not self._run_lock.acquire(blocking=False):
            return self._finish(run, "ESCALATED", "RUN_ALREADY_ACTIVE")
        try:
            with self._lock:
                killed = self._killed
            if killed:
                return self._finish(run, "STOPPED", "KILL_SWITCH_ACTIVE")
            result = self._agent.analyze(command)
            run["transitions"].extend(["PLANNING", "POLICY_CHECK"])
            run["planId"] = result["plan"]["planId"]
            plan = result["plan"]
            if plan["status"] == "PUBLISHED":
                run["policyDecision"] = {"authorized": True, "blockers": [], "evidence": {}}
                return self._finish(run, "VERIFIED", "NO_CHANGE_ALREADY_PUBLISHED")
            other_published = any(
                item["status"] == "PUBLISHED" and item["planId"] != plan["planId"]
                for item in self._scheduling.list_plans(command.workshop_id, 100)
            )
            policy_decision = self._policy.evaluate(
                plan, result["triggerReasons"], now=self._clock(),
                has_other_published_plan=other_published,
            )
            run["policyDecision"] = policy_decision
            if not policy_decision["authorized"]:
                return self._finish(run, "ESCALATED", "BLOCKED_BY_POLICY")
            if self._policy.mode is AutonomyMode.SHADOW:
                return self._finish(run, "SHADOW_COMPLETE", "WOULD_EXECUTE")
            if self._policy.mode is AutonomyMode.SUPERVISED:
                return self._finish(run, "AWAITING_HUMAN", "REQUIRE_HUMAN_APPROVAL")
            if self._execution is None:
                return self._finish(run, "ESCALATED", "EXECUTION_ADAPTER_UNAVAILABLE")

            current_fingerprint, _ = self._agent.snapshot_fingerprint(command)
            expected_fingerprint = plan["generationParameters"].get("inputFingerprint")
            if current_fingerprint != expected_fingerprint:
                return self._finish(run, "ESCALATED", "INPUT_VERSION_CHANGED")
            run["transitions"].append("EXECUTING")
            source_revision = str(
                plan["generationParameters"].get("inputSource", {}).get("sourceRevision", "")
            )
            idempotency_key = f"l4-schedule:{plan['planId']}:{expected_fingerprint}"
            receipt: dict[str, Any] | None = None
            try:
                receipt = self._execution.apply(plan, idempotency_key, source_revision)
                run["executionReceipt"] = receipt
                run["transitions"].append("VERIFYING")
                if not self._execution.verify(receipt, plan):
                    rolled_back = self._rollback(receipt)
                    run["rollbackVerified"] = rolled_back
                    return self._finish(
                        run, "ROLLED_BACK" if rolled_back else "CRITICAL",
                        "EXECUTION_VERIFICATION_FAILED",
                    )
                approved = self._scheduling.approve(
                    str(plan["planId"]), int(plan["recordVersion"]), POLICY_ACTOR,
                    "Pre-authorized L4 scheduling policy passed with recorded evidence",
                )
                published = self._scheduling.publish(
                    str(plan["planId"]), int(approved["recordVersion"]), EXECUTION_ACTOR
                )
            except Exception as exc:  # noqa: BLE001 - external state may be uncertain
                run["error"] = exc.__class__.__name__
                if receipt is None:
                    return self._finish(run, "CRITICAL", "EXECUTION_APPLY_FAILED")
                run["rollbackVerified"] = self._rollback(receipt)
                return self._finish(
                    run, "ROLLED_BACK" if run["rollbackVerified"] else "CRITICAL",
                    "LOCAL_COMMIT_FAILED",
                )
            run["publishedPlan"] = published
            return self._finish(run, "VERIFIED", "AUTONOMOUSLY_EXECUTED")
        except DomainError as exc:
            run["error"] = exc.__class__.__name__
            return self._finish(run, "ESCALATED", "DOMAIN_GUARD_REJECTED")
        except Exception as exc:  # noqa: BLE001 - runtime returns a safe error class only
            run["error"] = exc.__class__.__name__
            return self._finish(run, "ESCALATED", "RUNTIME_FAILURE")
        finally:
            self._run_lock.release()

    def _finish(self, run: dict[str, Any], state: str, decision: str) -> dict[str, Any]:
        run["state"] = state
        run["decision"] = decision
        run["transitions"].append(state)
        run["finishedAt"] = self._clock().isoformat()
        with self._lock:
            self._runs.insert(0, run)
            del self._runs[50:]
        return run

    def _rollback(self, receipt: dict[str, Any]) -> bool:
        try:
            return bool(self._execution and self._execution.rollback(receipt))
        except Exception:  # noqa: BLE001 - rollback failure is represented as CRITICAL
            return False

    def start_loop(
        self,
        commands: Callable[[], list[SchedulingAgentCommand]],
        poll_seconds: float,
    ) -> None:
        if poll_seconds < 1:
            raise ValueError("autonomy poll interval must be at least one second")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(
                target=self._loop, args=(commands, poll_seconds),
                name="scheduling-autonomy-l4", daemon=True,
            )
            self._thread.start()

    def _loop(self, commands: Callable[[], list[SchedulingAgentCommand]],
              poll_seconds: float) -> None:
        while not self._stop.is_set():
            for command in commands():
                if self._stop.is_set():
                    break
                self.run_once(command, "POLL")
            self._stop.wait(poll_seconds)

    def shutdown(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
