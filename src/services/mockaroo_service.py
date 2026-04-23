"""Service layer for calling and aggregating Mockaroo data."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from cachetools import TTLCache

from src.core.config import Settings
from src.utils.retry import RetryableRequestError, retry_async


class UpstreamServiceError(Exception):
    """Raised when the upstream provider cannot satisfy a request."""


@dataclass(slots=True)
class GenerationResult:
    """Represents generated records and execution metrics."""

    data: list[dict[str, Any]]
    requests_made: int
    retries: int
    execution_time_ms: int
    cache_hit: bool


@dataclass(slots=True)
class _UpstreamAggregateResult:
    """Represents one upstream aggregation operation."""

    data: list[dict[str, Any]]
    requests_made: int
    retries: int


class MockarooService:
    """Orchestrates Mockaroo batch requests and combines responses."""

    def __init__(self, settings: Settings, logger: logging.Logger | None = None) -> None:
        """Initialize service dependencies and HTTP client."""
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)
        self._client = httpx.AsyncClient(timeout=self._settings.request_timeout_seconds)
        self._snapshot_lock = asyncio.Lock()
        self._data_root = Path(self._settings.data_storage_dir)
        self._cache: TTLCache[tuple[Any, ...], list[dict[str, Any]]] = TTLCache(
            maxsize=self._settings.cache_max_entries,
            ttl=self._settings.cache_ttl_seconds,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def generate_total(self, total: int, use_cache: bool = True) -> GenerationResult:
        """Generate an explicit total number of records."""
        return await self._generate_records(
            records_to_generate=total,
            cache_key=("total", total),
            use_cache=use_cache,
            offset=0,
            max_records=total,
        )

    async def generate_page(
        self,
        *,
        page: int,
        limit: int,
        total: int | None,
        use_cache: bool = True,
    ) -> GenerationResult:
        """Generate one page worth of records based on pagination parameters."""
        records_to_generate = limit

        if total is not None:
            offset = (page - 1) * limit
            if offset >= total:
                return GenerationResult(
                    data=[],
                    requests_made=0,
                    retries=0,
                    execution_time_ms=0,
                    cache_hit=False,
                )
            records_to_generate = min(limit, total - offset)

        return await self._generate_records(
            records_to_generate=records_to_generate,
            cache_key=("page", page, limit, total),
            use_cache=use_cache,
            offset=(page - 1) * limit,
            max_records=total,
        )

    async def _generate_records(
        self,
        *,
        records_to_generate: int,
        cache_key: tuple[Any, ...],
        use_cache: bool,
        offset: int,
        max_records: int | None,
    ) -> GenerationResult:
        """Serve records from latest snapshot, creating it from Mockaroo on first request."""
        start_time = time.perf_counter()

        if records_to_generate <= 0:
            return GenerationResult([], 0, 0, 0, False)

        cached_data = self._try_get_cache(cache_key, use_cache)
        if cached_data is not None:
            return GenerationResult(
                data=cached_data,
                requests_made=0,
                retries=0,
                execution_time_ms=0,
                cache_hit=True,
            )

        snapshot_payload = self._load_latest_snapshot()
        if snapshot_payload is not None:
            snapshot_data, snapshot_path = snapshot_payload
            data = self._slice_snapshot(
                snapshot_data=snapshot_data,
                offset=offset,
                limit=records_to_generate,
                max_records=max_records,
            )
            execution_time_ms = int((time.perf_counter() - start_time) * 1000)
            self._logger.info(
                "snapshot_data_served",
                extra={
                    "snapshot_file": str(snapshot_path),
                    "offset": offset,
                    "limit": records_to_generate,
                    "max_records": max_records,
                    "records_returned": len(data),
                    "execution_time_ms": execution_time_ms,
                },
            )

            if self._settings.cache_enabled and use_cache:
                self._cache[cache_key] = copy.deepcopy(data)

            return GenerationResult(
                data=data,
                requests_made=0,
                retries=0,
                execution_time_ms=execution_time_ms,
                cache_hit=False,
            )

        async with self._snapshot_lock:
            snapshot_payload = self._load_latest_snapshot()
            if snapshot_payload is None:
                records_for_first_snapshot = self._settings.initial_snapshot_records
                upstream_result = await self._fetch_records(records_for_first_snapshot)
                snapshot_path = self._write_snapshot(upstream_result.data)
                snapshot_data = upstream_result.data

                self._logger.info(
                    "snapshot_created",
                    extra={
                        "snapshot_file": str(snapshot_path),
                        "records_saved": len(snapshot_data),
                        "initial_snapshot_records": self._settings.initial_snapshot_records,
                        "requests_made": upstream_result.requests_made,
                        "retries": upstream_result.retries,
                    },
                )
            else:
                snapshot_data, _ = snapshot_payload
                upstream_result = _UpstreamAggregateResult(data=[], requests_made=0, retries=0)

        data = self._slice_snapshot(
            snapshot_data=snapshot_data,
            offset=offset,
            limit=records_to_generate,
            max_records=max_records,
        )

        execution_time_ms = int((time.perf_counter() - start_time) * 1000)

        self._logger.info(
            "mockaroo_generation_completed",
            extra={
                "requests_made": upstream_result.requests_made,
                "retries": upstream_result.retries,
                "records_requested": records_to_generate,
                "records_returned": len(data),
                "offset": offset,
                "execution_time_ms": execution_time_ms,
            },
        )

        if self._settings.cache_enabled and use_cache:
            self._cache[cache_key] = copy.deepcopy(data)

        return GenerationResult(
            data=data,
            requests_made=upstream_result.requests_made,
            retries=upstream_result.retries,
            execution_time_ms=execution_time_ms,
            cache_hit=False,
        )

    async def _fetch_records(self, total_records: int) -> _UpstreamAggregateResult:
        """Fetch and aggregate records from Mockaroo in concurrent batches."""
        if total_records <= 0:
            return _UpstreamAggregateResult(data=[], requests_made=0, retries=0)

        batch_sizes = self._compute_batch_sizes(total_records)
        data: list[dict[str, Any]] = []
        total_retries = 0
        requests_made = 0

        for start_index in range(0, len(batch_sizes), self._settings.max_concurrency):
            wave = batch_sizes[start_index : start_index + self._settings.max_concurrency]
            tasks = [
                asyncio.create_task(
                    self._fetch_batch(batch_id=start_index + offset + 1, batch_size=batch_size)
                )
                for offset, batch_size in enumerate(wave)
            ]

            wave_results = await asyncio.gather(*tasks, return_exceptions=True)
            failures = [result for result in wave_results if isinstance(result, Exception)]

            if failures:
                self._logger.error(
                    "mockaroo_batch_failure",
                    extra={
                        "failed_batches": len(failures),
                        "wave_size": len(wave),
                        "total_batches": len(batch_sizes),
                        "records_to_generate": total_records,
                    },
                )
                if isinstance(failures[0], UpstreamServiceError):
                    raise failures[0]
                raise UpstreamServiceError("Failed to fetch data from Mockaroo after retries.") from failures[0]

            for batch_data, retries in wave_results:
                data.extend(batch_data)
                total_retries += retries
                requests_made += 1

        # Re-assign sequential IDs across the entire aggregated dataset
        for idx, record in enumerate(data, start=1):
            if "id" in record:
                record["id"] = idx

        return _UpstreamAggregateResult(
            data=data,
            requests_made=requests_made,
            retries=total_retries,
        )

    async def _fetch_batch(self, *, batch_id: int, batch_size: int) -> tuple[list[dict[str, Any]], int]:
        """Fetch one batch of records with retry and rate-limit handling."""
        retry_count = 0

        async def operation() -> list[dict[str, Any]]:
            response = await self._client.get(
                self._settings.mockaroo_url,
                params={
                    "key": self._settings.mockaroo_api_key,
                    "count": batch_size,
                },
            )

            response_text = response.text
            daily_limit_reached = (
                response.status_code >= 500
                and "limited to 200 requests per day" in response_text.lower()
            )

            if daily_limit_reached:
                raise UpstreamServiceError(
                    "Mockaroo daily request limit reached for this API key. "
                    "Wait for limit reset, use a paid plan, or provide an existing local snapshot."
                )

            if response.status_code == 429:
                retry_after_header = response.headers.get("Retry-After")
                retry_after = _parse_retry_after(retry_after_header)
                raise RetryableRequestError(
                    message="Mockaroo rate-limited request.",
                    retry_after_seconds=retry_after,
                    status_code=429,
                )

            if response.status_code >= 500:
                raise RetryableRequestError(
                    message=f"Mockaroo returned server error {response.status_code}.",
                    status_code=response.status_code,
                )

            if response.status_code >= 400:
                raise UpstreamServiceError(
                    f"Mockaroo returned non-retryable status code {response.status_code}: "
                    f"{response_text[:200]}"
                )

            payload = response.json()
            if not isinstance(payload, list):
                raise UpstreamServiceError("Mockaroo response payload was not a list.")

            return payload

        def should_retry(exc: Exception) -> bool:
            return isinstance(exc, (RetryableRequestError, httpx.TimeoutException, httpx.TransportError))

        def on_retry(attempt: int, exc: Exception, delay_seconds: float) -> None:
            nonlocal retry_count
            retry_count += 1
            self._logger.warning(
                "mockaroo_retry_attempt",
                extra={
                    "batch_id": batch_id,
                    "attempt": attempt,
                    "delay_seconds": round(delay_seconds, 3),
                    "error": str(exc),
                },
            )

        try:
            payload = await retry_async(
                operation,
                max_attempts=self._settings.retry_max_attempts,
                base_delay_seconds=self._settings.retry_base_delay_seconds,
                should_retry=should_retry,
                on_retry=on_retry,
            )
        except UpstreamServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UpstreamServiceError("Mockaroo request failed after retry attempts.") from exc

        return payload, retry_count

    def _compute_batch_sizes(self, total: int) -> list[int]:
        """Split total record count into Mockaroo-compatible batch sizes."""
        full_batches = total // self._settings.max_batch_size
        remaining = total % self._settings.max_batch_size

        sizes = [self._settings.max_batch_size] * full_batches
        if remaining:
            sizes.append(remaining)
        return sizes

    def _try_get_cache(
        self,
        cache_key: tuple[Any, ...],
        use_cache: bool,
    ) -> list[dict[str, Any]] | None:
        """Read a dataset copy from cache when enabled."""
        if not (self._settings.cache_enabled and use_cache):
            return None

        cached = self._cache.get(cache_key)
        if cached is None:
            return None

        self._logger.info("mockaroo_cache_hit", extra={"cache_key": str(cache_key)})
        return copy.deepcopy(cached)

    def _load_latest_snapshot(self) -> tuple[list[dict[str, Any]], Path] | None:
        """Load records from the most recently modified snapshot file."""
        if not self._data_root.exists():
            return None

        candidates: list[Path] = []
        for entry in self._data_root.iterdir():
            if not entry.is_dir():
                continue
            candidate = entry / "data.json"
            if candidate.exists() and candidate.is_file():
                candidates.append(candidate)

        if not candidates:
            return None

        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)

        for candidate in candidates:
            try:
                with candidate.open("r", encoding="utf-8-sig") as file_handle:
                    payload = json.load(file_handle)

                if not isinstance(payload, list):
                    self._logger.warning(
                        "snapshot_invalid_payload",
                        extra={"snapshot_file": str(candidate)},
                    )
                    continue

                return payload, candidate
            except (OSError, json.JSONDecodeError) as exc:
                self._logger.warning(
                    "snapshot_read_failed",
                    extra={"snapshot_file": str(candidate), "error": str(exc)},
                )

        return None

    def _write_snapshot(self, data: list[dict[str, Any]]) -> Path:
        """Persist a new snapshot as data/<date>/data.json."""
        self._data_root.mkdir(parents=True, exist_ok=True)
        folder_name = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        snapshot_dir = self._data_root / folder_name
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        snapshot_file = snapshot_dir / "data.json"
        temp_file = snapshot_dir / "data.json.tmp"

        with temp_file.open("w", encoding="utf-8") as file_handle:
            json.dump(data, file_handle, ensure_ascii=True)

        temp_file.replace(snapshot_file)
        return snapshot_file

    def _slice_snapshot(
        self,
        *,
        snapshot_data: list[dict[str, Any]],
        offset: int,
        limit: int,
        max_records: int | None,
    ) -> list[dict[str, Any]]:
        """Slice records from a snapshot for full or paginated API responses."""
        bounded_data = snapshot_data
        if max_records is not None:
            bounded_data = bounded_data[: max(max_records, 0)]

        if offset >= len(bounded_data):
            return []

        return copy.deepcopy(bounded_data[offset : offset + limit])


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After header to seconds when present."""
    if value is None:
        return None

    try:
        parsed = float(value)
    except ValueError:
        return None

    return max(parsed, 0.0)
