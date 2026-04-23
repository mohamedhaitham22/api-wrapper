"""API routes for data generation and service health."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, root_validator

from src.core.config import Settings, get_settings
from src.services.mockaroo_service import MockarooService, UpstreamServiceError
from src.utils.rate_limiter import InMemoryRateLimiter

router = APIRouter()


class GenerateDataQuery(BaseModel):
    """Validated query parameters for the generate-data endpoint."""

    total: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1)
    use_cache: bool = Field(default=True)

    @root_validator
    def validate_page_and_limit(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Ensure page and limit are provided together when pagination is used."""
        page_provided = values.get("page") is not None
        limit_provided = values.get("limit") is not None
        if page_provided != limit_provided:
            raise ValueError("Query parameters 'page' and 'limit' must be provided together.")
        return values


class GenerateDataMeta(BaseModel):
    """Metadata describing generation execution details."""

    mode: Literal["full", "paginated"]
    page: int | None = None
    limit: int | None = None
    total_requested: int | None = None
    total_returned: int
    requests_made: int
    retries: int
    cache_hit: bool
    execution_time_ms: int


class GenerateDataResponse(BaseModel):
    """Response schema for generated datasets."""

    data: list[dict[str, Any]]
    meta: GenerateDataMeta


class HealthResponse(BaseModel):
    """Health-check response schema."""

    status: Literal["ok"]
    service: str


def get_mockaroo_service(request: Request) -> MockarooService:
    """Resolve the request-scoped Mockaroo service instance."""
    return request.app.state.mockaroo_service


def get_rate_limiter(request: Request) -> InMemoryRateLimiter:
    """Resolve the request-scoped in-memory rate limiter."""
    return request.app.state.rate_limiter


def _client_identifier(request: Request) -> str:
    """Resolve a stable client key for rate limiting."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


async def enforce_rate_limit(
    request: Request,
    limiter: InMemoryRateLimiter = Depends(get_rate_limiter),
) -> None:
    """Reject requests that exceed configured per-client limits."""
    allowed, retry_after = await limiter.allow(_client_identifier(request))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded.",
            headers={"Retry-After": str(retry_after)},
        )


@router.get("/generate-data", response_model=GenerateDataResponse, dependencies=[Depends(enforce_rate_limit)])
async def generate_data(
    query: GenerateDataQuery = Depends(),
    settings: Settings = Depends(get_settings),
    service: MockarooService = Depends(get_mockaroo_service),
) -> GenerateDataResponse:
    """Generate data from Mockaroo as a full dataset or paginated chunk."""
    if query.total is not None and query.total > settings.max_total_records:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'total' cannot exceed {settings.max_total_records}.",
        )

    page = query.page or 1
    limit = query.limit or settings.default_page_limit

    if limit > settings.max_page_limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'limit' cannot exceed {settings.max_page_limit}.",
        )

    full_mode = query.total is not None and query.page is None and query.limit is None

    try:
        if full_mode:
            result = await service.generate_total(query.total, use_cache=query.use_cache)
            meta = GenerateDataMeta(
                mode="full",
                total_requested=query.total,
                total_returned=len(result.data),
                requests_made=result.requests_made,
                retries=result.retries,
                cache_hit=result.cache_hit,
                execution_time_ms=result.execution_time_ms,
            )
        else:
            result = await service.generate_page(
                page=page,
                limit=limit,
                total=query.total,
                use_cache=query.use_cache,
            )
            meta = GenerateDataMeta(
                mode="paginated",
                page=page,
                limit=limit,
                total_requested=query.total,
                total_returned=len(result.data),
                requests_made=result.requests_made,
                retries=result.retries,
                cache_hit=result.cache_hit,
                execution_time_ms=result.execution_time_ms,
            )

        return GenerateDataResponse(data=result.data, meta=meta)
    except UpstreamServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to generate data from Mockaroo: {exc}",
        ) from exc


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return a basic health status for readiness checks."""
    return HealthResponse(status="ok", service="mockaroo-wrapper")
