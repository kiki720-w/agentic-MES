import base64
import binascii
import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from autonomous_mes.application.agent_runtime import FallbackNarrator, IncidentResponseAgent
from autonomous_mes.application.agent_tools import (
    GetProductGenealogyTool,
    GetWorkOrderTool,
    ToolContext,
)
from autonomous_mes.application.connector_security import HmacConnectorAuthenticator
from autonomous_mes.application.equipment import (
    EquipmentApplicationService,
    RecordTelemetryCommand,
    RegisterEquipmentCommand,
)
from autonomous_mes.application.genealogy import (
    GenealogyApplicationService,
    MaterialLotInput,
    ProcessResourceInput,
    RecordExecutionSessionCommand,
    RegisterProductUnitCommand,
)
from autonomous_mes.application.identity import (
    DeveloperIdentityProvider,
    Identity,
    OidcTokenVerifier,
    parse_csv_set,
)
from autonomous_mes.application.master_data import (
    ManufacturingResourceApplicationService,
    RegisterManufacturingResourceCommand,
    UpdateManufacturingResourceCommand,
)
from autonomous_mes.application.natural_language import NaturalLanguageQueryService
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
from autonomous_mes.infrastructure.deepseek_gateway import DeepSeekDiagnosticModel
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


class OperationActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int
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


class ProductGenealogyToolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: str
    agentId: str
    subjectId: str
    purpose: str
    productSerial: str


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
    reason: str


class CreateInspectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workOrderId: str
    operationSequence: int
    sampleSize: int = 1


class InspectionResultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int
    passed: bool
    defectCode: str | None = None
    notes: str | None = None
    gaugeId: str
    measurementRecordedAt: datetime


class RegisterManufacturingResourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourceType: str
    resourceId: str
    revision: str | None = None
    name: str
    status: str
    lifeRemainingPercent: float | None = None
    calibrationDueAt: datetime | None = None
    sourceSystem: str = "MES"
    externalReference: str | None = None
    sourceUpdatedAt: datetime


class UpdateManufacturingResourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourceType: str
    resourceId: str
    revision: str | None = None
    expectedVersion: int = Field(gt=0)
    status: str
    lifeRemainingPercent: float | None = None
    calibrationDueAt: datetime | None = None
    sourceUpdatedAt: datetime


class PreviewResourceCsvBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csvText: str
    sourceSystem: str = "MES"


class ImportResourceCsvBody(PreviewResourceCsvBody):
    expectedPreviewId: str


class ConnectorResourceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourceType: str
    resourceId: str
    revision: str | None = None
    name: str
    status: str
    lifeRemainingPercent: float | None = None
    calibrationDueAt: datetime | None = None
    externalReference: str | None = None
    sourceUpdatedAt: datetime


class ConnectorResourcePush(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sourceSystem: str
    resources: list[ConnectorResourceItem] = Field(min_length=1, max_length=500)


class ReworkApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int
    route: list[str]


class NaturalLanguageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)


class RegisterProductUnitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    productSerial: str = Field(min_length=1, max_length=96)
    workOrderId: str


class MaterialConsumptionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    materialLot: str
    quantity: float = Field(gt=0)
    unit: str


class ProcessResourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourceType: str
    resourceId: str
    revision: str | None = None


class RecordExecutionSessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sessionId: str = Field(min_length=1, max_length=96)
    operationSequence: int = Field(gt=0)
    equipmentId: str
    startedAt: datetime
    endedAt: datetime
    materials: list[MaterialConsumptionBody] = Field(min_length=1)
    resources: list[ProcessResourceBody] = Field(default_factory=list)


settings = Settings()


