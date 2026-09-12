import base64
import binascii
import csv
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator

from autonomous_mes.application.agent_runtime import FallbackNarrator, IncidentResponseAgent
from autonomous_mes.application.agent_tools import (
    GetProductGenealogyTool,
    GetWorkOrderTool,
    ListQualityCandidatesTool,
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
from autonomous_mes.application.quality import (
    ConfirmQualityRecommendationCommand,
    CreateInspectionCommand,
    QualityApplicationService,
)
from autonomous_mes.application.quality_policy import (
    CreateQualityPolicyCommand,
    QualityPolicyApplicationService,
)
from autonomous_mes.application.quality_risk import default_quality_risk_configuration
from autonomous_mes.application.scheduling import (
    GenerateScheduleCommand,
    IngestSchedulingSnapshotCommand,
    RegisterPlanningResourceCommand,
    SchedulingApplicationService,
    UpdatePlanningResourceCommand,
)
from autonomous_mes.application.scheduling_agent import (
    SchedulingAgent,
    SchedulingAgentCommand,
)
from autonomous_mes.application.spreadsheet_import import (
    MAX_FILE_BYTES,
    build_spreadsheet_template,
    preview_spreadsheet,
    snapshot_fingerprint,
)
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


class QualityCandidatesToolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: str
    agentId: str
    subjectId: str
    purpose: str
    workshopId: str
    limit: int = Field(default=30, ge=1, le=100)


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


class ConfirmQualityRecommendationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sampleSize: int = Field(default=1, ge=1)
    reason: str = Field(min_length=1, max_length=512)


class CreateQualityPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    scope: str
    productRevisionId: str | None = None
    operationCode: str | None = None
    configuration: dict[str, object]
    changeReason: str = Field(min_length=1, max_length=512)


class QualityPolicyTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRecordVersion: int = Field(ge=1)


class SimulateQualityPolicyBody(QualityPolicyTransitionBody):
    limit: int = Field(default=200, ge=1, le=500)


class ApproveQualityPolicyBody(QualityPolicyTransitionBody):
    reason: str = Field(min_length=1, max_length=512)
    effectiveFrom: datetime


class RollbackQualityPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=512)


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


class RegisterPlanningResourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    resourceType: str
    workshopId: str
    workCenterId: str
    dailyCapacityMinutes: float = Field(gt=0)
    overtimeCapacityMinutes: float = Field(gt=0)
    capabilityCodes: list[str] = Field(default_factory=list)


class UpdatePlanningResourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    workCenterId: str = Field(min_length=1, max_length=64)
    dailyCapacityMinutes: float = Field(gt=0)
    overtimeCapacityMinutes: float = Field(gt=0)
    capabilityCodes: list[str] = Field(default_factory=list)
    active: bool = True


class GenerateScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workshopId: str
    horizonStart: date
    horizonDays: int = Field(default=6, ge=1, le=31)
    useOvertime: bool = False
    defaultMinutesPerUnit: float = Field(default=30, gt=0)
    operationRates: dict[str, float] = Field(default_factory=dict)


class AnalyzeScheduleBody(GenerateScheduleBody):
    pass


class SnapshotOperationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(gt=0)
    operationCode: str = Field(min_length=1, max_length=64)
    operationName: str = Field(min_length=1, max_length=160)
    workCenterId: str = Field(min_length=1, max_length=64)
    plannedQuantity: int = Field(gt=0)
    status: Literal["PENDING", "DISPATCHED", "IN_PROGRESS", "SUSPENDED", "COMPLETED"]
    assignedResourceId: str | None = None
    goodQuantity: int = Field(default=0, ge=0)
    scrapQuantity: int = Field(default=0, ge=0)
    minutesPerUnit: float | None = Field(default=None, gt=0)
    setupMinutes: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_reported_quantity(self) -> "SnapshotOperationBody":
        if self.goodQuantity + self.scrapQuantity > self.plannedQuantity:
            raise ValueError("reported quantity cannot exceed planned quantity")
        return self


class SnapshotWorkOrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    externalId: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64)
    productionOrderId: str = Field(min_length=1, max_length=128)
    quantity: int = Field(gt=0)
    dueAt: datetime
    priority: int = Field(default=50, ge=1, le=100)
    productRevisionId: str = Field(min_length=1, max_length=128)
    routingRevisionId: str = Field(min_length=1, max_length=128)
    bomRevisionId: str = Field(min_length=1, max_length=128)
    drawingRevisionIds: list[str] = Field(default_factory=list)
    status: Literal[
        "DRAFT", "RELEASED", "IN_PROGRESS", "SUSPENDED", "COMPLETED", "CLOSED", "CANCELLED"
    ]
    version: int = Field(default=1, ge=1)
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
    materialReady: bool = True
    qualityHold: bool = False
    operations: list[SnapshotOperationBody] = Field(default_factory=list)


class SnapshotResourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    externalId: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    resourceType: Literal["PERSON", "CELL", "EQUIPMENT"]
    workCenterId: str = Field(min_length=1, max_length=64)
    dailyCapacityMinutes: float = Field(gt=0)
    overtimeCapacityMinutes: float = Field(gt=0)
    capabilityCodes: list[str] = Field(default_factory=list)
    active: bool = True
    state: Literal["UNKNOWN", "IDLE", "RUNNING", "DOWN", "ALARM", "OFFLINE"] = "UNKNOWN"

    @model_validator(mode="after")
    def validate_capacity(self) -> "SnapshotResourceBody":
        if self.overtimeCapacityMinutes < self.dailyCapacityMinutes:
            raise ValueError("overtime capacity cannot be lower than daily capacity")
        return self


class SchedulingSnapshotPush(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sourceSystem: str = Field(min_length=1, max_length=64)
    workshopId: str = Field(min_length=1, max_length=64)
    sourceRevision: str = Field(min_length=1, max_length=128)
    observedAt: datetime
    workOrders: list[SnapshotWorkOrderBody]
    resources: list[SnapshotResourceBody]


class ConfirmSpreadsheetImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previewFingerprint: str = Field(min_length=64, max_length=64)
    snapshot: SchedulingSnapshotPush
    runAgent: bool = True
    horizonStart: date
    horizonDays: int = Field(default=6, ge=1, le=31)
    useOvertime: bool = False
    defaultMinutesPerUnit: float = Field(default=30, gt=0)


class ScheduleTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRecordVersion: int = Field(ge=1)


class ScheduleReasonBody(ScheduleTransitionBody):
    reason: str = Field(min_length=1, max_length=512)


class MoveScheduleAssignmentBody(ScheduleReasonBody):
    targetResourceId: str
    productionDate: date


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
    x_dev_actor: str | None = Header(default=None, alias="X-Dev-Actor"),
) -> Identity:
    if settings.auth_mode.upper() == "DEV" and x_dev_actor:
        factory_ids = parse_csv_set(settings.dev_factory_ids) or frozenset({settings.factory_id})
        if x_dev_actor == settings.dev_subject_id:
            return Identity(
                settings.dev_subject_id,
                settings.dev_display_name,
                parse_csv_set(settings.dev_roles),
                factory_ids,
            )
        if x_dev_actor == settings.dev_quality_subject_id:
            return Identity(
                settings.dev_quality_subject_id,
                settings.dev_quality_display_name,
                frozenset({"QUALITY"}),
                factory_ids,
            )
        if x_dev_actor == settings.dev_planner_subject_id:
            return Identity(
                settings.dev_planner_subject_id,
                settings.dev_planner_display_name,
                frozenset({"PLANNER"}),
                factory_ids,
            )
        raise Forbidden("unknown developer identity profile")
    token = credentials.credentials if credentials else None
    return identity_provider.authenticate(token)


