from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from autonomous_mes.application.agent_tools import GetWorkOrderTool, ToolContext
from autonomous_mes.application.ports import MesStore
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationCommand,
    OperationSpec,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.config import Settings
from autonomous_mes.domain.errors import DomainError, Forbidden, NotFound
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
