import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from autonomous_mes.api import app


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
                "modelGateway": "NOT_REQUIRED",
                "agentRuntime": "RULES_ONLY",
                "storageBackend": "memory",
            },
            self.client.get("/health/ready").json(),
        )

    def test_dashboard_and_read_models_are_available(self):
        dashboard = self.client.get("/")
        self.assertEqual(200, dashboard.status_code)
        self.assertIn("AGENTIC", dashboard.text)
        self.assertIn("生产执行中心", dashboard.text)
        self.assertIn("质量检验与返工", dashboard.text)

        orders = self.client.get("/api/v1/work-orders")
        outbox = self.client.get("/api/v1/system/outbox")
        self.assertEqual(200, orders.status_code)
        self.assertIn("items", orders.json())
        self.assertEqual(200, outbox.status_code)
        self.assertIn("items", outbox.json())

    def test_create_is_idempotent_over_http(self):
        body, key, first = self._create()
        second = self.client.post(
            "/api/v1/work-orders", json=body, headers={"Idempotency-Key": key}
        )
        self.assertEqual(201, second.status_code)
        self.assertEqual(first["workOrderId"], second.json()["workOrderId"])

    def test_release_and_agent_scope(self):
        _, _, created = self._create()
        work_order_id = created["workOrderId"]
        released = self.client.post(
            f"/api/v1/work-orders/{work_order_id}/release",
            json={"expectedVersion": 1, "actorId": "planner-1"},
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
            ("release", {"expectedVersion": 1, "actorId": "planner"}),
            (
                "operations/10/dispatch",
                {
                    "expectedVersion": 2,
                    "actorId": "operator",
                    "resourceId": equipment["equipmentId"],
                },
            ),
            ("operations/10/start", {"expectedVersion": 3, "actorId": "operator"}),
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
            json={"expectedVersion": suspended["version"], "actorId": "supervisor"},
            headers={"Idempotency-Key": f"blocked-resume-{suffix}"},
        )
        self.assertEqual(409, blocked.status_code)


if __name__ == "__main__":
    unittest.main()