def authorize_human(identity: Identity, *roles: str) -> None:
    identity.require_factory(settings.factory_id)
    identity.require_any_role(*roles)


def require_simulator_mode() -> None:
    if not settings.simulator_mode:
        raise Forbidden(
            "built-in MES write path is disabled; use an authorized external-system connector"
        )


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
quality_service = QualityApplicationService(store, store, store)
quality_policy_service = QualityPolicyApplicationService(store)
genealogy_service = GenealogyApplicationService(store)
master_data_service = ManufacturingResourceApplicationService(store)
scheduling_service = SchedulingApplicationService(store)
scheduling_agent = SchedulingAgent(
    store,
    enabled=settings.scheduling_agent_enabled,
    auto_submit=settings.scheduling_agent_auto_submit,
)
natural_language_service = NaturalLanguageQueryService(
    store,
    deepseek_model_gateway,
    incident_agent,
    scheduling_agent=scheduling_agent,
    scheduling_workshop_id=settings.scheduling_agent_workshop_ids.split(",")[0].strip(),
    scheduling_horizon_days=settings.scheduling_agent_horizon_days,
    scheduling_default_minutes_per_unit=settings.scheduling_agent_default_minutes_per_unit,
    scheduling_use_overtime=settings.scheduling_agent_use_overtime,
)
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
list_quality_candidates_tool = ListQualityCandidatesTool(store, policy)
app = FastAPI(title="Autonomous MES Core", version="0.1.0")
control_tower_path = Path(__file__).parent / "static" / "control_tower.html"
simulator_path = Path(__file__).parent / "static" / "dashboard.html"
planning_path = Path(__file__).parent / "static" / "planning.html"
workspace_path = Path(__file__).parent / "static" / "agent_workspace.html"
capacity_path = Path(__file__).parent / "static" / "capacity.html"
planning_results_path = Path(__file__).parent / "static" / "planning_results.html"


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
        "schedulingAgentLevel": "L3_BOUNDED" if settings.scheduling_agent_enabled else "DISABLED",
        "simulatorMode": str(settings.simulator_mode).lower(),
        "deploymentMode": settings.deployment_mode,
        "organizationId": settings.organization_id,
        "factoryId": settings.factory_id,
        "authMode": settings.auth_mode.upper(),
    }


@app.get("/api/v1/system/deployment-context")
def deployment_context() -> dict[str, str | bool]:
    return {
        "deploymentMode": settings.deployment_mode,
        "organizationId": settings.organization_id,
        "factoryId": settings.factory_id,
        "dataIsolation": "DEDICATED_DATABASE",
        "cloudControlPlane": "OPTIONAL_NOT_CONNECTED",
        "simulatorMode": settings.simulator_mode,
        "productRole": "MANUFACTURING_INTELLIGENCE_CONTROL_PLANE",
    }


