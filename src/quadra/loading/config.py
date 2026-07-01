"""Loader configuration: credentials + the fixed January-2025 window.

Never hard-code or log the API key. It is read from `.env` (or the process environment,
which takes precedence for CI) and only ever attached as the `X-API-Key` header.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import dotenv_values

BASE_URL = "https://poc16264.quadra-energy.com/api/v1"
REPORT_MONTH = "2025-01"
DATE_FROM = "2025-01-01"
DATE_TO = "2025-01-31"

# Live-verified Phase-0 trace set (see plans/DATA_LOADING.md §8). Every attribute confirmed
# against the API 2026-07-01; these five exercise every mechanic + all three deviation zones.
TRACE_ASSETS = ["WND-0042", "SOL-0007", "SOL-0214", "SOL-0032", "WND-0004"]

DEFAULT_RAW_ROOT = "data/raw"


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str = BASE_URL
    date_from: str = DATE_FROM
    date_to: str = DATE_TO
    report_month: str = REPORT_MONTH


def load_config(env_path: str = ".env") -> Config:
    """Fail fast if the key is missing/empty — we cannot extract without it."""
    file_vals = dotenv_values(env_path)
    key = os.environ.get("QUADRA_API_KEY") or file_vals.get("QUADRA_API_KEY")
    if not key or not key.strip():
        raise SystemExit(
            "QUADRA_API_KEY is missing or empty (checked process env and .env). "
            "Set it before extracting; never commit the value."
        )
    return Config(api_key=key.strip())
