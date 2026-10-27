"""Elexon BMRS data adapter. Only the two user-specified January 2026 sources.

WINDFOR is filtered by publication time. FUELHH is filtered by settlement
**date**, not forecast publication time. This distinction is essential.
"""
from __future__ import annotations

import asyncio
from typing import Callable
import httpx

BASE = "https://data.elexon.co.uk/bmrs/api/v1"
SOURCES = {
    "WINDFOR": {
        "publishDateTimeFrom": "2026-01-01T00:00:00Z",
        "publishDateTimeTo": "2026-02-01T00:00:00Z",
    },
    "FUELHH": {
        "settlementDateFrom": "2026-01-02",
        "settlementDateTo": "2026-02-02",
    },
}

class UpstreamError(RuntimeError):
    """The source server could not supply trustworthy records."""


def endpoint(dataset: str) -> str:
    return f"{BASE}/datasets/{dataset}/stream"


async def fetch_dataset(client: httpx.AsyncClient, dataset: str) -> list[dict]:
    """Fetch the precise supplied URL, retrying transient errors only."""
    if dataset not in SOURCES:
        raise ValueError(f"Unsupported dataset: {dataset}")
    for attempt in range(3):
        try:
            response = await client.get(endpoint(dataset), params=SOURCES[dataset])
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(1.0 * (2 ** attempt))
                continue
            response.raise_for_status()
            data = response.json()
            # /stream endpoints should be arrays, but permit an official JSON
            # envelope for compatibility with Elexon's non-streaming endpoints.
            if isinstance(data, dict):
                data = data.get("data")
            if not isinstance(data, list):
                raise UpstreamError(f"{dataset}: expected JSON array of records")
            if any(not isinstance(row, dict) for row in data):
                raise UpstreamError(f"{dataset}: invalid non-object record")
            if not data:
                raise UpstreamError(f"{dataset}: source returned an empty data window")
            return data
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
            # 4xx errors except 429 are typically invalid queries; fail fast.
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (429, 500, 502, 503, 504):
                raise UpstreamError(f"{dataset}: Elexon HTTP {exc.response.status_code}") from exc
            if attempt == 2:
                raise UpstreamError(f"{dataset}: Elexon request failed: {type(exc).__name__}") from exc
            await asyncio.sleep(1.0 * (2 ** attempt))
    raise UpstreamError(f"{dataset}: retries exhausted")


async def load_january(on_progress: Callable[[int, int], None] | None = None) -> tuple[list[dict], list[dict]]:
    """Return (wind-only FUELHH, all WINDFOR revisions).

    No mock/fallback data is ever substituted for a failed Elexon call.
    """
    timeout = httpx.Timeout(120.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"Accept": "application/json", "User-Agent": "WindwardPilot/2.0"}) as client:
        # Sequential requests reduce load against the public upstream.
        forecast = await fetch_dataset(client, "WINDFOR")
        if on_progress:
            on_progress(1, 2)
        outturn = await fetch_dataset(client, "FUELHH")
        if on_progress:
            on_progress(2, 2)
    wind = [row for row in outturn if row.get("fuelType") == "WIND"]
    if not wind:
        raise UpstreamError("FUELHH returned no WIND fuelType records")
    return wind, forecast
