import re
from datetime import UTC, datetime
from typing import Any, Protocol

from autonomous_mes.domain.errors import InvalidTransition, NotFound, ValidationError

from .equipment import EquipmentApplicationService
from .manufacturing_grounding import compact_operational_snapshot
from .model_gateway import (
    ModelGatewayError,
    NaturalLanguageModel,
)
from .ports import MesStore
from .quality import QualityApplicationService
from .scheduling_agent import SchedulingAgentCommand
from .work_orders import WorkOrderApplicationService

WRITE_INTENT = re.compile(
    r"(?:^|帮我|请|立即|直接|执行|现在).{0,12}"
    r"(?:复工|恢复|释放|下达|派工|开工|报工|完工|放行|批准|审批|删除|修改|控制|停机)"
)
RESUME_INTENT = re.compile(r"(?:复工|恢复)", re.IGNORECASE)
WORK_ORDER_CODE = re.compile(r"\bWO-[A-Z0-9-]+\b", re.IGNORECASE)
OPERATION_SEQUENCE = re.compile(r"\bOP\s*[-:]?\s*(\d+)\b", re.IGNORECASE)
ANALYZE_INCIDENTS_INTENT = re.compile(
    r"(?:分析|诊断|检查|排查).{0,12}(?:异常|停机|暂停|故障)|"
    r"(?:异常|停机|暂停|故障).{0,12}(?:分析|诊断|检查|排查)"
)
QUALITY_RECOMMENDATION_INTENT = re.compile(
    r"(?:创建|生成|发起).{0,24}(?:检验|质检).{0,12}(?:建议|申请|提案)|"
    r"(?:检验|质检).{0,12}(?:建议|申请|提案)"
)
SCHEDULE_REPLAN_INTENT = re.compile(
    r"(?:重新排产|重排计划|生成排产|运行排产|优化排产|重新安排生产)"
)
ATTACHMENT_SYNC_INTENT = re.compile(
    r"(?:同步|导入|写入|更新).{0,16}(?:人员|工单|MES)|"
    r"(?:人员|工单|MES).{0,16}(?:同步|导入|写入|更新)",
    re.IGNORECASE,
)
UNUSABLE_ATTACHMENT_ANSWER = re.compile(
    r"^(?:无法确定|无法从(?:附件|当前|提供的)?(?:内容|信息|数据|证据).{0,20}(?:确定|回答))[。！!]?$"
)
MAX_ATTACHMENT_CONTEXT_CHARACTERS = 1_800


def _attachment_excerpt(item: dict[str, Any], character_limit: int) -> str:
    text = str(item.get("text", "")).strip()
    summary = str(item.get("summary", "")).strip()
    if summary and text.startswith(summary):
        text = text[len(summary):].lstrip()
    if not summary:
        return text[:character_limit]
    if len(summary) >= character_limit:
        return summary[:character_limit]
    remaining = character_limit - len(summary)
    return f"{summary}\n\n# 有界内容节选\n{text[:remaining]}".strip()


class ActionProposalAgent(Protocol):
    def analyze(self) -> list[dict[str, Any]]: ...

    def recommend_quality_inspection(
        self, work_order_id: str, operation_sequence: int
    ) -> dict[str, Any]: ...


class ScheduleProposalAgent(Protocol):
    def analyze(self, command: SchedulingAgentCommand) -> dict[str, Any]: ...


