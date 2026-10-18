from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://expense:expense@localhost:5432/expense_analyzer"
    session_secret: str = "development-only-change-me"
    upload_dir: Path = Path("./storage/statements")
    frontend_origin: str = "http://localhost:3000"
    max_upload_bytes: int = 20 * 1024 * 1024

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")


settings = Settings()