def build_identity_provider() -> DeveloperIdentityProvider | OidcTokenVerifier:
    auth_mode = settings.auth_mode.upper()
    if auth_mode == "DEV":
        factory_ids = parse_csv_set(settings.dev_factory_ids) or frozenset({settings.factory_id})
        return DeveloperIdentityProvider(
            Identity(
                settings.dev_subject_id,
                settings.dev_display_name,
                parse_csv_set(settings.dev_roles),
                factory_ids,
            )
        )
    if auth_mode == "OIDC":
        if not settings.oidc_issuer or not settings.oidc_audience:
            raise RuntimeError("OIDC auth requires issuer and audience")
        jwks_url = settings.oidc_jwks_url or (
            f"{settings.oidc_issuer.rstrip('/')}/protocol/openid-connect/certs"
        )
        return OidcTokenVerifier(
            settings.oidc_issuer,
            settings.oidc_audience,
            jwks_url,
            settings.oidc_roles_claim,
            settings.oidc_factory_ids_claim,
        )
    raise RuntimeError(f"unsupported auth mode: {settings.auth_mode}")


identity_provider = build_identity_provider()
bearer_scheme = HTTPBearer(auto_error=False)


def current_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Identity:
    token = credentials.credentials if credentials else None
    return identity_provider.authenticate(token)


def authorize_human(identity: Identity, *roles: str) -> None:
    identity.require_factory(settings.factory_id)
    identity.require_any_role(*roles)


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
deepseek_model_gateway = (
    DeepSeekDiagnosticModel(
        settings.deepseek_api_key,
        settings.deepseek_model,
        settings.deepseek_base_url,
        settings.deepseek_timeout_seconds,
    )
    if settings.deepseek_api_key
    else None
)
deepseek_narrator = FallbackNarrator(deepseek_model_gateway) if deepseek_model_gateway else None
incident_agent = IncidentResponseAgent(
    store,
    deepseek_narrator,
    settings.agent_l3_execution_enabled,
    {item.strip() for item in settings.agent_l3_approver_ids.split(",") if item.strip()},
)
natural_language_service = NaturalLanguageQueryService(
    store, deepseek_model_gateway, incident_agent
)
quality_service = QualityApplicationService(store, store, store)
genealogy_service = GenealogyApplicationService(store)
master_data_service = ManufacturingResourceApplicationService(store)
connector_credentials = (
    {settings.connector_key_id: settings.connector_hmac_secret}
    if settings.connector_key_id and settings.connector_hmac_secret
    else {}
)
connector_authenticator = HmacConnectorAuthenticator(
    store, connector_credentials, settings.connector_max_clock_skew_seconds
)
policy = ScopedReadPolicy({"demo-planner": {"WS-MACH-01"}})
get_work_order_tool = GetWorkOrderTool(store, policy, store)
get_product_genealogy_tool = GetProductGenealogyTool(store, policy)
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
        "modelGateway": "DEEPSEEK_CONFIGURED" if settings.deepseek_api_key else "DISABLED",
        "agentRuntime": "DEEPSEEK_WITH_RULES_FALLBACK"
        if settings.deepseek_api_key
        else "RULES_ONLY",
        "storageBackend": settings.storage_backend,
        "agentLevel": "L3_EXPERIMENTAL" if settings.agent_l3_execution_enabled else "L2",
        "deploymentMode": settings.deployment_mode,
        "organizationId": settings.organization_id,
        "factoryId": settings.factory_id,
        "authMode": settings.auth_mode.upper(),
    }


@app.get("/api/v1/system/deployment-context")
def deployment_context() -> dict[str, str]:
    return {
        "deploymentMode": settings.deployment_mode,
        "organizationId": settings.organization_id,
        "factoryId": settings.factory_id,
        "dataIsolation": "DEDICATED_DATABASE",
        "cloudControlPlane": "OPTIONAL_NOT_CONNECTED",
    }


@app.get("/api/v1/system/auth-config")
def auth_config() -> dict[str, str | None]:
    return {
        "mode": settings.auth_mode.upper(),
        "issuer": settings.oidc_issuer,
        "clientId": settings.oidc_web_client_id if settings.auth_mode.upper() == "OIDC" else None,
    }


@app.get("/api/v1/identity/me")
def identity_me(identity: Annotated[Identity, Depends(current_identity)]) -> dict[str, object]:
    return identity.as_dict()


