"""Hub server configuration."""

import secrets
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    hub_name: str = "Agent WeChat Hub"
    hub_description: str = "A2A messaging hub for AI agents — like WeChat for agents"
    hub_url: str = "http://localhost:9999"
    hub_version: str = "1.0.0"

    database_url: str = "sqlite+aiosqlite:///./data/hub.db"
    master_api_key: str = ""

    host: str = "0.0.0.0"
    port: int = 9999

    cors_origins: list[str] = ["*"]

    heartbeat_timeout_seconds: int = 120
    message_retention_days: int = 90

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def generate_master_key() -> str:
    return secrets.token_urlsafe(32)
