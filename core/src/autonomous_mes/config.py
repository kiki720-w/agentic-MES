from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTONOMOUS_MES_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://mes:local-development-only@localhost:5432/agentic_mes"
    storage_backend: str = "memory"
    log_level: str = "INFO"
    model_provider: str | None = None
    model_api_key: str | None = None
    model_name: str | None = None
    model_base_url: str | None = None
    model_timeout_seconds: float | None = None
    # Exact base URLs, independently authorized by the deployment administrator.
    model_local_endpoints: str = "http://127.0.0.1:11434/v1"
    model_cloud_endpoints: str = ""
    business_endpoints: str = ""
    # Legacy DeepSeek variables remain supported for existing deployments.
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_timeout_seconds: float = 12.0
    agent_l3_execution_enabled: bool = False
    agent_l3_approver_ids: str = ""
    scheduling_agent_enabled: bool = True
    scheduling_agent_auto_submit: bool = True
    scheduling_agent_workshop_ids: str = "WS-MACH-01"
    scheduling_agent_horizon_days: int = 10
    scheduling_agent_default_minutes_per_unit: float = 30.0
    scheduling_agent_use_overtime: bool = False
    scheduling_autonomy_mode: str = "SHADOW"
    scheduling_autonomy_loop_enabled: bool = False
    scheduling_autonomy_poll_seconds: int = 60
    scheduling_autonomy_execution_target: str = "NONE"
    scheduling_autonomy_max_assignments: int = 50
    scheduling_autonomy_max_affected_orders: int = 20
    scheduling_autonomy_max_late_orders: int = 0
    scheduling_autonomy_max_shortages: int = 0
    scheduling_autonomy_max_snapshot_age_seconds: int = 300
    scheduling_autonomy_allow_overtime: bool = False
    scheduling_autonomy_require_external_snapshot: bool = True
    scheduling_autonomy_require_process_standards: bool = True
    simulator_mode: bool = False
    deployment_mode: str = "FACTORY_EDGE"
    organization_id: str = "ORG-DEMO"
    factory_id: str = "FACTORY-DEMO"
    auth_mode: str = "DEV"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_web_client_id: str = "capaxion-web"
    oidc_roles_claim: str = "realm_access.roles"
    oidc_factory_ids_claim: str = "factory_ids"
    dev_subject_id: str = "demo-supervisor"
    dev_display_name: str = "Demo Supervisor"
    dev_roles: str = "SUPERVISOR,PLANNER,OPERATOR,QUALITY,MASTER_DATA_ADMIN"
    dev_factory_ids: str = "FACTORY-DEMO"
    dev_quality_subject_id: str = "demo-quality-manager"
    dev_quality_display_name: str = "Demo Quality Manager"
    dev_planner_subject_id: str = "demo-planner"
    dev_planner_display_name: str = "Demo Planner"
    connector_key_id: str | None = None
    connector_hmac_secret: str | None = None
    connector_max_clock_skew_seconds: int = 300