class NaturalLanguageQueryService:
    def __init__(
        self,
        store: MesStore,
        model: NaturalLanguageModel | None,
        action_agent: ActionProposalAgent | None = None,
        *,
        scheduling_agent: ScheduleProposalAgent | None = None,
        scheduling_workshop_id: str = "WS-MACH-01",
        scheduling_horizon_days: int = 10,
        scheduling_default_minutes_per_unit: float = 30.0,
        scheduling_use_overtime: bool = False,
    ) -> None:
        self._orders = WorkOrderApplicationService(store)
        self._equipment = EquipmentApplicationService(store)
        self._quality = QualityApplicationService(store, store)
        self._model = model
        self._action_agent = action_agent
        self._scheduling_agent = scheduling_agent
        self._scheduling_workshop_id = scheduling_workshop_id
        self._scheduling_horizon_days = scheduling_horizon_days
        self._scheduling_default_minutes_per_unit = scheduling_default_minutes_per_unit
        self._scheduling_use_overtime = scheduling_use_overtime

    def ask(
        self, question: str, attachments: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        question = question.strip()
        if not question or len(question) > 2000:
            raise ValidationError("question must contain 1 to 2000 characters")
        attachment_facts: list[dict[str, Any]] = []
        attachment_summaries: list[dict[str, str]] = []
        selected_attachments = [
            item for item in (attachments or [])[:8] if str(item.get("text", "")).strip()
        ]
        per_attachment_limit = MAX_ATTACHMENT_CONTEXT_CHARACTERS // max(
            1, len(selected_attachments)
        )
        for item in selected_attachments:
            text = str(item.get("text", "")).strip()
            excerpt = _attachment_excerpt(item, per_attachment_limit)
            attachment_summaries.append({
                "name": str(item.get("name", "attachment"))[:255],
                "deterministicSummary": str(item.get("summary", ""))[:2_500],
            })
            attachment_facts.append({
                "name": str(item.get("name", "attachment"))[:255],
                "kind": str(item.get("kind", "DOCUMENT"))[:40],
                "parser": str(item.get("parser", "LOCAL"))[:80],
                "sha256": str(item.get("sha256", ""))[:64],
                "extractedText": excerpt,
                "truncated": bool(item.get("truncated")) or len(excerpt) < len(text),
                "trust": "UNTRUSTED_USER_ATTACHMENT_DATA",
            })
        as_of = datetime.now(UTC).isoformat()
        if not attachment_facts and SCHEDULE_REPLAN_INTENT.search(question):
            return self._schedule_proposal(as_of)
        if not attachment_facts and QUALITY_RECOMMENDATION_INTENT.search(question):
            return self._quality_recommendation(question, as_of)
        if not attachment_facts and ANALYZE_INCIDENTS_INTENT.search(question):
            return self._analyze_incidents(as_of)
        if attachment_facts and ATTACHMENT_SYNC_INTENT.search(question):
            references = [
                {"type": "Attachment", "id": str(item["sha256"])}
                for item in attachment_facts if item["sha256"]
            ]
            return {
                "answer": (
                    "附件内容已在本机完成理解，但尚未写入人员或工单。当前附件没有形成"
                    "经校验的 MES 唯一主键、字段映射和关联规则；直接同步可能覆盖或重复"
                    "现有生产数据。请先完成字段映射预览，通过校验后再生成可审批的导入操作。"
                ),
                "source": "RULES",
                "model": None,
                "policyDecision": "REQUIRE_FIELD_MAPPING",
                "asOf": as_of,
                "sourceObjects": references,
            }
        if WRITE_INTENT.search(question):
            resume = self._resume_proposal(question, as_of)
            if resume is not None:
                return resume
            return {
                "answer": "该请求涉及生产状态变更，自然语言接口无权执行。请在对应业务页面发起操作，并按现有安全策略完成校验和人工审批。",
                "source": "POLICY",
                "model": None,
                "policyDecision": "DENY_WRITE_INTENT",
                "asOf": as_of,
                "sourceObjects": [],
            }

        orders = self._orders.list(500)
        equipment = self._equipment.list(500)
        inspections = self._quality.list(500)
        facts: dict[str, Any]
        if attachment_facts:
            facts = {
                "asOf": as_of,
                "attachments": attachment_facts,
                "scope": "USER_ATTACHMENTS_ONLY",
            }
        else:
            facts = {"asOf": as_of, **compact_operational_snapshot(
                question, orders, equipment, inspections
            )}
        selected_order_ids = {
            str(item["workOrderId"]) for item in facts.get("workOrders", [])
            if item.get("workOrderId")
        }
        selected_equipment_ids = {
            str(item["equipmentId"]) for item in facts.get("equipment", [])
            if item.get("equipmentId")
        }
        selected_inspection_ids = {
            str(item["inspectionId"]) for item in facts.get("qualityInspections", [])
            if item.get("inspectionId")
        }
        objects = (
            [{"type": "WorkOrder", "id": str(x["workOrderId"])} for x in orders
             if str(x["workOrderId"]) in selected_order_ids]
            + [{"type": "Equipment", "id": str(x["equipmentId"])} for x in equipment
               if str(x["equipmentId"]) in selected_equipment_ids]
            + [
                {"type": "QualityInspection", "id": str(x["inspectionId"])}
                for x in inspections if str(x["inspectionId"]) in selected_inspection_ids
            ]
            + [
                {"type": "Attachment", "id": str(item["sha256"])}
                for item in attachment_facts if item["sha256"]
            ]
        )
        if self._model is None:
            return self._fallback(
                as_of, objects, orders, equipment, inspections, attachment_summaries
            )
        try:
            result = self._model.answer(question, facts)
            if attachment_summaries and UNUSABLE_ATTACHMENT_ANSWER.match(result.answer.strip()):
                return self._fallback(
                    as_of, objects, orders, equipment, inspections, attachment_summaries
                )
            return {
                "answer": result.answer,
                "source": result.source,
                "model": result.model,
                "policyDecision": "ALLOW_READ_ONLY",
                "asOf": as_of,
                "sourceObjects": objects,
            }
        except ModelGatewayError:
            return self._fallback(
                as_of, objects, orders, equipment, inspections, attachment_summaries
            )

    def _schedule_proposal(self, as_of: str) -> dict[str, Any]:
        if self._scheduling_agent is None:
            return self._action_rejected(as_of, "排产智能体不可用。", [])
        result = self._scheduling_agent.analyze(
            SchedulingAgentCommand(
                self._scheduling_workshop_id,
                datetime.now(UTC).date(),
                self._scheduling_horizon_days,
                self._scheduling_use_overtime,
                self._scheduling_default_minutes_per_unit,
                {},
            )
        )
        plan = result["plan"]
        reused = bool(result["reused"])
        if plan["status"] == "PENDING_APPROVAL":
            outcome = "已复用待审批排产方案" if reused else "已生成排产方案并提交人工审批"
        else:
            outcome = "已生成排产草稿，但输入数据或产能仍需补齐"
        return {
            "answer": (
                f"{outcome}，共 {len(plan['assignments'])} 项工序分配、"
                f"{len(plan['shortages'])} 项缺口。L3 智能体不能批准或发布计划。"
            ),
            "source": "POLICY",
            "model": None,
            "policyDecision": result["decision"],
            "asOf": as_of,
            "sourceObjects": [{"type": "SchedulePlan", "id": str(plan["planId"])}],
            "schedulingAgentResult": result,
        }

    def _analyze_incidents(self, as_of: str) -> dict[str, Any]:
        if self._action_agent is None:
            return self._action_rejected(as_of, "异常分析服务不可用。", [])
        proposals = self._action_agent.analyze()
        pending = [item for item in proposals if item["status"] == "PENDING_APPROVAL"]
        observed = [item for item in proposals if item["status"] == "OBSERVED"]
        references = [
            {"type": "AgentProposal", "id": str(item["proposalId"])}
            for item in proposals
        ]
        return {
            "answer": (
                f"异常分析已完成，共检查到 {len(proposals)} 个暂停工单："
                f"{len(pending)} 个形成待审批复工提案，{len(observed)} 个保持停机观察。"
                "分析没有直接改变任何生产状态。"
            ),
            "source": "POLICY",
            "model": next((item["modelName"] for item in proposals if item["modelName"]), None),
            "policyDecision": "EXECUTED_SAFE_ANALYSIS",
            "asOf": as_of,
            "sourceObjects": references,
            "actionProposals": proposals,
        }

    def _quality_recommendation(self, question: str, as_of: str) -> dict[str, Any]:
        code_match = WORK_ORDER_CODE.search(question)
        sequence_match = OPERATION_SEQUENCE.search(question)
        if code_match is None or sequence_match is None:
            return {
                "answer": "请明确工单号和工序，例如“为工单 WO-... 的 OP 10 生成检验建议”。",
                "source": "POLICY",
                "model": None,
                "policyDecision": "REQUIRE_EXPLICIT_TARGET",
                "asOf": as_of,
                "sourceObjects": [],
            }
        code = code_match.group(0).upper()
        sequence = int(sequence_match.group(1))
        order = next(
            (item for item in self._orders.list(500) if str(item["humanCode"]).upper() == code),
            None,
        )
        if order is None:
            return self._action_rejected(as_of, f"未找到工单 {code}。", [])
        references = [{"type": "WorkOrder", "id": str(order["workOrderId"])}]
        if self._action_agent is None:
            return self._action_rejected(as_of, "质量建议服务不可用。", references)
        try:
            proposal = self._action_agent.recommend_quality_inspection(
                str(order["workOrderId"]), sequence
            )
        except (InvalidTransition, NotFound, ValidationError):
            return self._action_rejected(
                as_of,
                f"工单 {code} 的 OP {sequence} 不满足生成检验建议的条件。",
                references,
            )
        references.append({"type": "AgentProposal", "id": str(proposal["proposalId"])})
        return {
            "answer": (
                f"已为工单 {code} 的 OP {sequence} 生成质量检验建议草稿。"
                "草稿不会自动创建检验、隔离产品或批准返工，请由检验员在质量页面处理。"
            ),
            "source": "POLICY",
            "model": proposal["modelName"],
            "policyDecision": "CREATED_RECOMMENDATION_DRAFT",
            "asOf": as_of,
            "sourceObjects": references,
            "actionProposal": proposal,
        }

    def _resume_proposal(self, question: str, as_of: str) -> dict[str, Any] | None:
        if not RESUME_INTENT.search(question):
            return None
        code_match = WORK_ORDER_CODE.search(question)
        if code_match is None:
            return {
                "answer": "我识别到复工意图，但缺少明确工单编号。请使用“恢复工单 WO-...”重新提交；系统只会生成待审批提案。",
                "source": "POLICY",
                "model": None,
                "policyDecision": "REQUIRE_EXPLICIT_TARGET",
                "asOf": as_of,
                "sourceObjects": [],
            }
        code = code_match.group(0).upper()
        order = next(
            (x for x in self._orders.list(500) if str(x["humanCode"]).upper() == code),
            None,
        )
        if order is None:
            return self._action_rejected(as_of, f"未找到工单 {code}。", [])
        references = [{"type": "WorkOrder", "id": str(order["workOrderId"])}]
        if order["status"] != "SUSPENDED":
            return self._action_rejected(
                as_of, f"工单 {code} 当前状态为 {order['status']}，不满足复工条件。", references
            )
        if self._action_agent is None:
            return self._action_rejected(as_of, "动作提案服务不可用。", references)
        proposal = next(
            (
                item
                for item in self._action_agent.analyze()
                if item["workOrderId"] == order["workOrderId"]
            ),
            None,
        )
        if proposal is None or proposal["action"] != "RESUME_OPERATION":
            return self._action_rejected(
                as_of,
                f"工单 {code} 的关联设备尚未恢复健康，不能生成复工提案。",
                references,
            )
        references.append({"type": "Equipment", "id": str(proposal["equipmentId"])})
        return {
            "answer": (
                f"已为工单 {code} 生成复工提案，但尚未执行。"
                "请在下方提案中检查设备状态和影响范围，再由主管审批执行。"
            ),
            "source": "POLICY",
            "model": proposal["modelName"],
            "policyDecision": "REQUIRE_APPROVAL",
            "asOf": as_of,
            "sourceObjects": references,
            "actionProposal": proposal,
        }

    @staticmethod
    def _action_rejected(
        as_of: str, answer: str, objects: list[dict[str, str]]
    ) -> dict[str, Any]:
        return {
            "answer": answer,
            "source": "POLICY",
            "model": None,
            "policyDecision": "REJECTED_BY_POLICY",
            "asOf": as_of,
            "sourceObjects": objects,
        }

    @staticmethod
    def _fallback(
        as_of: str,
        objects: list[dict[str, str]],
        orders: list[dict[str, Any]],
        equipment: list[dict[str, Any]],
        inspections: list[dict[str, Any]],
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        suspended = sum(x["status"] == "SUSPENDED" for x in orders)
        abnormal = sum(x["state"] in {"DOWN", "ALARM", "OFFLINE"} for x in equipment)
        quarantined = sum(x["status"] == "QUARANTINED" for x in inspections)
        attachment_note = ""
        if attachments:
            summaries = []
            for item in attachments:
                summary = str(item.get("deterministicSummary", "")).strip()
                name = str(item.get("name", "附件"))
                summaries.append(f"【{name}】\n{summary or '已提取内容，但没有结构化摘要。'}")
            attachment_note = (
                "本地模型本次未给出可用答案。以下是本机解析器已确认的内容：\n"
                + "\n\n".join(summaries)
            )
        return {
            "answer": attachment_note or f"当前共有 {len(orders)} 个工单，其中 {suspended} 个暂停；{len(equipment)} 台设备中 {abnormal} 台异常；质量隔离 {quarantined} 项。模型不可用，以上为规则汇总。",
            "source": "RULES",
            "model": None,
            "policyDecision": "ALLOW_READ_ONLY",
            "asOf": as_of,
            "sourceObjects": objects,
        }