@app.get("/api/v1/agent/model-status")
def agent_model_status() -> dict[str, str | None]:
    if deepseek_model_gateway is None:
        return {
            "provider": "NONE",
            "model": None,
            "connectionStatus": "DISABLED",
            "lastCheckedAt": None,
            "lastError": None,
        }
    return deepseek_model_gateway.status()


@app.post("/api/v1/agent/chat")
def agent_chat(
    body: NaturalLanguageBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "OPERATOR", "SUPERVISOR", "QUALITY", "PLANNER")
    return natural_language_service.ask(body.question)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    return HTMLResponse(dashboard_path.read_text(encoding="utf-8"))


@app.post("/api/v1/work-orders", status_code=201)
def create_work_order(
    body: CreateWorkOrderBody,
    identity: Annotated[Identity, Depends(current_identity)],
    idempotency_key: str = Header(...),
) -> dict[str, object]:
    authorize_human(identity, "PLANNER")
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
def list_work_orders(
    limit: int = 30,
    offset: int = 0,
    query: str | None = None,
    status: str | None = None,
    includeTest: bool = False,
) -> dict[str, object]:
    return service.list_page(limit, offset, query, status, includeTest)


@app.get("/api/v1/work-orders-summary")
def work_order_summary(includeTest: bool = False) -> dict[str, object]:
    return service.summary(includeTest)


@app.get("/api/v1/system/outbox")
def list_outbox(
    limit: int = 100,
    offset: int = 0,
    cursor: str | None = None,
) -> dict[str, object]:
    if limit < 1 or limit > 100:
        raise ValidationError("limit must be between 1 and 100")
    if offset < 0 or offset > 10_000_000:
        raise ValidationError("offset must be between 0 and 10000000")
    if cursor and offset:
        raise ValidationError("cursor and offset cannot be combined")
    before_occurred_at: datetime | None = None
    before_event_id: str | None = None
    if cursor:
        try:
            padding = "=" * (-len(cursor) % 4)
            values = json.loads(base64.urlsafe_b64decode(cursor + padding))
            before_occurred_at = datetime.fromisoformat(values["occurredAt"])
            before_event_id = str(values["eventId"])
            if before_occurred_at.tzinfo is None or not before_event_id:
                raise ValueError("cursor values are incomplete")
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise ValidationError("cursor is invalid") from exc
    candidates = store.list_recent_outbox(
        limit + 1,
        offset,
        before_occurred_at,
        before_event_id,
    )
    items = candidates[:limit]
    next_cursor: str | None = None
    if len(candidates) > limit:
        cursor_payload = json.dumps(
            {"occurredAt": items[-1]["occurredAt"], "eventId": items[-1]["eventId"]},
            separators=(",", ":"),
        ).encode()
        next_cursor = base64.urlsafe_b64encode(cursor_payload).decode().rstrip("=")
    return {
        "items": items,
        "count": len(items),
        "total": store.count_outbox(),
        "limit": limit,
        "offset": offset,
        "nextCursor": next_cursor,
    }


@app.post("/api/v1/agent/incidents/analyze")
def analyze_incidents(
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "OPERATOR", "SUPERVISOR")
    items = incident_agent.analyze()
    return {"items": items, "count": len(items)}


@app.get("/api/v1/agent/proposals")
def list_agent_proposals(limit: int = 100) -> dict[str, object]:
    items = incident_agent.list(limit)
    return {"items": items, "count": len(items)}


@app.get("/api/v1/quality/inspections")
def list_quality_inspections(
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    status: str | None = None,
) -> dict[str, object]:
    return quality_service.list_page(limit, offset, query, status)


@app.get("/api/v1/quality/inspections-summary")
def quality_inspections_summary() -> dict[str, object]:
    return quality_service.summary()


@app.post("/api/v1/genealogy/product-units", status_code=201)
def register_product_unit(
    body: RegisterProductUnitBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "OPERATOR")
    return genealogy_service.register(
        RegisterProductUnitCommand(
            str(uuid4()), body.productSerial, body.workOrderId, identity.subject_id
        )
    )


