from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from autonomous_mes.application.agent_runtime import IncidentResponseAgent
from autonomous_mes.application.agent_tools import GetWorkOrderTool, ToolContext
from autonomous_mes.application.equipment import (
    EquipmentApplicationService,
    RecordTelemetryCommand,
    RegisterEquipmentCommand,
)
from autonomous_mes.application.ports import MesStore
from autonomous_mes.application.quality import CreateInspectionCommand, QualityApplicationService
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationCommand,
    OperationSpec,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.config import Settings
from autonomous_mes.domain.errors import DomainError, Forbidden, NotFound, ValidationError
from autonomous_mes.infrastructure.database import build_engine, build_session_factory
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore, ScopedReadPolicy
from autonomous_mes.infrastructure.sqlalchemy_store import SqlAlchemyWorkOrderStore


class RevisionsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    productRevisionId: str
    routingRevisionId: str
    bomRevisionId: str
    drawingRevisionIds: list[str] = Field(default_factory=list)


class OperationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int
    operationCode: str
    operationName: str
    workCenterId: str


class CreateWorkOrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    humanCode: str
    productionOrderId: str
    workshopId: str
    quantity: int
    dueAt: datetime
    priority: int = 50
    revisions: RevisionsBody
    operations: list[OperationBody] = Field(default_factory=list)


class ReleaseWorkOrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int
    actorId: str


class OperationActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int
    actorId: str
    resourceId: str | None = None
    goodQuantity: int = 0
    scrapQuantity: int = 0


class AgentToolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: str
    agentId: str
    subjectId: str
    purpose: str
    workOrderId: str


class RegisterEquipmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str
    workshopId: str
    workCenterId: str
    protocol: str = "SIMULATED"


class TelemetryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sampleId: str
    observedAt: datetime
    expectedVersion: int
    state: str
    spindleLoadPercent: float | None = None
    temperatureCelsius: float | None = None
    alarmCode: str | None = None
    downtimeReason: str | None = None


class ApproveProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actorId: str
    reason: str


class CreateInspectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workOrderId: str
    operationSequence: int
    sampleSize: int = 1
    actorId: str


class InspectionResultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int
    passed: bool
    defectCode: str | None = None
    notes: str | None = None
    actorId: str


class ReworkApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int
    route: list[str]
    actorId: str


settings = Settings()


def build_store() -> MesStore:
    if settings.storage_backend == "memory":
        return InMemoryWorkOrderStore()
    if settings.storage_backend == "postgresql":
        engine = build_engine(settings.database_url)
        return SqlAlchemyWorkOrderStore(build_session_factory(engine))
    raise RuntimeError(f"unsupported storage backend: {settings.storage_backend}")


store = build_store()
service = WorkOrderApplicationService(store)
equipment_service = EquipmentApplicationService(store)
incident_agent = IncidentResponseAgent(store)
quality_service = QualityApplicationService(store, store)
policy = ScopedReadPolicy({"demo-planner": {"WS-MACH-01"}})
get_work_order_tool = GetWorkOrderTool(store, policy, store)
app = FastAPI(title="Autonomous MES Core", version="0.1.0")
dashboard_path = Path(__file__).parent / "static" / "dashboard.html"


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    status = 404 if isinstance(exc, NotFound) else 403 if isinstance(exc, Forbidden) else 409
    return JSONResponse(
        status_code=status,
        content={
            "type": "about:blank",
            "title": exc.__class__.__name__,
            "status": status,
            "code": exc.__class__.__name__.upper(),
            "detail": str(exc),
        },
    )


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "UP"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    return {
        "status": "READY",
        "modelGateway": "NOT_REQUIRED",
        "agentRuntime": "RULES_ONLY",
        "storageBackend": settings.storage_backend,
    }


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    return HTMLResponse(dashboard_path.read_text(encoding="utf-8"))


@app.post("/api/v1/work-orders", status_code=201)
def create_work_order(
    body: CreateWorkOrderBody, idempotency_key: str = Header(...)
) -> dict[str, object]:
    return service.create(
        CreateWorkOrderCommand(
            idempotency_key=idempotency_key,
            correlation_id=str(uuid4()),
            human_code=body.humanCode,
            production_order_id=body.productionOrderId,
            workshop_id=body.workshopId,
            quantity=body.quantity,
            due_at=body.dueAt,
            priority=body.priority,
            product_revision_id=body.revisions.productRevisionId,
            routing_revision_id=body.revisions.routingRevisionId,
            bom_revision_id=body.revisions.bomRevisionId,
            drawing_revision_ids=body.revisions.drawingRevisionIds,
            operations=[
                OperationSpec(
                    sequence=item.sequence,
                    operation_code=item.operationCode,
                    operation_name=item.operationName,
                    work_center_id=item.workCenterId,
                )
                for item in body.operations
            ],
        )
    )


@app.get("/api/v1/work-orders")
def list_work_orders(limit: int = 100) -> dict[str, object]:
    items = service.list(limit)
    return {"items": items, "count": len(items)}


@app.get("/api/v1/system/outbox")
def list_outbox() -> dict[str, object]:
    items = store.list_outbox()[-100:]
    return {"items": list(reversed(items)), "count": len(items)}


@app.post("/api/v1/agent/incidents/analyze")
def analyze_incidents() -> dict[str, object]:
    items = incident_agent.analyze()
    return {"items": items, "count": len(items)}


@app.get("/api/v1/agent/proposals")
def list_agent_proposals(limit: int = 100) -> dict[str, object]:
    items = incident_agent.list(limit)
    return {"items": items, "count": len(items)}


