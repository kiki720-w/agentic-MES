from autonomous_mes.application.manufacturing_grounding import (
    compact_operational_snapshot,
    deterministic_evidence,
    grounded_request,
)


def results(facts):
    return {item["id"]: item["result"] for item in deterministic_evidence(facts)}


def test_allowlisted_manufacturing_calculations_are_exact_and_unit_aware():
    assert results({
        "setupMinutes": 30, "plannedQuantity": 20, "completedQuantity": 5,
        "minutesPerUnit": 12, "demandMinutes": 540, "normalCapacityMinutes": 480,
        "secondsPerUnit": 90, "quantity": 40,
    }) == {
        "remaining-demand-minutes": 210,
        "normal-capacity-shortage-minutes": 60,
        "total-minutes-from-seconds": 60,
    }


def test_completed_quantity_above_plan_never_creates_negative_demand():
    evidence = results({
        "setupMinutes": 15, "plannedQuantity": 10, "completedQuantity": 12,
        "minutesPerUnit": 8,
    })
    assert evidence["remaining-demand-minutes"] == 15


def test_conflicts_and_permissions_fail_closed():
    evidence = deterministic_evidence({
        "ERP": {"code": "WO-1", "quantity": 20},
        "MES": {"code": "WO-1", "quantity": 25},
        "permissions": ["read", "PROPOSE"],
    })
    by_id = {item["id"]: item for item in evidence}
    assert by_id["unresolved-source-conflicts"]["result"] == "需核对来源"
    assert by_id["unresolved-source-conflicts"]["conflicts"][0]["field"] == "quantity"
    assert by_id["explicit-permissions"]["result"] == {
        "canApprove": False, "canPublish": False,
    }


def test_unrecognized_values_are_data_and_are_never_evaluated():
    malicious = "__import__('os').system('should-never-run')"
    request = grounded_request("test", {
        "setupMinutes": malicious, "plannedQuantity": 1,
        "completedQuantity": 0, "minutesPerUnit": 1,
    })
    assert request["observedSnapshot"]["setupMinutes"] == malicious
    assert request["deterministicEvidence"]["items"] == []


def test_operational_snapshot_aggregates_all_and_projects_relevant_records():
    orders = [
        {"workOrderId": f"o-{index}", "humanCode": f"WO-{index}",
         "status": "SUSPENDED" if index < 14 else "RELEASED", "operations": []}
        for index in range(20)
    ]
    equipment = [
        {"equipmentId": "e-1", "code": "CNC-1", "state": "RUNNING"},
        {"equipmentId": "e-2", "code": "CNC-2", "state": "ALARM"},
    ]
    snapshot = compact_operational_snapshot("有哪些暂停工单？", orders, equipment, [])
    assert snapshot["summary"]["workOrders"]["statusCounts"] == {
        "RELEASED": 6, "SUSPENDED": 14,
    }
    assert len(snapshot["workOrders"]) == 12
    assert all(item["status"] == "SUSPENDED" for item in snapshot["workOrders"])
    assert snapshot["equipment"] == []


def test_explicit_code_retrieval_wins_over_default_ordering():
    orders = [
        {"workOrderId": f"o-{index}", "humanCode": f"WO-{index}",
         "status": "DRAFT", "operations": []}
        for index in range(20)
    ]
    snapshot = compact_operational_snapshot("WO-19 的交期是什么？", orders, [], [])
    assert [item["humanCode"] for item in snapshot["workOrders"]] == ["WO-19"]