@app.get("/api/v1/genealogy/product-units/{product_serial}")
def get_product_genealogy(product_serial: str) -> dict[str, object]:
    return genealogy_service.get(product_serial)


@app.post("/api/v1/genealogy/product-units/{product_serial}/execution-sessions", status_code=201)
def record_execution_session(
    product_serial: str,
    body: RecordExecutionSessionBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "OPERATOR")
    return genealogy_service.record_execution(
        RecordExecutionSessionCommand(
            str(uuid4()),
            body.sessionId,
            product_serial,
            body.operationSequence,
            identity.subject_id,
            body.equipmentId,
            body.startedAt,
            body.endedAt,
            [
                MaterialLotInput(item.materialLot, item.quantity, item.unit)
                for item in body.materials
            ],
            [
                ProcessResourceInput(
                    item.resourceType,
                    item.resourceId,
                    item.revision,
                )
                for item in body.resources
            ],
        )
    )


@app.post("/api/v1/quality/inspections", status_code=201)
def create_quality_inspection(
    body: CreateInspectionBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "QUALITY")
    return quality_service.create(
        CreateInspectionCommand(
            str(uuid4()),
            body.workOrderId,
            body.operationSequence,
            body.sampleSize,
            identity.subject_id,
        )
    )


@app.post("/api/v1/quality/inspections/{inspection_id}/result")
def record_quality_result(
    inspection_id: str,
    body: InspectionResultBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "QUALITY")
    return quality_service.record(
        inspection_id,
        body.passed,
        body.defectCode,
        body.notes,
        body.expectedVersion,
        identity.subject_id,
        str(uuid4()),
        body.gaugeId,
        body.measurementRecordedAt,
    )


@app.post("/api/v1/quality/inspections/{inspection_id}/approve-rework")
def approve_quality_rework(
    inspection_id: str,
    body: ReworkApprovalBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "QUALITY", "SUPERVISOR")
    return quality_service.approve_rework(
        inspection_id, body.route, body.expectedVersion, identity.subject_id, str(uuid4())
    )


@app.post("/api/v1/agent/proposals/{proposal_id}/approve")
def approve_agent_proposal(
    proposal_id: str,
    body: ApproveProposalBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "SUPERVISOR")
    return incident_agent.approve(proposal_id, identity.subject_id, body.reason)


