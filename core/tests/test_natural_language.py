import unittest
from typing import Any
from unittest.mock import Mock

from autonomous_mes.application.model_gateway import NaturalLanguageAnswer
from autonomous_mes.application.natural_language import NaturalLanguageQueryService
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


class FakeNaturalLanguageModel:
    def answer(self, question: str, facts: dict[str, Any]) -> NaturalLanguageAnswer:
        return NaturalLanguageAnswer(f"已读取 {len(facts['workOrders'])} 个工单：{question}", "FAKE", "fake-model")


class NaturalLanguageQueryTests(unittest.TestCase):
    def test_read_question_uses_snapshot_model_and_returns_provenance(self) -> None:
        result = NaturalLanguageQueryService(
            InMemoryWorkOrderStore(), FakeNaturalLanguageModel()
        ).ask("当前有哪些暂停工单？")

        self.assertEqual("ALLOW_READ_ONLY", result["policyDecision"])
        self.assertEqual("FAKE", result["source"])
        self.assertEqual("fake-model", result["model"])

    def test_write_instruction_is_denied_before_model_call(self) -> None:
        model = Mock()
        result = NaturalLanguageQueryService(InMemoryWorkOrderStore(), model).ask(
            "请立即恢复这个工单"
        )

        self.assertEqual("DENY_WRITE_INTENT", result["policyDecision"])
        model.answer.assert_not_called()
