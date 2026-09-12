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
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_timeout_seconds: float = 12.0
