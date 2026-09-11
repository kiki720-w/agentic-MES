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