@app.get("/api/v1/quality/inspections")
def list_quality_inspections(limit: int = 100) -> dict[str, object]:
    items = quality_service.list(limit)
    return {"items": items, "count": len(items)}


@app.post("/api/v1/quality/inspections", status_code=201)
def create_quality_inspection(body: CreateInspectionBody) -> dict[str, object]:
    return quality_service.create(
        CreateInspectionCommand(
            str(uuid4()), body.workOrderId, body.operationSequence, body.sampleSize, body.actorId
        )
    )


@app.post("/api/v1/quality/inspections/{inspection_id}/result")
def record_quality_result(inspection_id: str, body: InspectionResultBody) -> dict[str, object]:
    return quality_service.record(
        inspection_id,
        body.passed,
        body.defectCode,
        body.notes,
        body.expectedVersion,
        body.actorId,
        str(uuid4()),
    )


@app.post("/api/v1/quality/inspections/{inspection_id}/approve-rework")
def approve_quality_rework(inspection_id: str, body: ReworkApprovalBody) -> dict[str, object]:
    return quality_service.approve_rework(
        inspection_id, body.route, body.expectedVersion, body.actorId, str(uuid4())
    )


@app.post("/api/v1/agent/proposals/{proposal_id}/approve")
def approve_agent_proposal(proposal_id: str, body: ApproveProposalBody) -> dict[str, object]:
    return incident_agent.approve(proposal_id, body.actorId, body.reason)


@app.post("/api/v1/equipment", status_code=201)
def register_equipment(body: RegisterEquipmentBody) -> dict[str, object]:
    return equipment_service.register(
        RegisterEquipmentCommand(
            correlation_id=str(uuid4()),
            code=body.code,
            name=body.name,
            workshop_id=body.workshopId,
            work_center_id=body.workCenterId,
            protocol=body.protocol,
        )
    )


@app.get("/api/v1/equipment")
def list_equipment(limit: int = 100) -> dict[str, object]:
    items = equipment_service.list(limit)
    return {"items": items, "count": len(items)}


@app.post("/api/v1/equipment/{equipment_id}/telemetry")
def record_equipment_telemetry(equipment_id: str, body: TelemetryBody) -> dict[str, object]:
    equipment = equipment_service.record(
        RecordTelemetryCommand(
            correlation_id=str(uuid4()),
            equipment_id=equipment_id,
            expected_version=body.expectedVersion,
            sample_id=body.sampleId,
            observed_at=body.observedAt,
            state=body.state,
            spindle_load_percent=body.spindleLoadPercent,
            temperature_celsius=body.temperatureCelsius,
            alarm_code=body.alarmCode,
            downtime_reason=body.downtimeReason,
        )
    )
    affected: list[str] = []
    if body.state in {"DOWN", "ALARM"}:
        affected = service.handle_equipment_incident(
            equipment_id,
            str(equipment["code"]),
            body.state,
            body.downtimeReason or body.alarmCode or "unspecified equipment incident",
            body.sampleId,
        )
    return {**equipment, "affectedWorkOrderIds": affected}


@app.post("/api/v1/work-orders/{work_order_id}/release")
def release_work_order(
    work_order_id: str,
    body: ReleaseWorkOrderBody,
    idempotency_key: str = Header(...),
) -> dict[str, object]:
    return service.release(
        ReleaseWorkOrderCommand(
            idempotency_key=idempotency_key,
            correlation_id=str(uuid4()),
            work_order_id=work_order_id,
            expected_version=body.expectedVersion,
            actor_id=body.actorId,
        )
    )


@app.get("/api/v1/work-orders/{work_order_id}")
def get_work_order(work_order_id: str) -> dict[str, object]:
    return service.get(work_order_id)


@app.post("/api/v1/work-orders/{work_order_id}/operations/{sequence}/{action}")
def execute_operation(
    work_order_id: str,
    sequence: int,
    action: str,
    body: OperationActionBody,
    idempotency_key: str = Header(...),
) -> dict[str, object]:
    current = service.get(work_order_id)
    operation = next((item for item in current["operations"] if item["sequence"] == sequence), None)
    if operation is None:
        raise ValidationError("operation was not found in the frozen route")
    if action == "dispatch":
        if not body.resourceId:
            raise ValidationError("resourceId must identify a registered equipment")
        equipment = equipment_service.get(body.resourceId)
        if equipment["workCenterId"] != operation["workCenterId"]:
            raise ValidationError("equipment does not belong to the operation work center")
        if equipment["state"] not in {"IDLE", "RUNNING"}:
            raise ValidationError("equipment must be online and healthy before dispatch")
    if action == "resume":
        resource_id = operation["assignedResourceId"]
        if not resource_id:
            raise ValidationError("suspended operation has no assigned equipment")
        equipment = equipment_service.get(resource_id)
        if equipment["state"] not in {"IDLE", "RUNNING"}:
            raise ValidationError("equipment must recover before operation resume")
    return service.execute_operation(
        OperationCommand(
            idempotency_key=idempotency_key,
            correlation_id=str(uuid4()),
            work_order_id=work_order_id,
            sequence=sequence,
            expected_version=body.expectedVersion,
            actor_id=body.actorId,
            action=action,
            resource_id=body.resourceId,
            good_quantity=body.goodQuantity,
            scrap_quantity=body.scrapQuantity,
        )
    )


@app.post("/api/v1/agent-tools/get-work-order")
def agent_get_work_order(body: AgentToolBody) -> dict[str, object]:
    return get_work_order_tool.execute(
        ToolContext(
            request_id=body.requestId,
            agent_id=body.agentId,
            subject_id=body.subjectId,
            purpose=body.purpose,
        ),
        body.workOrderId,
    )
