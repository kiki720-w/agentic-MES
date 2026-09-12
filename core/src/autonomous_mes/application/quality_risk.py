from dataclasses import dataclass
from math import ceil
from typing import Any


@dataclass(frozen=True)
class QualityRiskAssessment:
    score: int
    level: str
    recommended_sample_size: int
    factors: tuple[dict[str, Any], ...]
    ruleset_version: str = "QUALITY-RISK-V1"


def assess_quality_risk(
    *,
    planned_quantity: int,
    good_quantity: int,
    scrap_quantity: int,
    historical_inspections: int,
    historical_failures: int,
    recent_alarm_count: int,
    minimum_tool_life_percent: float | None,
) -> QualityRiskAssessment:
    """Deterministic, bounded risk ranking; it never decides inspection outcomes."""
    factors: list[dict[str, Any]] = []
    produced = good_quantity + scrap_quantity
    scrap_rate = scrap_quantity / produced if produced else 0.0
    scrap_points = 45 if scrap_rate > 0.05 else 30 if scrap_rate > 0.02 else 15 if scrap_rate > 0 else 0
    factors.append(_factor("CURRENT_SCRAP_RATE", scrap_points, round(scrap_rate, 4)))

    failure_rate = historical_failures / historical_inspections if historical_inspections else 0.0
    history_points = (
        30
        if failure_rate > 0.15
        else 20
        if failure_rate > 0.05
        else 10
        if historical_failures
        else 0
    )
    factors.append(
        _factor(
            "EQUIPMENT_QUALITY_HISTORY",
            history_points,
            {
                "inspections": historical_inspections,
                "failures": historical_failures,
                "failureRate": round(failure_rate, 4),
            },
        )
    )

    alarm_points = 20 if recent_alarm_count >= 3 else 10 if recent_alarm_count else 0
    factors.append(_factor("RECENT_EQUIPMENT_ALARMS", alarm_points, recent_alarm_count))

    tool_points = 0
    if minimum_tool_life_percent is not None:
        tool_points = 20 if minimum_tool_life_percent < 20 else 10 if minimum_tool_life_percent <= 50 else 0
    factors.append(_factor("MINIMUM_TOOL_LIFE", tool_points, minimum_tool_life_percent))

    score = min(100, sum(int(item["points"]) for item in factors))
    level = "HIGH" if score >= 60 else "MEDIUM" if score >= 30 else "LOW"
    ratio, ceiling = (0.10, 50) if level == "HIGH" else (0.05, 20) if level == "MEDIUM" else (0.02, 10)
    recommended = min(planned_quantity, ceiling, max(1, ceil(planned_quantity * ratio)))
    return QualityRiskAssessment(score, level, recommended, tuple(factors))


def _factor(code: str, points: int, observed: Any) -> dict[str, Any]:
    return {"code": code, "points": points, "observed": observed}
