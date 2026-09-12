import re
from datetime import UTC, datetime
from typing import Any, Protocol

from autonomous_mes.domain.errors import InvalidTransition, NotFound, ValidationError

from .equipment import EquipmentApplicationService
from .model_gateway import (
    ModelGatewayError,
    NaturalLanguageModel,
)
from .ports import MesStore
from .quality import QualityApplicationService
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


class ActionProposalAgent(Protocol):
    def analyze(self) -> list[dict[str, Any]]: ...

    def recommend_quality_inspection(
        self, work_order_id: str, operation_sequence: int
    ) -> dict[str, Any]: ...


class NaturalLanguageQueryService:
    def __init__(
        self,
        store: MesStore,
        model: NaturalLanguageModel | None,
        action_agent: ActionProposalAgent | None = None,
    ) -> None:
        self._orders = WorkOrderApplicationService(store)
        self._equipment = EquipmentApplicationService(store)
        self._quality = QualityApplicationService(store, store)
        self._model = model
        self._action_agent = action_agent

    def ask(self, question: str) -> dict[str, Any]:
        question = question.strip()
        if not question or len(question) > 500:
            raise ValidationError("question must contain 1 to 500 characters")
        as_of = datetime.now(UTC).isoformat()
        if QUALITY_RECOMMENDATION_INTENT.search(question):
            return self._quality_recommendation(question, as_of)
        if ANALYZE_INCIDENTS_INTENT.search(question):
            return self._analyze_incidents(as_of)
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

        orders = self._orders.list(50)
        equipment = self._equipment.list(50)
        inspections = self._quality.list(50)
        facts = {
            "asOf": as_of,
            "workOrders": [
                {
                    "id": x["workOrderId"],
                    "code": x["humanCode"],
                    "status": x["status"],
                    "quantity": x["quantity"],
                    "dueAt": x["dueAt"],
                    "operations": x["operations"],
                }
                for x in orders
            ],
            "equipment": equipment,
            "qualityInspections": inspections,
        }
        objects = (
            [{"type": "WorkOrder", "id": str(x["workOrderId"])} for x in orders]
            + [{"type": "Equipment", "id": str(x["equipmentId"])} for x in equipment]
            + [
                {"type": "QualityInspection", "id": str(x["inspectionId"])}
                for x in inspections
            ]
        )
        if self._model is None:
            return self._fallback(as_of, objects, orders, equipment, inspections)
        try:
            result = self._model.answer(question, facts)
            return {
                "answer": result.answer,
                "source": result.source,
                "model": result.model,
                "policyDecision": "ALLOW_READ_ONLY",
                "asOf": as_of,
                "sourceObjects": objects,
            }
        except ModelGatewayError:
            return self._fallback(as_of, objects, orders, equipment, inspections)

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
    ) -> dict[str, Any]:
        suspended = sum(x["status"] == "SUSPENDED" for x in orders)
        abnormal = sum(x["state"] in {"DOWN", "ALARM", "OFFLINE"} for x in equipment)
        quarantined = sum(x["status"] == "QUARANTINED" for x in inspections)
        return {
            "answer": f"当前共有 {len(orders)} 个工单，其中 {suspended} 个暂停；{len(equipment)} 台设备中 {abnormal} 台异常；质量隔离 {quarantined} 项。模型不可用，以上为规则汇总。",
            "source": "RULES",
            "model": None,
            "policyDecision": "ALLOW_READ_ONLY",
            "asOf": as_of,
            "sourceObjects": objects,
        }
