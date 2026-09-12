import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from autonomous_mes.api import app, settings


class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _create(self):
        suffix = uuid4().hex[:8]
        body = {
            "humanCode": f"WO-API-{suffix}",
            "productionOrderId": f"PO-API-{suffix}",
            "workshopId": "WS-MACH-01",
            "quantity": 10,
            "dueAt": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            "priority": 80,
            "revisions": {
                "productRevisionId": "PR-SHAFT-A",
                "routingRevisionId": "RT-SHAFT-A",
                "bomRevisionId": "BOM-SHAFT-A",
                "drawingRevisionIds": ["DWG-SHAFT-A"],
            },
        }
        key = f"create-{suffix}"
        response = self.client.post(
            "/api/v1/work-orders", json=body, headers={"Idempotency-Key": key}
        )
        self.assertEqual(201, response.status_code, response.text)
        return body, key, response.json()

    def test_health_does_not_require_model_gateway(self):
        self.assertEqual({"status": "UP"}, self.client.get("/health/live").json())
        self.assertEqual(
            {
                "status": "READY",
                "modelGateway": "DISABLED",
                "agentRuntime": "RULES_ONLY",
                "storageBackend": "memory",
                "agentLevel": "L2",
                "deploymentMode": "FACTORY_EDGE",
                "organizationId": "ORG-DEMO",
                "factoryId": "FACTORY-DEMO",
                "authMode": "DEV",
                "schedulingAgentLevel": "L3_BOUNDED",
                "simulatorMode": "true",
            },
            self.client.get("/health/ready").json(),
        )

        identity = self.client.get("/api/v1/identity/me")
        self.assertEqual(200, identity.status_code)
        self.assertEqual("demo-supervisor", identity.json()["subjectId"])
        self.assertIn("SUPERVISOR", identity.json()["roles"])
        self.assertEqual(["FACTORY-DEMO"], identity.json()["factoryIds"])

    def test_control_plane_mode_blocks_legacy_mes_writes(self):
        with patch.object(settings, "simulator_mode", False):
            response = self.client.post(
                "/api/v1/work-orders",
                headers={"Idempotency-Key": "blocked-control-plane-write"},
                json={
                    "humanCode": "WO-BLOCKED",
                    "productionOrderId": "PO-BLOCKED",
                    "workshopId": "WS-MACH-01",
                    "quantity": 1,
                    "dueAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "revisions": {
                        "productRevisionId": "PR-1",
                        "routingRevisionId": "RT-1",
                        "bomRevisionId": "BOM-1",
                    },
                },
            )
            simulator = self.client.get("/simulator")

        self.assertEqual(403, response.status_code)
        self.assertIn("external-system connector", response.json()["detail"])
        self.assertEqual(403, simulator.status_code)

        quality_identity = self.client.get(
            "/api/v1/identity/me", headers={"X-Dev-Actor": "demo-quality-manager"}
        )
        self.assertEqual("demo-quality-manager", quality_identity.json()["subjectId"])
        self.assertEqual(["QUALITY"], quality_identity.json()["roles"])
        planner_identity = self.client.get(
            "/api/v1/identity/me", headers={"X-Dev-Actor": "demo-planner"}
        )
        self.assertEqual("demo-planner", planner_identity.json()["subjectId"])
        self.assertEqual(["PLANNER"], planner_identity.json()["roles"])
        unknown_identity = self.client.get(
            "/api/v1/identity/me", headers={"X-Dev-Actor": "unconfigured-user"}
        )
        self.assertEqual(403, unknown_identity.status_code)

        spoofed_approval = self.client.post(
            "/api/v1/agent/proposals/not-a-proposal/approve",
            json={"actorId": "attacker", "reason": "spoofed"},
        )
        self.assertEqual(422, spoofed_approval.status_code)

    def test_unified_aps_schedule_lifecycle(self):
        suffix = uuid4().hex[:8]
        work_center = f"WC-APS-{suffix}"
        planner_headers = {"X-Dev-Actor": "demo-planner"}
        create_order = self.client.post(
            "/api/v1/work-orders",
            headers={**planner_headers, "Idempotency-Key": f"aps-order-{suffix}"},
            json={
                "humanCode": f"WO-APS-{suffix}",
                "productionOrderId": f"PO-APS-{suffix}",
                "workshopId": "WS-MACH-01",
                "quantity": 10,
                "dueAt": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
                "priority": 90,
                "revisions": {
                    "productRevisionId": f"PR-APS-{suffix}",
                    "routingRevisionId": f"RT-APS-{suffix}",
                    "bomRevisionId": f"BOM-APS-{suffix}",
                    "drawingRevisionIds": [],
                },
                "operations": [
                    {
                        "sequence": 10,
                        "operationCode": "TURN",
                        "operationName": "APS turning",
                        "workCenterId": work_center,
                    }
                ],
            },
        )
        self.assertEqual(201, create_order.status_code, create_order.text)
        resource = self.client.post(
            "/api/v1/planning/resources",
            headers=planner_headers,
            json={
                "code": f"PERSON-{suffix}",
                "name": "APS test operator",
                "resourceType": "PERSON",
                "workshopId": "WS-MACH-01",
                "workCenterId": work_center,
                "dailyCapacityMinutes": 480,
                "overtimeCapacityMinutes": 600,
                "capabilityCodes": ["TURN"],
            },
        )
        self.assertEqual(201, resource.status_code, resource.text)
        generated = self.client.post(
            "/api/v1/planning/plans/generate",
            headers=planner_headers,
            json={
                "workshopId": "WS-MACH-01",
                "horizonStart": datetime.now(UTC).date().isoformat(),
                "horizonDays": 6,
                "defaultMinutesPerUnit": 30,
            },
        )
        self.assertEqual(201, generated.status_code, generated.text)
        plan = generated.json()
        self.assertTrue(
            any(item["workOrderCode"] == f"WO-APS-{suffix}" for item in plan["assignments"])
        )
        assignment = next(
            item for item in plan["assignments"] if item["workOrderCode"] == f"WO-APS-{suffix}"
        )
        moved = self.client.post(
            f"/api/v1/planning/plans/{plan['planId']}/assignments/"
            f"{assignment['assignmentId']}/move",
            headers=planner_headers,
            json={
                "expectedRecordVersion": plan["recordVersion"],
                "targetResourceId": resource.json()["resourceId"],
                "productionDate": assignment["productionDate"],
                "reason": "verified manual scheduling override",
            },
        )
        self.assertEqual(200, moved.status_code, moved.text)
        plan = moved.json()
        submitted = self.client.post(
            f"/api/v1/planning/plans/{plan['planId']}/submit",
            headers=planner_headers,
            json={"expectedRecordVersion": plan["recordVersion"]},
        )
        self.assertEqual(200, submitted.status_code, submitted.text)
        approved = self.client.post(
            f"/api/v1/planning/plans/{plan['planId']}/approve",
            json={
                "expectedRecordVersion": submitted.json()["recordVersion"],
                "reason": "capacity and delivery reviewed",
            },
        )
        self.assertEqual(200, approved.status_code, approved.text)
        published = self.client.post(
            f"/api/v1/planning/plans/{plan['planId']}/publish",
            json={"expectedRecordVersion": approved.json()["recordVersion"]},
        )
        self.assertEqual("PUBLISHED", published.json()["status"])
        planning_page = self.client.get("/planning")
        self.assertEqual(200, planning_page.status_code)
        self.assertIn("有限产能排产", planning_page.text)
        self.assertIn("产能日历", planning_page.text)
        self.assertIn("调整排产条目", planning_page.text)

    def test_dashboard_and_read_models_are_available(self):
        dashboard = self.client.get("/")
        self.assertEqual(200, dashboard.status_code)
        self.assertIn("AGENTIC", dashboard.text)
        self.assertIn("制造智能控制塔", dashboard.text)
        self.assertIn("企业系统连接面", dashboard.text)
        self.assertIn("排产智能体", dashboard.text)
        self.assertIn("L3 BOUNDED", dashboard.text)
        self.assertIn("运行 L3 排产分析", dashboard.text)
        self.assertIn("mes_dev_actor", dashboard.text)

        default_policy = self.client.get("/api/v1/quality/risk-policies/default-configuration")
        self.assertEqual(200, default_policy.status_code)
        self.assertEqual(30, default_policy.json()["lookbackDays"])

        orders = self.client.get("/api/v1/work-orders")
        outbox = self.client.get("/api/v1/system/outbox")
        self.assertEqual(200, orders.status_code)
        self.assertIn("items", orders.json())
        self.assertEqual(200, outbox.status_code)
        self.assertIn("items", outbox.json())
        if outbox.json()["items"]:
            event = outbox.json()["items"][0]
            filtered = self.client.get(
                "/api/v1/system/outbox",
                params={"query": event["eventId"], "publishStatus": event["publishStatus"]},
            ).json()
            self.assertGreaterEqual(filtered["total"], 1)
            self.assertEqual(event["eventId"], filtered["items"][0]["eventId"])

        first_events = self.client.get("/api/v1/system/outbox", params={"limit": 2}).json()
        if first_events["nextCursor"]:
            next_events = self.client.get(
                "/api/v1/system/outbox",
                params={"limit": 2, "cursor": first_events["nextCursor"]},
            ).json()
            first_ids = {item["eventId"] for item in first_events["items"]}
            next_ids = {item["eventId"] for item in next_events["items"]}
            self.assertFalse(first_ids.intersection(next_ids))

        invalid_cursor = self.client.get(
            "/api/v1/system/outbox",
            params={
                "cursor": "eyJvY2N1cnJlZEF0IjoiMjAyNi0wMS0wMVQwMDowMDowMCIsImV2ZW50SWQiOiJ4In0"
            },
        )
        self.assertEqual(409, invalid_cursor.status_code)
        projection = self.client.get("/api/v1/system/operation-projection-health").json()
        self.assertEqual("CONSISTENT", projection["status"])

        missing_quality_proposal = self.client.post(
            f"/api/v1/agent/proposals/{uuid4()}/create-inspection",
            json={"sampleSize": 1, "reason": "contract check"},
        )
        self.assertEqual(404, missing_quality_proposal.status_code)

    def test_quality_risk_policy_lifecycle_contract(self):
        default_configuration = self.client.get(
            "/api/v1/quality/risk-policies/default-configuration"
        ).json()
        suffix = uuid4().hex[:8]
        created = self.client.post(
            "/api/v1/quality/risk-policies",
            json={
                "name": f"Contract policy {suffix}",
                "scope": "PRODUCT_OPERATION",
                "productRevisionId": f"PR-{suffix}",
                "operationCode": "TURN",
                "configuration": default_configuration,
                "changeReason": "Exercise governed lifecycle",
            },
        )
        self.assertEqual(201, created.status_code, created.text)
        draft = created.json()
        simulated = self.client.post(
            f"/api/v1/quality/risk-policies/{draft['policyId']}/simulate",
            json={"expectedRecordVersion": draft["recordVersion"], "limit": 50},
        )
        self.assertEqual(200, simulated.status_code, simulated.text)
        evidence = simulated.json()
        self.assertIsNotNone(evidence["simulationRunId"])
        submitted = self.client.post(
            f"/api/v1/quality/risk-policies/{draft['policyId']}/submit",
            json={"expectedRecordVersion": evidence["recordVersion"]},
        )
        self.assertEqual(200, submitted.status_code, submitted.text)
        pending = submitted.json()
        self_approval = self.client.post(
            f"/api/v1/quality/risk-policies/{draft['policyId']}/approve",
            json={
                "expectedRecordVersion": pending["recordVersion"],
                "reason": "Contract validation complete",
                "effectiveFrom": datetime.now(UTC).isoformat(),
            },
        )
        self.assertEqual(409, self_approval.status_code)
        approved = self.client.post(
            f"/api/v1/quality/risk-policies/{draft['policyId']}/approve",
            headers={"X-Dev-Actor": "demo-quality-manager"},
            json={
                "expectedRecordVersion": pending["recordVersion"],
                "reason": "Independent quality approval",
                "effectiveFrom": datetime.now(UTC).isoformat(),
            },
        )
        self.assertEqual(200, approved.status_code, approved.text)
        rollback = self.client.post(
            f"/api/v1/quality/risk-policies/{draft['policyId']}/rollback-draft",
            json={"reason": "Contract rollback copy"},
        )
        self.assertEqual(201, rollback.status_code, rollback.text)
        self.assertEqual("DRAFT", rollback.json()["status"])
        self.assertEqual(2, rollback.json()["version"])

    def test_operational_read_models_are_paginated_and_filterable(self):
        equipment_code = f"CNC-PAGE-{uuid4().hex[:8]}"
        created = self.client.post(
            "/api/v1/equipment",
            json={
                "code": equipment_code,
                "name": "Pagination Test Lathe",
                "workshopId": "WS-MACH-01",
                "workCenterId": "WC-LATHE-01",
                "protocol": "SIMULATED",
            },
        )
        self.assertEqual(201, created.status_code, created.text)

        equipment = self.client.get(
            "/api/v1/equipment",
            params={"limit": 1, "query": equipment_code, "state": "UNKNOWN"},
        ).json()
        self.assertEqual(1, equipment["count"])
        self.assertEqual(1, equipment["total"])
        self.assertEqual(equipment_code, equipment["items"][0]["code"])
        self.assertIn("stateCounts", self.client.get("/api/v1/equipment-summary").json())

        resources = self.client.get(
            "/api/v1/master-data/manufacturing-resources",
            params={"limit": 1, "resourceType": "TOOL"},
        ).json()
        self.assertEqual(1, resources["limit"])
        self.assertIn("total", resources)
        self.assertIn(
            "typeCounts",
            self.client.get("/api/v1/master-data/manufacturing-resources-summary").json(),
        )

        quality = self.client.get(
            "/api/v1/quality/inspections",
            params={"limit": 1, "status": "OPEN"},
        ).json()
        self.assertEqual(1, quality["limit"])
        self.assertIn("total", quality)
        self.assertIn(
            "statusCounts",
            self.client.get("/api/v1/quality/inspections-summary").json(),
        )
        eligible = self.client.get(
            "/api/v1/quality/eligible-operations",
            params={"limit": 1},
        ).json()
        self.assertEqual(1, eligible["limit"])
        self.assertIn("total", eligible)

    def test_create_is_idempotent_over_http(self):
        body, key, first = self._create()
        second = self.client.post(
            "/api/v1/work-orders", json=body, headers={"Idempotency-Key": key}
        )
        self.assertEqual(201, second.status_code)
        self.assertEqual(first["workOrderId"], second.json()["workOrderId"])

    def test_work_order_list_is_server_paginated_and_filterable(self):
        prefix = f"WO-PAGE-{uuid4().hex[:8]}"
        for index in range(3):
            body = {
                "humanCode": f"{prefix}-{index}",
                "productionOrderId": f"PO-{prefix}-{index}",
                "workshopId": "WS-MACH-01",
                "quantity": 5,
                "dueAt": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
                "revisions": {
                    "productRevisionId": "PR-PAGE",
                    "routingRevisionId": "RT-PAGE",
                    "bomRevisionId": "BOM-PAGE",
                },
            }
            response = self.client.post(
                "/api/v1/work-orders",
                json=body,
                headers={"Idempotency-Key": f"page-{prefix}-{index}"},
            )
            self.assertEqual(201, response.status_code, response.text)

        first = self.client.get(
            "/api/v1/work-orders",
            params={"query": prefix, "status": "DRAFT", "limit": 2, "offset": 0},
        ).json()
        second = self.client.get(
            "/api/v1/work-orders",
            params={"query": prefix, "status": "DRAFT", "limit": 2, "offset": 2},
        ).json()

        self.assertEqual(3, first["total"])
        self.assertEqual(2, first["count"])
        self.assertEqual(1, second["count"])
        self.assertEqual(2, second["offset"])

    def test_product_serial_genealogy_api(self):
        _, _, created = self._create()
        released = self.client.post(
            f"/api/v1/work-orders/{created['workOrderId']}/release",
            json={"expectedVersion": created["version"]},
            headers={"Idempotency-Key": f"release-{uuid4().hex}"},
        ).json()
        serial = f"SN-API-{uuid4().hex[:8]}"
        registered = self.client.post(
            "/api/v1/genealogy/product-units",
            json={
                "productSerial": serial,
                "workOrderId": released["workOrderId"],
            },
        )
        self.assertEqual(201, registered.status_code, registered.text)
        traced = self.client.get(f"/api/v1/genealogy/product-units/{serial}")
        self.assertEqual(200, traced.status_code, traced.text)
        self.assertEqual(serial.upper(), traced.json()["productSerial"])
        self.assertGreaterEqual(len(traced.json()["links"]), 4)

    def test_release_and_agent_scope(self):
        _, _, created = self._create()
        work_order_id = created["workOrderId"]
        released = self.client.post(
            f"/api/v1/work-orders/{work_order_id}/release",
            json={"expectedVersion": 1},
            headers={"Idempotency-Key": f"release-{uuid4().hex}"},
        )
        self.assertEqual(200, released.status_code, released.text)
        self.assertEqual("RELEASED", released.json()["status"])

        allowed = self.client.post(
            "/api/v1/agent-tools/get-work-order",
            json={
                "requestId": f"req-{uuid4().hex}",
                "agentId": "agent-readonly",
                "subjectId": "demo-planner",
                "purpose": "explain current status",
                "workOrderId": work_order_id,
            },
        )
        self.assertEqual(200, allowed.status_code, allowed.text)
        self.assertEqual("ALLOW", allowed.json()["policyDecision"])

        quality_queue = self.client.post(
            "/api/v1/agent-tools/list-quality-candidates",
            json={
                "requestId": f"req-{uuid4().hex}",
                "agentId": "quality-agent-readonly",
                "subjectId": "demo-planner",
                "purpose": "triage pending quality candidates",
                "workshopId": "WS-MACH-01",
                "limit": 10,
            },
        )
        self.assertEqual(200, quality_queue.status_code, quality_queue.text)
        self.assertEqual("R1", quality_queue.json()["risk"])
        self.assertEqual("ALLOW", quality_queue.json()["policyDecision"])

        denied = self.client.post(
            "/api/v1/agent-tools/get-work-order",
            json={
                "requestId": f"req-{uuid4().hex}",
                "agentId": "agent-readonly",
                "subjectId": "outside-user",
                "purpose": "attempt out-of-scope read",
                "workOrderId": work_order_id,
            },
        )
        self.assertEqual(403, denied.status_code)
        self.assertEqual("FORBIDDEN", denied.json()["code"])

    def test_equipment_registration_and_telemetry_ingestion(self):
        suffix = uuid4().hex[:8]
        registered = self.client.post(
            "/api/v1/equipment",
            json={
                "code": f"CNC-API-{suffix}",
                "name": "API数控车床",
                "workshopId": "WS-MACH-01",
                "workCenterId": "WC-LATHE-01",
                "protocol": "SIMULATED",
            },
        )
        self.assertEqual(201, registered.status_code, registered.text)
        equipment = registered.json()
        recorded = self.client.post(
            f"/api/v1/equipment/{equipment['equipmentId']}/telemetry",
            json={
                "sampleId": f"sample-{suffix}",
                "observedAt": datetime.now(UTC).isoformat(),
                "expectedVersion": equipment["version"],
                "state": "RUNNING",
                "spindleLoadPercent": 68.2,
                "temperatureCelsius": 39.5,
            },
        )
        self.assertEqual(200, recorded.status_code, recorded.text)
        self.assertEqual("RUNNING", recorded.json()["state"])
        self.assertGreaterEqual(self.client.get("/api/v1/equipment").json()["count"], 1)

    def test_equipment_alarm_interlocks_bound_operation(self):
        suffix = uuid4().hex[:8]
        equipment = self.client.post(
            "/api/v1/equipment",
            json={
                "code": f"CNC-LOCK-{suffix}",
                "name": "联锁测试机床",
                "workshopId": "WS-MACH-01",
                "workCenterId": "WC-LATHE-01",
                "protocol": "SIMULATED",
            },
        ).json()
        equipment = self.client.post(
            f"/api/v1/equipment/{equipment['equipmentId']}/telemetry",
            json={
                "sampleId": f"healthy-{suffix}",
                "observedAt": datetime.now(UTC).isoformat(),
                "expectedVersion": equipment["version"],
                "state": "IDLE",
            },
        ).json()
        body = {
            "humanCode": f"WO-LOCK-{suffix}",
            "productionOrderId": f"PO-LOCK-{suffix}",
            "workshopId": "WS-MACH-01",
            "quantity": 5,
            "dueAt": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "priority": 90,
            "revisions": {
                "productRevisionId": "PR-A",
                "routingRevisionId": "RT-A",
                "bomRevisionId": "BOM-A",
                "drawingRevisionIds": ["DWG-A"],
            },
            "operations": [
                {
                    "sequence": 10,
                    "operationCode": "TURN",
                    "operationName": "车削",
                    "workCenterId": "WC-LATHE-01",
                }
            ],
        }
        work_order = self.client.post(
            "/api/v1/work-orders",
            json=body,
            headers={"Idempotency-Key": f"lock-create-{suffix}"},
        ).json()
        for action, payload in [
            ("release", {"expectedVersion": 1}),
            (
                "operations/10/dispatch",
                {
                    "expectedVersion": 2,
                    "resourceId": equipment["equipmentId"],
                },
            ),
            ("operations/10/start", {"expectedVersion": 3}),
        ]:
            work_order = self.client.post(
                f"/api/v1/work-orders/{work_order['workOrderId']}/{action}",
                json=payload,
                headers={"Idempotency-Key": f"lock-{action}-{suffix}"},
            ).json()

        alarmed = self.client.post(
            f"/api/v1/equipment/{equipment['equipmentId']}/telemetry",
            json={
                "sampleId": f"alarm-{suffix}",
                "observedAt": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
                "expectedVersion": equipment["version"],
                "state": "ALARM",
                "alarmCode": "SERVO-OVERLOAD",
            },
        )
        self.assertEqual([work_order["workOrderId"]], alarmed.json()["affectedWorkOrderIds"])
        suspended = self.client.get(f"/api/v1/work-orders/{work_order['workOrderId']}").json()
        self.assertEqual("SUSPENDED", suspended["status"])
        blocked = self.client.post(
            f"/api/v1/work-orders/{work_order['workOrderId']}/operations/10/resume",
            json={"expectedVersion": suspended["version"]},
            headers={"Idempotency-Key": f"blocked-resume-{suffix}"},
        )
        self.assertEqual(409, blocked.status_code)


if __name__ == "__main__":
    unittest.main()