@app.post("/api/v1/equipment", status_code=201)
def register_equipment(
    body: RegisterEquipmentBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
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
def list_equipment(
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    state: str | None = None,
) -> dict[str, object]:
    return equipment_service.list_page(limit, offset, query, state)


@app.get("/api/v1/equipment-summary")
def equipment_summary() -> dict[str, object]:
    return equipment_service.summary()


@app.post("/api/v1/master-data/manufacturing-resources", status_code=201)
def register_manufacturing_resource(
    body: RegisterManufacturingResourceBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    return master_data_service.register(
        RegisterManufacturingResourceCommand(
            str(uuid4()),
            identity.subject_id,
            body.resourceType,
            body.resourceId,
            body.revision,
            body.name,
            body.status,
            body.lifeRemainingPercent,
            body.calibrationDueAt,
            body.sourceSystem,
            body.externalReference,
            body.sourceUpdatedAt,
        )
    )


@app.get("/api/v1/master-data/manufacturing-resources")
def list_manufacturing_resources(
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    resourceType: str | None = None,
    status: str | None = None,
) -> dict[str, object]:
    return master_data_service.list_page(limit, offset, query, resourceType, status)


@app.get("/api/v1/master-data/manufacturing-resources-summary")
def manufacturing_resources_summary() -> dict[str, object]:
    return master_data_service.summary()


@app.put("/api/v1/master-data/manufacturing-resources/state")
def update_manufacturing_resource(
    body: UpdateManufacturingResourceBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    return master_data_service.update(
        UpdateManufacturingResourceCommand(
            str(uuid4()),
            identity.subject_id,
            body.resourceType,
            body.resourceId,
            body.revision,
            body.expectedVersion,
            body.status,
            body.lifeRemainingPercent,
            body.calibrationDueAt,
            body.sourceUpdatedAt,
        )
    )


@app.get("/api/v1/master-data/manufacturing-resources/import-template")
def manufacturing_resource_import_template() -> PlainTextResponse:
    header = (
        "resourceType,resourceId,revision,name,status,lifeRemainingPercent,"
        "calibrationDueAt,externalReference,sourceUpdatedAt\n"
    )
    return PlainTextResponse(header, media_type="text/csv")


@app.post("/api/v1/master-data/manufacturing-resources/import-preview")
def preview_manufacturing_resource_csv(
    body: PreviewResourceCsvBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    return master_data_service.preview_csv(body.csvText, body.sourceSystem)


@app.post("/api/v1/master-data/manufacturing-resources/import")
def import_manufacturing_resource_csv(
    body: ImportResourceCsvBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    return master_data_service.import_csv(
        body.csvText,
        body.sourceSystem,
        body.expectedPreviewId,
        identity.subject_id,
        str(uuid4()),
    )


@app.post("/api/v1/connectors/v1/manufacturing-resources")
async def connector_push_manufacturing_resources(
    request: Request,
    x_connector_key: str = Header(alias="X-Connector-Key"),
    x_connector_timestamp: str = Header(alias="X-Connector-Timestamp"),
    x_connector_nonce: str = Header(alias="X-Connector-Nonce"),
    x_connector_signature: str = Header(alias="X-Connector-Signature"),
) -> dict[str, object]:
    body = await request.body()
    receipt = connector_authenticator.authenticate(
        x_connector_key,
        x_connector_timestamp,
        x_connector_nonce,
        x_connector_signature,
        body,
    )
    try:
        payload = ConnectorResourcePush.model_validate_json(body)
    except ValueError as exc:
        raise ValidationError("connector payload is invalid") from exc
    columns = [
        "resourceType",
        "resourceId",
        "revision",
        "name",
        "status",
        "lifeRemainingPercent",
        "calibrationDueAt",
        "externalReference",
        "sourceUpdatedAt",
    ]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for item in payload.resources:
        values = item.model_dump(mode="json")
        writer.writerow({key: values.get(key) or "" for key in columns})
    csv_text = stream.getvalue()
    preview = master_data_service.preview_csv(csv_text, payload.sourceSystem)
    result = master_data_service.import_csv(
        csv_text,
        payload.sourceSystem,
        str(preview["previewId"]),
        f"connector:{receipt.key_id}",
        receipt.nonce,
    )
    return {
        **result,
        "keyId": receipt.key_id,
        "nonce": receipt.nonce,
        "requestDigest": receipt.request_digest,
    }


@app.post("/api/v1/equipment/{equipment_id}/telemetry")
def record_equipment_telemetry(
    equipment_id: str,
    body: TelemetryBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "OPERATOR")
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
    identity: Annotated[Identity, Depends(current_identity)],
    idempotency_key: str = Header(...),
) -> dict[str, object]:
    authorize_human(identity, "PLANNER")
    return service.release(
        ReleaseWorkOrderCommand(
            idempotency_key=idempotency_key,
            correlation_id=str(uuid4()),
            work_order_id=work_order_id,
            expected_version=body.expectedVersion,
            actor_id=identity.subject_id,
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
    identity: Annotated[Identity, Depends(current_identity)],
    idempotency_key: str = Header(...),
) -> dict[str, object]:
    authorize_human(identity, "SUPERVISOR" if action == "resume" else "OPERATOR")
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
            actor_id=identity.subject_id,
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


@app.post("/api/v1/agent-tools/get-product-genealogy")
def agent_get_product_genealogy(body: ProductGenealogyToolBody) -> dict[str, object]:
    return get_product_genealogy_tool.execute(
        ToolContext(body.requestId, body.agentId, body.subjectId, body.purpose),
        body.productSerial,
    )
