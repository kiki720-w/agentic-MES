from __future__ import annotations

import re
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from autonomous_mes.domain.errors import ValidationError

from .model_gateway import ManufacturingActionModel, ModelGatewayError
from .scheduling import GenerateScheduleCommand, SchedulingApplicationService
from .work_orders import CreateWorkOrderCommand, OperationSpec, WorkOrderApplicationService

ORDER_INTENT = re.compile(
    r"(?:新增|新建|加入|添加|创建|录入).{0,10}(?:订单|工单)|"
    r"(?:订单|工单).{0,10}(?:新增|新建|加入|添加|创建|录入)"
)


class ManufacturingAgentService:
    def __init__(
        self,
        work_orders: WorkOrderApplicationService,
        scheduling: SchedulingApplicationService,
        model: ManufacturingActionModel | None,
    ) -> None:
        self._work_orders = work_orders
        self._scheduling = scheduling
        self._model = model

    def execute(self, instruction: str, actor_id: str) -> dict[str, Any]:
        prepared = self._prepare(instruction)
        if not prepared["valid"]:
            return {
                "status": "NEEDS_INFORMATION",
                "answer": "要创建工单并排产，还需要：" + "、".join(prepared["missingFields"]),
                **prepared,
            }
        order = prepared["order"]
        preferred: dict[str, str] = {}
        if order.get("preferredOperator"):
            matching = [
                item for item in self._scheduling.list_resources(order["workshopId"])
                if item["active"] and item["name"] == order["preferredOperator"]
            ]
            if len(matching) != 1:
                return {
                    "status": "NEEDS_INFORMATION",
                    "answer": f"产能管理中无法唯一找到人员“{order['preferredOperator']}”，请先维护人员或改用准确姓名。",
                    **prepared,
                }
            target = matching[0]
            capabilities = {str(item).upper() for item in target["capabilityCodes"]}
            if capabilities and "*" not in capabilities and order["operationCode"].upper() not in capabilities:
                return {
                    "status": "CONSTRAINT_CONFLICT",
                    "answer": (
                        f"人员“{order['preferredOperator']}”的能力列表不包含"
                        f"“{order['operationCode']}”，工单未写入。请在产能管理中修正能力或指定其他人员。"
                    ),
                    **prepared,
                }
            preferred[order["humanCode"]] = str(target["resourceId"])

        created = self._work_orders.create(CreateWorkOrderCommand(
            idempotency_key=order["idempotencyKey"],
            correlation_id=str(uuid4()),
            human_code=order["humanCode"],
            production_order_id=order["productionOrderId"],
            workshop_id=order["workshopId"],
            quantity=order["quantity"],
            due_at=datetime.fromisoformat(order["dueAt"]),
            priority=order["priority"],
            product_revision_id=order["materialCode"],
            routing_revision_id=f"ROUTE-{order['operationCode']}",
            bom_revision_id=f"BOM-{order['materialCode']}",
            drawing_revision_ids=[],
            operations=[OperationSpec(
                10,
                order["operationCode"],
                order["operationName"],
                order["workCenterId"],
            )],
        ))
        verified = self._work_orders.get(str(created["workOrderId"]))
        if verified["humanCode"] != order["humanCode"]:
            raise ValidationError("work order write-back verification failed")
        schedule = self._scheduling.generate(GenerateScheduleCommand(
            workshop_id=order["workshopId"],
            horizon_start=datetime.now(UTC).date(),
            horizon_days=10,
            use_overtime=False,
            default_minutes_per_unit=order["minutesPerUnit"],
            operation_rates={order["operationCode"]: order["minutesPerUnit"]},
            actor_id=actor_id,
            correlation_id=str(uuid4()),
            trigger_context={"source": "LOCAL_AGENT", "instructionHash": order["instructionHash"]},
            preferred_resource_ids=preferred,
        ))
        assignments = [
            item for item in schedule["assignments"]
            if item["workOrderId"] == created["workOrderId"]
        ]
        shortages = [
            item for item in schedule["shortages"]
            if item["workOrderId"] == created["workOrderId"]
        ]
        return {
            "status": "EXECUTED_AND_VERIFIED",
            "answer": (
                f"已创建工单 {created['humanCode']} 并写入排产结果："
                f"{len(assignments)} 项安排，{len(shortages)} 项能力或产能缺口。"
            ),
            "parser": prepared["parser"],
            "assumptions": prepared["assumptions"],
            "order": created,
            "plan": schedule,
            "orderAssignments": assignments,
            "orderShortages": shortages,
        }

    def _prepare(self, instruction: str) -> dict[str, Any]:
        text = instruction.strip()
        if not text or len(text) > 2_000:
            raise ValidationError("instruction must contain 1 to 2000 characters")
        raw: dict[str, Any]
        parser = "LOCAL_MODEL"
        try:
            if self._model is None:
                raise ModelGatewayError("model gateway is disabled")
            raw = self._model.plan_manufacturing_action(text)
        except ModelGatewayError:
            raw = _deterministic_order_plan(text)
            parser = "LOCAL_RULE_FALLBACK"
        if raw.get("action") != "CREATE_ORDER_AND_SCHEDULE" and ORDER_INTENT.search(text):
            fallback = _deterministic_order_plan(text)
            if fallback.get("action") == "CREATE_ORDER_AND_SCHEDULE":
                raw = fallback
                parser = "LOCAL_RULE_FALLBACK"
        raw_order = raw.get("order")
        values: dict[str, Any] = raw_order if isinstance(raw_order, dict) else {}
        grounded_raw = _deterministic_order_plan(text).get("order", {})
        grounded: dict[str, Any] = grounded_raw if isinstance(grounded_raw, dict) else {}
        digest = sha256(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()
        quantity = _positive_int(grounded.get("quantity"))
        minutes = _positive_float(grounded.get("minutesPerUnit")) or 30.0
        due_at = _due_at(grounded.get("dueAt"))
        model_material = _clean(values.get("materialCode")) or _clean(values.get("productName"))
        material = _clean(grounded.get("materialCode"))
        if not material and model_material and model_material.casefold() in text.casefold():
            material = model_material
        operation_code = _clean(values.get("operationCode"))
        operation_name = _clean(values.get("operationName"))
        work_center = _clean(values.get("workCenterId"))
        if not operation_code and re.search(r"车工|车削|车床", text):
            operation_code = "TURN"
        if operation_code and not operation_name:
            operation_name = operation_code
        if operation_code and not work_center:
            work_center = "WC-LATHE-01" if re.search(r"车工|车削|车床", text) else ""
        missing = []
        if raw.get("action") != "CREATE_ORDER_AND_SCHEDULE":
            missing.append("明确说明要新增工单并排产")
        if not material:
            missing.append("物料编码或工件名称")
        if quantity is None:
            missing.append("数量")
        if due_at is None:
            missing.append("交期（YYYY-MM-DD）")
        if not operation_code:
            missing.append("工序或加工能力")
        if not work_center:
            missing.append("工作中心")
        suffix = digest[:10].upper()
        order = {
            "humanCode": _clean(values.get("humanCode")) or f"WO-LOCAL-{suffix}",
            "productionOrderId": _clean(values.get("productionOrderId")) or f"PO-LOCAL-{suffix}",
            "workshopId": "WS-MACH-01",
            "materialCode": material,
            "quantity": quantity,
            "dueAt": due_at,
            "priority": min(100, max(1, _positive_int(values.get("priority")) or 50)),
            "operationCode": operation_code,
            "operationName": operation_name,
            "workCenterId": work_center,
            "minutesPerUnit": minutes,
            "preferredOperator": (
                _clean(grounded.get("preferredOperator"))
                or (
                    _clean(values.get("preferredOperator"))
                    if _clean(values.get("preferredOperator")) in text
                    else ""
                )
                or None
            ),
            "instructionHash": digest,
            "idempotencyKey": f"agent-order-{digest}",
        }
        assumptions = []
        if not values.get("humanCode"):
            assumptions.append("未提供工单号，已按原始指令生成稳定工单号。")
        if not grounded.get("minutesPerUnit"):
            assumptions.append("未提供单件工时，排产暂按 30 分钟/件，可在人工排产中修改。")
        return {
            "valid": not missing,
            "missingFields": missing,
            "parser": parser,
            "assumptions": assumptions,
            "order": order,
        }


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _due_at(value: Any) -> str | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(raw[:10])
        except ValueError:
            return None
        parsed = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 23, 59, tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _deterministic_order_plan(instruction: str) -> dict[str, Any]:
    if not ORDER_INTENT.search(instruction):
        return {"action": "UNSUPPORTED", "order": {}}
    def match(pattern: str) -> str | None:
        found = list(re.finditer(pattern, instruction, re.IGNORECASE))
        return found[-1].group(1).strip() if found else None

    material = match(
        r"(?:物料(?:编码)?|料号|工件(?:编码)?)\s*(?:改为|是|为)?\s*[：:]?\s*([A-Za-z0-9._/-]+)"
    )
    quantity = match(r"(?:数量|共|生产)\s*(?:改为|是|为)?\s*[：:]?\s*(\d+)") or match(
        r"(?:^|[，,；;\s])\s*(\d+)\s*(?:件|个|套)"
    )
    due_at = match(
        r"(?:交期|交货|完成日期|要求日期)\s*(?:改为|是|为)?\s*[：:]?\s*"
        r"(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2})"
    )
    if due_at:
        due_at = due_at.replace("年", "-").replace("月", "-").replace("日", "")
        due_at = due_at.replace("/", ".").replace(".", "-")
    minutes = match(
        r"(?:单件工时|每件|节拍)\s*(?:改为|是|为)?\s*[：:]?\s*(\d+(?:\.\d+)?)"
    )
    operator = match(r"(?:由|安排|指定)\s*([\u4e00-\u9fff]{2,8}?)\s*(?:来做|做|加工|负责)")
    operation = "TURN" if re.search(r"车工|车削|车床", instruction) else None
    return {
        "action": "CREATE_ORDER_AND_SCHEDULE",
        "order": {
            "materialCode": material,
            "quantity": quantity,
            "dueAt": due_at,
            "operationCode": operation,
            "operationName": "车工" if operation else None,
            "workCenterId": "WC-LATHE-01" if operation else None,
            "minutesPerUnit": minutes,
            "preferredOperator": operator,
        },
    }