@app.get("/api/v1/system/auth-config")
def auth_config() -> dict[str, object]:
    return {
        "mode": settings.auth_mode.upper(),
        "issuer": settings.oidc_issuer,
        "clientId": settings.oidc_web_client_id if settings.auth_mode.upper() == "OIDC" else None,
        "devProfiles": [
            {"subjectId": settings.dev_subject_id, "displayName": settings.dev_display_name},
            {
                "subjectId": settings.dev_quality_subject_id,
                "displayName": settings.dev_quality_display_name,
            },
            {
                "subjectId": settings.dev_planner_subject_id,
                "displayName": settings.dev_planner_display_name,
            },
        ]
        if settings.auth_mode.upper() == "DEV"
        else [],
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
    return HTMLResponse(control_tower_path.read_text(encoding="utf-8"))


@app.get("/simulator", response_class=HTMLResponse, include_in_schema=False)
def simulator_console() -> HTMLResponse:
    require_simulator_mode()
    return HTMLResponse(simulator_path.read_text(encoding="utf-8"))


@app.get("/planning", response_class=HTMLResponse, include_in_schema=False)
def planning_console() -> HTMLResponse:
    return HTMLResponse(planning_path.read_text(encoding="utf-8"))


@app.get("/workspace", response_class=HTMLResponse, include_in_schema=False)
def agent_workspace() -> HTMLResponse:
    return HTMLResponse(workspace_path.read_text(encoding="utf-8"))


@app.get("/capacity", response_class=HTMLResponse, include_in_schema=False)
def capacity_console() -> HTMLResponse:
    return HTMLResponse(capacity_path.read_text(encoding="utf-8"))


@app.get("/planning/results", response_class=HTMLResponse, include_in_schema=False)
def planning_results_console() -> HTMLResponse:
    return HTMLResponse(planning_results_path.read_text(encoding="utf-8"))


@app.get("/api/v1/planning/imports/spreadsheet/template")
def download_scheduling_spreadsheet_template(
    identity: Annotated[Identity, Depends(current_identity)],
) -> Response:
    authorize_human(identity, "PLANNER", "SUPERVISOR", "OPERATOR", "QUALITY")
    return Response(
        content=build_spreadsheet_template(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="agentic-aps-template.xlsx"'},
    )


@app.post("/api/v1/planning/resources", status_code=201)
def register_planning_resource(
    body: RegisterPlanningResourceBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "PLANNER")
    return scheduling_service.register_resource(
        RegisterPlanningResourceCommand(
            body.code,
            body.name,
            body.resourceType,
            body.workshopId,
            body.workCenterId,
            body.dailyCapacityMinutes,
            body.overtimeCapacityMinutes,
            body.capabilityCodes,
            identity.subject_id,
            str(uuid4()),
        )
    )


@app.get("/api/v1/planning/resources")
def list_planning_resources(
    workshopId: str,
    identity: Annotated[Identity, Depends(current_identity)],
) -> list[dict[str, object]]:
    authorize_human(identity, "PLANNER", "SUPERVISOR", "OPERATOR", "QUALITY")
    return scheduling_service.list_resources(workshopId)


@app.put("/api/v1/planning/resources/{resource_id}")
def update_planning_resource(
    resource_id: str,
    body: UpdatePlanningResourceBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "PLANNER")
    return scheduling_service.update_resource(
        UpdatePlanningResourceCommand(
            resource_id,
            body.expectedVersion,
            body.name,
            body.workCenterId,
            body.dailyCapacityMinutes,
            body.overtimeCapacityMinutes,
            body.capabilityCodes,
            body.active,
            identity.subject_id,
            str(uuid4()),
        )
    )


@app.get("/api/v1/planning/snapshots/latest")
def latest_scheduling_snapshot(
    workshopId: str,
    identity: Annotated[Identity, Depends(current_identity)],
    includePayload: bool = True,
) -> dict[str, object] | None:
    authorize_human(identity, "PLANNER", "SUPERVISOR", "OPERATOR", "QUALITY")
    return scheduling_service.latest_snapshot(workshopId, include_payload=includePayload)


@app.get("/api/v1/planning/input-summary")
def planning_input_summary(
    workshopId: str,
    identity: Annotated[Identity, Depends(current_identity)],
    limit: int = 100,
) -> dict[str, object]:
    authorize_human(identity, "PLANNER", "SUPERVISOR", "OPERATOR", "QUALITY")
    return scheduling_service.input_summary(workshopId, limit)


@app.post("/api/v1/planning/imports/spreadsheet/preview")
async def preview_scheduling_spreadsheet(
    request: Request,
    workshopId: str,
    identity: Annotated[Identity, Depends(current_identity)],
    x_file_name: str = Header(default="schedule.xlsx", alias="X-File-Name"),
) -> dict[str, object]:
    authorize_human(identity, "PLANNER", "SUPERVISOR")
    content = await request.body()
    if len(content) > MAX_FILE_BYTES:
        raise ValidationError("spreadsheet exceeds 5 MB limit")
    result = preview_spreadsheet(content, unquote(x_file_name), workshopId)
    if result.get("valid") and result.get("snapshot"):
        try:
            canonical = SchedulingSnapshotPush.model_validate(result["snapshot"]).model_dump(
                mode="json"
            )
        except ValueError as exc:
            result["valid"] = False
            result["stats"]["errorCount"] += 1
            result["issues"].append(
                {
                    "severity": "ERROR",
                    "sheet": "业务校验",
                    "row": None,
                    "field": None,
                    "message": str(exc),
                }
            )
        else:
            result["snapshot"] = canonical
            result["previewFingerprint"] = snapshot_fingerprint(canonical)
    return result


@app.post("/api/v1/planning/imports/spreadsheet/confirm", status_code=201)
def confirm_scheduling_spreadsheet(
    body: ConfirmSpreadsheetImportBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "PLANNER")
    values = body.snapshot.model_dump(mode="json")
    if snapshot_fingerprint(values) != body.previewFingerprint:
        raise ValidationError("preview fingerprint changed; preview the file again")
    ingested = scheduling_service.ingest_snapshot(
        IngestSchedulingSnapshotCommand(
            body.snapshot.sourceSystem,
            body.snapshot.workshopId,
            body.snapshot.sourceRevision,
            body.snapshot.observedAt,
            {"workOrders": values["workOrders"], "resources": values["resources"]},
            identity.subject_id,
            str(uuid4()),
        )
    )
    agent_result = None
    if body.runAgent:
        agent_result = scheduling_agent.analyze(
            SchedulingAgentCommand(
                body.snapshot.workshopId,
                body.horizonStart,
                body.horizonDays,
                body.useOvertime,
                body.defaultMinutesPerUnit,
                {},
            )
        )
    return {"snapshot": ingested, "agent": agent_result}


@app.post("/api/v1/planning/plans/generate", status_code=201)
def generate_schedule_plan(
    body: GenerateScheduleBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "PLANNER")
    return scheduling_service.generate(
        GenerateScheduleCommand(
            body.workshopId,
            body.horizonStart,
            body.horizonDays,
            body.useOvertime,
            body.defaultMinutesPerUnit,
            {key.upper(): value for key, value in body.operationRates.items()},
            identity.subject_id,
            str(uuid4()),
        )
    )


@app.post("/api/v1/planning/agent/analyze", status_code=201)
def analyze_schedule_with_agent(
    body: AnalyzeScheduleBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "PLANNER", "SUPERVISOR")
    return scheduling_agent.analyze(
        SchedulingAgentCommand(
            body.workshopId,
            body.horizonStart,
            body.horizonDays,
            body.useOvertime,
            body.defaultMinutesPerUnit,
            {key.upper(): value for key, value in body.operationRates.items()},
        )
    )


@app.get("/api/v1/planning/plans")
def list_schedule_plans(
    workshopId: str,
    identity: Annotated[Identity, Depends(current_identity)],
    limit: int = 30,
) -> list[dict[str, object]]:
    authorize_human(identity, "PLANNER", "SUPERVISOR", "OPERATOR", "QUALITY")
    if not 1 <= limit <= 100:
        raise ValidationError("limit must be between 1 and 100")
    return scheduling_service.list_plans(workshopId, limit)


@app.post("/api/v1/planning/plans/{plan_id}/submit")
def submit_schedule_plan(
    plan_id: str,
    body: ScheduleTransitionBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "PLANNER")
    return scheduling_service.submit(plan_id, body.expectedRecordVersion, identity.subject_id)


@app.post("/api/v1/planning/plans/{plan_id}/assignments/{assignment_id}/move")
def move_schedule_assignment(
    plan_id: str,
    assignment_id: str,
    body: MoveScheduleAssignmentBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "PLANNER")
    return scheduling_service.move_assignment(
        plan_id,
        assignment_id,
        body.expectedRecordVersion,
        body.targetResourceId,
        body.productionDate,
        body.reason,
        identity.subject_id,
    )


@app.post("/api/v1/planning/plans/{plan_id}/approve")
def approve_schedule_plan(
    plan_id: str,
    body: ScheduleReasonBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "SUPERVISOR")
    return scheduling_service.approve(
        plan_id, body.expectedRecordVersion, identity.subject_id, body.reason
    )


@app.post("/api/v1/planning/plans/{plan_id}/publish")
def publish_schedule_plan(
    plan_id: str,
    body: ScheduleTransitionBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "SUPERVISOR")
    return scheduling_service.publish(plan_id, body.expectedRecordVersion, identity.subject_id)


@app.post("/api/v1/planning/plans/{plan_id}/withdraw")
def withdraw_schedule_plan(
    plan_id: str,
    body: ScheduleReasonBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "SUPERVISOR")
    return scheduling_service.withdraw(
        plan_id, body.expectedRecordVersion, identity.subject_id, body.reason
    )


@app.post("/api/v1/work-orders", status_code=201)
def create_work_order(
    body: CreateWorkOrderBody,
    identity: Annotated[Identity, Depends(current_identity)],
    idempotency_key: str = Header(...),
) -> dict[str, object]:
    authorize_human(identity, "PLANNER")
    require_simulator_mode()
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
    query: str | None = None,
    publishStatus: str | None = None,
) -> dict[str, object]:
    if limit < 1 or limit > 100:
        raise ValidationError("limit must be between 1 and 100")
    if offset < 0 or offset > 10_000_000:
        raise ValidationError("offset must be between 0 and 10000000")
    if cursor and offset:
        raise ValidationError("cursor and offset cannot be combined")
    normalized_query = query.strip() if query else None
    if normalized_query and len(normalized_query) > 128:
        raise ValidationError("query must not exceed 128 characters")
    normalized_status = publishStatus.strip().upper() if publishStatus else None
    if normalized_status and normalized_status not in {
        "PENDING",
        "PROCESSING",
        "PUBLISHED",
        "QUARANTINED",
    }:
        raise ValidationError("publishStatus is invalid")
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
        normalized_query,
        normalized_status,
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
        "total": store.count_outbox(normalized_query, normalized_status),
        "limit": limit,
        "offset": offset,
        "nextCursor": next_cursor,
    }


