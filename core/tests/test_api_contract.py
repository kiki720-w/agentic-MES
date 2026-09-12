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
                "storageBackend": "memory",
            },
            self.client.get("/health/ready").json(),
        )

    def test_dashboard_and_read_models_are_available(self):
        dashboard = self.client.get("/")
        self.assertEqual(200, dashboard.status_code)
        self.assertIn("AGENTIC", dashboard.text)
        self.assertIn("生产控制台", dashboard.text)

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


if __name__ == "__main__":
    unittest.main()
