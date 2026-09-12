import json
import unittest
from unittest.mock import Mock, patch

from autonomous_mes.application.agent_runtime import FallbackNarrator
from autonomous_mes.application.model_gateway import DiagnosticFacts, ModelGatewayError
from autonomous_mes.infrastructure.deepseek_gateway import (
    ConfigurableModelGateway,
    DeepSeekDiagnosticModel,
)


def facts() -> DiagnosticFacts:
    return DiagnosticFacts(
        "WO-1", "SUSPENDED", 10, "数控车削", "LATHE-01", "DOWN", None,
        "主轴过载", False, "2026-09-12T10:00:00+00:00",
    )


class DeepSeekGatewayTests(unittest.TestCase):
    @patch("autonomous_mes.infrastructure.deepseek_gateway.httpx.post")
    def test_returns_only_validated_structured_narrative(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({"diagnosis": "主轴过载导致工序暂停", "recommendation": "由维修人员检查主轴后再由主管审批复工"}, ensure_ascii=False)}}]}
        post.return_value = response

        gateway = DeepSeekDiagnosticModel("secret")
        result = gateway.explain(facts())

        self.assertEqual("DEEPSEEK", result.source)
        self.assertEqual("CONNECTED", gateway.status()["connectionStatus"])
        request = post.call_args.kwargs["json"]
        self.assertNotIn("tools", request)
        self.assertNotIn("response_format", request)
        self.assertNotIn("secret", json.dumps(gateway.status()))

    @patch("autonomous_mes.infrastructure.deepseek_gateway.httpx.post")
    def test_configurable_gateway_switches_provider_without_exposing_key(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": '```json\n{"answer":"连接成功"}\n```'}}]
        }
        post.return_value = response
        gateway = ConfigurableModelGateway()

        status = gateway.configure(
            "KIMI",
            "customer-model",
            "https://api.moonshot.cn/v1",
            15,
            "customer-secret",
            verify_connection=True,
        )

        self.assertEqual("KIMI", status["provider"])
        self.assertEqual("CONNECTED", status["connectionStatus"])
        self.assertTrue(status["apiKeyConfigured"])
        self.assertNotIn("customer-secret", json.dumps(status))
        self.assertEqual(
            "https://api.moonshot.cn/v1/chat/completions",
            post.call_args.args[0],
        )

    def test_gateway_failure_falls_back_to_deterministic_rules(self) -> None:
        primary = Mock()
        primary.explain.side_effect = ModelGatewayError("offline")

        result = FallbackNarrator(primary).explain(facts())

        self.assertEqual("RULES", result.source)
        self.assertIn("DOWN", result.diagnosis)
