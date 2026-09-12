from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DiagnosticFacts:
    work_order_code: str
    work_order_status: str
    operation_sequence: int
    operation_name: str
    equipment_code: str
    equipment_state: str
    alarm_code: str | None
    downtime_reason: str | None
    healthy: bool
    observed_at: str | None


@dataclass(frozen=True)
class DiagnosticNarrative:
    diagnosis: str
    recommendation: str
    source: str
    model: str | None = None


class DiagnosticModel(Protocol):
    def explain(self, facts: DiagnosticFacts) -> DiagnosticNarrative: ...


class ModelGatewayError(RuntimeError):
    """Raised when a remote model cannot return a validated narrative."""
