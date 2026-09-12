import re
from datetime import UTC, datetime
from typing import Any

from autonomous_mes.domain.errors import ValidationError

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


class NaturalLanguageQueryService:
    def __init__(self, store: MesStore, model: NaturalLanguageModel | None) -> None:
        self._orders = WorkOrderApplicationService(store)
        self._equipment = EquipmentApplicationService(store)
        self._quality = QualityApplicationService(store, store)
        self._model = model

    def ask(self, question: str) -> dict[str, Any]:
        question = question.strip()
        if not question or len(question) > 500:
            raise ValidationError("question must contain 1 to 500 characters")
        as_of = datetime.now(UTC).isoformat()
        if WRITE_INTENT.search(question):
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
