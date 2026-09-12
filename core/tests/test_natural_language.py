import unittest
from datetime import date
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

    def test_resume_requires_explicit_target_before_model_call(self) -> None:
        model = Mock()
        result = NaturalLanguageQueryService(InMemoryWorkOrderStore(), model).ask(
            "请立即恢复这个工单"
        )

        self.assertEqual("REQUIRE_EXPLICIT_TARGET", result["policyDecision"])
        model.answer.assert_not_called()

    def test_resume_command_creates_approval_proposal_not_execution(self) -> None:
        model = Mock()
        action_agent = Mock()
        service = NaturalLanguageQueryService(InMemoryWorkOrderStore(), model, action_agent)
        service._orders = Mock()
        service._orders.list.return_value = [
            {"workOrderId": "order-1", "humanCode": "WO-1008", "status": "SUSPENDED"}
        ]
        action_agent.analyze.return_value = [
            {
                "proposalId": "proposal-1",
                "workOrderId": "order-1",
                "equipmentId": "machine-1",
                "action": "RESUME_OPERATION",
                "modelName": "fake-model",
            }
        ]

        result = service.ask("请恢复工单 WO-1008")

        self.assertEqual("REQUIRE_APPROVAL", result["policyDecision"])
        self.assertEqual("proposal-1", result["actionProposal"]["proposalId"])
        model.answer.assert_not_called()

    def test_incident_analysis_runs_agent_without_executing_proposals(self) -> None:
        model = Mock()
        action_agent = Mock()
        action_agent.analyze.return_value = [
            {
                "proposalId": "proposal-1",
                "status": "PENDING_APPROVAL",
                "modelName": "fake-model",
            },
            {
                "proposalId": "proposal-2",
                "status": "OBSERVED",
                "modelName": "fake-model",
            },
        ]

        result = NaturalLanguageQueryService(
            InMemoryWorkOrderStore(), model, action_agent
        ).ask("请分析当前设备异常并生成建议")

        self.assertEqual("EXECUTED_SAFE_ANALYSIS", result["policyDecision"])
        self.assertEqual(2, len(result["actionProposals"]))
        self.assertIn("1 个形成待审批复工提案", result["answer"])
        action_agent.analyze.assert_called_once_with()
        model.answer.assert_not_called()

    def test_quality_instruction_creates_recommendation_draft_only(self) -> None:
        model = Mock()
        action_agent = Mock()
        service = NaturalLanguageQueryService(InMemoryWorkOrderStore(), model, action_agent)
        service._orders = Mock()
        service._orders.list.return_value = [
            {"workOrderId": "order-1", "humanCode": "WO-1008", "status": "IN_PROGRESS"}
        ]
        action_agent.recommend_quality_inspection.return_value = {
            "proposalId": "quality-draft-1",
            "status": "OBSERVED",
            "modelName": None,
        }

        result = service.ask("为工单 WO-1008 的 OP 10 生成检验建议")

        self.assertEqual("CREATED_RECOMMENDATION_DRAFT", result["policyDecision"])
        self.assertEqual("quality-draft-1", result["actionProposal"]["proposalId"])
        action_agent.recommend_quality_inspection.assert_called_once_with("order-1", 10)
        model.answer.assert_not_called()

    def test_replan_instruction_invokes_bounded_scheduling_agent(self) -> None:
        model = Mock()
        scheduling_agent = Mock()
        scheduling_agent.analyze.return_value = {
            "decision": "SUBMITTED_FOR_APPROVAL",
            "reused": False,
            "publicationAuthority": "HUMAN_SUPERVISOR_ONLY",
            "plan": {
                "planId": "plan-1",
                "status": "PENDING_APPROVAL",
                "assignments": [{"assignmentId": "assignment-1"}],
                "shortages": [],
            },
        }
        service = NaturalLanguageQueryService(
            InMemoryWorkOrderStore(),
            model,
            scheduling_agent=scheduling_agent,
            scheduling_workshop_id="WS-1",
            scheduling_horizon_days=7,
            scheduling_default_minutes_per_unit=20,
        )

        result = service.ask("请重新排产")

        self.assertEqual("SUBMITTED_FOR_APPROVAL", result["policyDecision"])
        self.assertEqual("plan-1", result["sourceObjects"][0]["id"])
        command = scheduling_agent.analyze.call_args.args[0]
        self.assertEqual("WS-1", command.workshop_id)
        self.assertEqual(7, command.horizon_days)
        self.assertEqual(20, command.default_minutes_per_unit)
        self.assertIsInstance(command.horizon_start, date)
        model.answer.assert_not_called()
