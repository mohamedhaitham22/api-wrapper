"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.core.config import get_settings, setup_logging
from src.services.mockaroo_service import MockarooService
from src.utils.rate_limiter import InMemoryRateLimiter

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create and clean up long-lived application resources."""
    app.state.mockaroo_service = MockarooService(settings=settings, logger=logging.getLogger("mockaroo.service"))
    app.state.rate_limiter = InMemoryRateLimiter(
        max_requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )

    logger.info(
        "application_startup",
        extra={
            "rate_limit_requests": settings.rate_limit_requests,
            "rate_limit_window_seconds": settings.rate_limit_window_seconds,
            "max_concurrency": settings.max_concurrency,
            "cache_enabled": settings.cache_enabled,
        },
    )

    try:
        yield
    finally:
        await app.state.mockaroo_service.close()
        logger.info("application_shutdown")


app = FastAPI(
    title="Mockaroo ETL Data Wrapper",
    version="1.0.0",
    description="Generate large ETL testing datasets by aggregating multiple Mockaroo API requests.",
    lifespan=lifespan,
)

allow_credentials = settings.cors_origins_list != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
