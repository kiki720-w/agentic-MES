from copy import deepcopy
from dataclasses import dataclass
from math import ceil
from typing import Any

from autonomous_mes.domain.errors import ValidationError

DEFAULT_QUALITY_RISK_CONFIG: dict[str, Any] = {
    "lookbackDays": 30,
    "bands": {"mediumMin": 30, "highMin": 60},
    "sampling": {
        "LOW": {"ratio": 0.02, "cap": 10},
        "MEDIUM": {"ratio": 0.05, "cap": 20},
        "HIGH": {"ratio": 0.10, "cap": 50},
    },
    "factorRules": {
        "scrapRate": [
            {"operator": "gt", "threshold": 0.05, "points": 45},
            {"operator": "gt", "threshold": 0.02, "points": 30},
            {"operator": "gt", "threshold": 0.0, "points": 15},
        ],
        "failureRate": [
            {"operator": "gt", "threshold": 0.15, "points": 30},
            {"operator": "gt", "threshold": 0.05, "points": 20},
            {"operator": "gt", "threshold": 0.0, "points": 10},
        ],
        "recentAlarms": [
            {"operator": "gte", "threshold": 3, "points": 20},
            {"operator": "gte", "threshold": 1, "points": 10},
        ],
        "minimumToolLife": [
            {"operator": "lt", "threshold": 20, "points": 20},
            {"operator": "lte", "threshold": 50, "points": 10},
        ],
    },
}


@dataclass(frozen=True)
class QualityRiskAssessment:
    score: int
    level: str
    recommended_sample_size: int
    factors: tuple[dict[str, Any], ...]
    ruleset_version: str = "QUALITY-RISK-V1"


def default_quality_risk_configuration() -> dict[str, Any]:
    return deepcopy(DEFAULT_QUALITY_RISK_CONFIG)


def validate_quality_risk_configuration(configuration: dict[str, Any]) -> None:
    try:
        lookback = int(configuration["lookbackDays"])
        bands = configuration["bands"]
        medium, high = int(bands["mediumMin"]), int(bands["highMin"])
        sampling = configuration["sampling"]
        factor_rules = configuration["factorRules"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("quality risk configuration is incomplete") from exc
    if not 1 <= lookback <= 3650 or not 0 <= medium < high <= 100:
        raise ValidationError("quality risk lookback or score bands are invalid")
    for level in ("LOW", "MEDIUM", "HIGH"):
        try:
            ratio, cap = float(sampling[level]["ratio"]), int(sampling[level]["cap"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("quality risk sampling configuration is invalid") from exc
        if not 0 < ratio <= 1 or not 1 <= cap <= 100_000:
            raise ValidationError("quality risk sampling ratio or cap is invalid")
    for name in ("scrapRate", "failureRate", "recentAlarms", "minimumToolLife"):
        rules = factor_rules.get(name)
        if not isinstance(rules, list) or not 1 <= len(rules) <= 10:
            raise ValidationError(f"quality risk factor {name} requires 1-10 rules")
        for rule in rules:
            try:
                operator = str(rule["operator"])
                float(rule["threshold"])
                points = int(rule["points"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(f"quality risk factor {name} has an invalid rule") from exc
            if operator not in {"gt", "gte", "lt", "lte"} or not 0 <= points <= 100:
                raise ValidationError(f"quality risk factor {name} has an invalid rule")


def assess_quality_risk(
    *,
    planned_quantity: int,
    good_quantity: int,
    scrap_quantity: int,
    historical_inspections: int,
    historical_failures: int,
    recent_alarm_count: int,
    minimum_tool_life_percent: float | None,
    configuration: dict[str, Any] | None = None,
    ruleset_version: str = "QUALITY-RISK-V1",
) -> QualityRiskAssessment:
    """Deterministic, bounded risk ranking; it never decides inspection outcomes."""
    config = configuration or DEFAULT_QUALITY_RISK_CONFIG
    validate_quality_risk_configuration(config)
    produced = good_quantity + scrap_quantity
    scrap_rate = scrap_quantity / produced if produced else 0.0
    failure_rate = historical_failures / historical_inspections if historical_inspections else 0.0
    values = {
        "scrapRate": scrap_rate,
        "failureRate": failure_rate,
        "recentAlarms": recent_alarm_count,
        "minimumToolLife": minimum_tool_life_percent,
    }
    codes = {
        "scrapRate": "CURRENT_SCRAP_RATE",
        "failureRate": "EQUIPMENT_QUALITY_HISTORY",
        "recentAlarms": "RECENT_EQUIPMENT_ALARMS",
        "minimumToolLife": "MINIMUM_TOOL_LIFE",
    }
    observed: dict[str, Any] = {
        "scrapRate": round(scrap_rate, 4),
        "failureRate": {
            "inspections": historical_inspections,
            "failures": historical_failures,
            "failureRate": round(failure_rate, 4),
        },
        "recentAlarms": recent_alarm_count,
        "minimumToolLife": minimum_tool_life_percent,
    }
    factors = tuple(
        _factor(
            codes[name],
            _points(values[name], config["factorRules"][name]),
            observed[name],
        )
        for name in codes
    )
    score = min(100, sum(int(item["points"]) for item in factors))
    bands = config["bands"]
    level = (
        "HIGH"
        if score >= int(bands["highMin"])
        else "MEDIUM"
        if score >= int(bands["mediumMin"])
        else "LOW"
    )
    sample = config["sampling"][level]
    recommended = min(
        planned_quantity,
        int(sample["cap"]),
        max(1, ceil(planned_quantity * float(sample["ratio"]))),
    )
    return QualityRiskAssessment(score, level, recommended, factors, ruleset_version)


def _points(value: float | None, rules: list[dict[str, Any]]) -> int:
    if value is None:
        return 0
    matches = [
        int(rule["points"])
        for rule in rules
        if _matches(float(value), str(rule["operator"]), float(rule["threshold"]))
    ]
    return max(matches, default=0)


def _matches(value: float, operator: str, threshold: float) -> bool:
    return {
        "gt": value > threshold,
        "gte": value >= threshold,
        "lt": value < threshold,
        "lte": value <= threshold,
    }[operator]


def _factor(code: str, points: int, observed: Any) -> dict[str, Any]:
    return {"code": code, "points": points, "observed": observed}
