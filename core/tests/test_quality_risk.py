from autonomous_mes.application.quality_risk import assess_quality_risk


def test_low_risk_assessment_is_bounded_and_explainable() -> None:
    result = assess_quality_risk(
        planned_quantity=200,
        good_quantity=200,
        scrap_quantity=0,
        historical_inspections=10,
        historical_failures=0,
        recent_alarm_count=0,
        minimum_tool_life_percent=80,
    )

    assert result.score == 0
    assert result.level == "LOW"
    assert result.recommended_sample_size == 4
    assert len(result.factors) == 4


def test_high_risk_assessment_increases_sample_without_exceeding_lot() -> None:
    result = assess_quality_risk(
        planned_quantity=12,
        good_quantity=9,
        scrap_quantity=3,
        historical_inspections=10,
        historical_failures=3,
        recent_alarm_count=4,
        minimum_tool_life_percent=10,
    )

    assert result.score == 100
    assert result.level == "HIGH"
    assert result.recommended_sample_size == 2
    assert result.ruleset_version == "QUALITY-RISK-V1"