@app.get("/api/v1/system/operation-projection-health")
def operation_projection_health() -> dict[str, object]:
    counts = store.inspect_operation_projection()
    consistent = (
        counts["legacyCount"] == counts["normalizedCount"]
        and counts["mismatchCount"] == 0
        and counts["extraCount"] == 0
    )
    return {"status": "CONSISTENT" if consistent else "DRIFT_DETECTED", **counts}


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


@app.get("/api/v1/quality/eligible-operations")
def list_quality_eligible_operations(
    limit: int = 30,
    offset: int = 0,
    query: str | None = None,
) -> dict[str, object]:
    return quality_service.list_eligible_page(limit, offset, query)


@app.get("/api/v1/quality/risk-policies/default-configuration")
def quality_risk_default_configuration() -> dict[str, object]:
    return default_quality_risk_configuration()


@app.get("/api/v1/quality/risk-policies")
def list_quality_risk_policies(limit: int = 100) -> dict[str, object]:
    return quality_policy_service.list(limit)


@app.post("/api/v1/quality/risk-policies", status_code=201)
def create_quality_risk_policy(
    body: CreateQualityPolicyBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    return quality_policy_service.create_draft(
        CreateQualityPolicyCommand(
            body.name,
            body.scope,
            body.productRevisionId,
            body.operationCode,
            dict(body.configuration),
            body.changeReason,
            identity.subject_id,
        )
    )


@app.post("/api/v1/quality/risk-policies/{policy_id}/submit")
def submit_quality_risk_policy(
    policy_id: str,
    body: QualityPolicyTransitionBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    return quality_policy_service.submit(policy_id, body.expectedRecordVersion, identity.subject_id)


@app.post("/api/v1/quality/risk-policies/{policy_id}/simulate")
def simulate_quality_risk_policy(
    policy_id: str,
    body: SimulateQualityPolicyBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    return quality_policy_service.simulate(
        policy_id, body.expectedRecordVersion, identity.subject_id, body.limit
    )


@app.post("/api/v1/quality/risk-policies/{policy_id}/approve")
def approve_quality_risk_policy(
    policy_id: str,
    body: ApproveQualityPolicyBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "QUALITY")
    return quality_policy_service.approve(
        policy_id,
        body.expectedRecordVersion,
        identity.subject_id,
        body.reason,
        body.effectiveFrom,
    )


@app.post("/api/v1/quality/risk-policies/{policy_id}/rollback-draft", status_code=201)
def rollback_quality_risk_policy(
    policy_id: str,
    body: RollbackQualityPolicyBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    return quality_policy_service.rollback_draft(policy_id, identity.subject_id, body.reason)


@app.post("/api/v1/genealogy/product-units", status_code=201)
def register_product_unit(
    body: RegisterProductUnitBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "OPERATOR")
    require_simulator_mode()
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
    require_simulator_mode()
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
    require_simulator_mode()
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
    require_simulator_mode()
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
    require_simulator_mode()
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
    require_simulator_mode()
    return incident_agent.approve(proposal_id, identity.subject_id, body.reason)


@app.post(
    "/api/v1/agent/proposals/{proposal_id}/create-inspection",
    status_code=201,
)
def create_inspection_from_agent_proposal(
    proposal_id: str,
    body: ConfirmQualityRecommendationBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "QUALITY")
    require_simulator_mode()
    return quality_service.confirm_recommendation(
        ConfirmQualityRecommendationCommand(
            proposal_id,
            body.sampleSize,
            identity.subject_id,
            body.reason,
            str(uuid4()),
        )
    )


@app.post("/api/v1/equipment", status_code=201)
def register_equipment(
    body: RegisterEquipmentBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    require_simulator_mode()
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
    require_simulator_mode()
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
    require_simulator_mode()
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
    require_simulator_mode()
    return master_data_service.preview_csv(body.csvText, body.sourceSystem)


@app.post("/api/v1/master-data/manufacturing-resources/import")
def import_manufacturing_resource_csv(
    body: ImportResourceCsvBody,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, object]:
    authorize_human(identity, "MASTER_DATA_ADMIN")
    require_simulator_mode()
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


@app.post("/api/v1/connectors/v1/scheduling-snapshots", status_code=201)
async def connector_push_scheduling_snapshot(
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
        payload = SchedulingSnapshotPush.model_validate_json(body)
    except ValueError as exc:
        raise ValidationError("scheduling snapshot payload is invalid") from exc
    values = payload.model_dump(mode="json")
    result = scheduling_service.ingest_snapshot(
        IngestSchedulingSnapshotCommand(
            payload.sourceSystem,
            payload.workshopId,
            payload.sourceRevision,
            payload.observedAt,
            {
                "workOrders": values["workOrders"],
                "resources": values["resources"],
            },
            f"connector:{receipt.key_id}",
            receipt.nonce,
        )
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
    require_simulator_mode()
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
    require_simulator_mode()
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
    require_simulator_mode()
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


@app.post("/api/v1/agent-tools/list-quality-candidates")
def agent_list_quality_candidates(body: QualityCandidatesToolBody) -> dict[str, object]:
    return list_quality_candidates_tool.execute(
        ToolContext(body.requestId, body.agentId, body.subjectId, body.purpose),
        body.workshopId,
        body.limit,
    )
