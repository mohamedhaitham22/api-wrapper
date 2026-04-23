"""Application configuration and logging utilities."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseSettings, Field

load_dotenv()

_RESERVED_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class Settings(BaseSettings):
    """Settings loaded from environment variables."""

    mockaroo_api_key: str = Field(..., env="MOCKAROO_API_KEY")
    mockaroo_url: str = Field(..., env="MOCKAROO_URL")

    request_timeout_seconds: float = Field(default=30.0, env="REQUEST_TIMEOUT_SECONDS", gt=0)
    max_batch_size: int = Field(default=1000, env="MAX_BATCH_SIZE", ge=1, le=1000)
    max_total_records: int = Field(default=200000, env="MAX_TOTAL_RECORDS", ge=1000)
    initial_snapshot_records: int = Field(default=100000, env="INITIAL_SNAPSHOT_RECORDS", ge=1000)
    max_concurrency: int = Field(default=10, env="MAX_CONCURRENCY", ge=1, le=100)

    retry_max_attempts: int = Field(default=4, env="RETRY_MAX_ATTEMPTS", ge=1, le=10)
    retry_base_delay_seconds: float = Field(
        default=0.5,
        env="RETRY_BASE_DELAY_SECONDS",
        gt=0,
    )

    cache_enabled: bool = Field(default=True, env="CACHE_ENABLED")
    cache_ttl_seconds: int = Field(default=120, env="CACHE_TTL_SECONDS", ge=1)
    cache_max_entries: int = Field(default=128, env="CACHE_MAX_ENTRIES", ge=1)
    data_storage_dir: str = Field(default="data", env="DATA_STORAGE_DIR")

    default_page_limit: int = Field(default=1000, env="DEFAULT_PAGE_LIMIT", ge=1, le=100000)
    max_page_limit: int = Field(default=100000, env="MAX_PAGE_LIMIT", ge=1, le=100000)

    rate_limit_requests: int = Field(default=60, env="RATE_LIMIT_REQUESTS", ge=1)
    rate_limit_window_seconds: int = Field(default=60, env="RATE_LIMIT_WINDOW_SECONDS", ge=1)

    cors_origins: str = Field(default="*", env="CORS_ORIGINS")
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    class Config:
        """Pydantic settings configuration."""

        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins parsed from a comma-separated string."""
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


class JsonFormatter(logging.Formatter):
    """Simple JSON formatter for structured application logs."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON."""
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


def setup_logging(level: str) -> None:
    """Configure root logger to emit structured JSON logs."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger.handlers.clear()
    root_logger.addHandler(handler)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
