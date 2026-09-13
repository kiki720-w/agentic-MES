import unittest
from datetime import date
from typing import Any
from unittest.mock import Mock

from autonomous_mes.application.model_gateway import ModelGatewayError, NaturalLanguageAnswer
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

    def test_parsed_attachment_is_grounded_as_untrusted_data(self) -> None:
        model = Mock()
        model.answer.return_value = NaturalLanguageAnswer("附件工单数量为 24。", "FAKE", "local")
        scheduling_agent = Mock()
        service = NaturalLanguageQueryService(
            InMemoryWorkOrderStore(), model, scheduling_agent=scheduling_agent
        )

        result = service.ask("请根据附件生成排产摘要", [{
            "name": "orders.csv",
            "kind": "TABLE",
            "parser": "LOCAL_CSV",
            "sha256": "a" * 64,
            "text": "工单号\t数量\nWO-18\t24",
            "truncated": False,
        }])

        self.assertEqual("附件工单数量为 24。", result["answer"])
        facts = model.answer.call_args.args[1]
        self.assertEqual("UNTRUSTED_USER_ATTACHMENT_DATA", facts["attachments"][0]["trust"])
        self.assertIn("WO-18", facts["attachments"][0]["extractedText"])
        self.assertEqual("Attachment", result["sourceObjects"][-1]["type"])
        scheduling_agent.analyze.assert_not_called()

    def test_attachment_context_uses_summary_and_stays_within_local_model_budget(self) -> None:
        model = Mock()
        model.answer.return_value = NaturalLanguageAnswer("识别到人员能力表。", "FAKE", "local")
        service = NaturalLanguageQueryService(InMemoryWorkOrderStore(), model)
        attachments = [
            {
                "name": f"input-{index}.xlsx",
                "kind": "WORKBOOK",
                "parser": "LOCAL_DOCLING_XLSX",
                "sha256": str(index) * 64,
                "summary": f"摘要 {index}：人员、物料、工时",
                "text": "原始行" * 10_000,
            }
            for index in range(1, 4)
        ]

        service.ask("汇总附件", attachments)

        facts = model.answer.call_args.args[1]
        excerpts = [item["extractedText"] for item in facts["attachments"]]
        self.assertTrue(all("摘要" in item for item in excerpts))
        self.assertLessEqual(sum(len(item) for item in excerpts), 1_900)

    def test_model_failure_returns_deterministic_attachment_summary(self) -> None:
        model = Mock()
        model.answer.side_effect = ModelGatewayError("context limit")
        service = NaturalLanguageQueryService(InMemoryWorkOrderStore(), model)

        result = service.ask("有哪些工作表？", [{
            "name": "计划.xlsx",
            "kind": "WORKBOOK",
            "parser": "LOCAL_DOCLING_XLSX",
            "sha256": "a" * 64,
            "summary": "3 个工作表：每日计划、人员能力、工艺编制中",
            "text": "完整提取内容",
        }])

        self.assertIn("每日计划", result["answer"])
        self.assertNotIn("不能解释附件内容", result["answer"])

    def test_unusable_model_answer_returns_deterministic_attachment_summary(self) -> None:
        model = Mock()
        model.answer.return_value = NaturalLanguageAnswer("无法确定", "LOCAL", "small-local")
        service = NaturalLanguageQueryService(InMemoryWorkOrderStore(), model)

        result = service.ask("列出一个人员和工时", [{
            "name": "计划.xlsx",
            "kind": "WORKBOOK",
            "parser": "LOCAL_DOCLING_XLSX",
            "sha256": "c" * 64,
            "summary": "人员=王师傅；8小时工时=160",
            "text": "人员=王师傅；8小时工时=160",
        }])

        self.assertEqual("RULES", result["source"])
        self.assertIn("王师傅", result["answer"])

    def test_attachment_sync_requires_field_mapping_before_write(self) -> None:
        model = Mock()
        service = NaturalLanguageQueryService(InMemoryWorkOrderStore(), model)

        result = service.ask("将附件同步到人员和工单中", [{
            "name": "计划.xlsx",
            "kind": "WORKBOOK",
            "parser": "LOCAL_DOCLING_XLSX",
            "sha256": "b" * 64,
            "summary": "制造计划与人员能力数据",
            "text": "人员=王师傅；物料编码=MAT-1",
        }])

        self.assertEqual("REQUIRE_FIELD_MAPPING", result["policyDecision"])
        self.assertIn("尚未写入", result["answer"])
        model.answer.assert_not_called()
