#!/usr/bin/env python3
"""THROW-AWAY probe. Grounds plans/STORAGE.md + plans/DATA_LOADING.md in real API
behavior. Reads the key from .env, writes raw JSON to the scratchpad (never the repo),
prints a compact, secret-free summary. Safe to delete after the docs are finalized.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import requests
from dotenv import dotenv_values

BASE = "https://poc16264.quadra-energy.com/api/v1"
OUT = pathlib.Path(os.environ["PROBE_OUT"])
OUT.mkdir(parents=True, exist_ok=True)

cfg = dotenv_values(".env")
KEY = cfg["QUADRA_API_KEY"]
SESSION = requests.Session()
SESSION.headers["X-API-Key"] = KEY

RL = ("X-RateLimit-Remaining", "X-RateLimit-Reset")


def typ(v):
    if v is None:
        return "null"
    return type(v).__name__


def probe(tag: str, path: str, params: dict | None = None):
    url = f"{BASE}{path}"
    try:
        r = SESSION.get(url, params=params or {}, timeout=60)
    except Exception as e:  # noqa: BLE001
        print(f"\n### {tag}  ERROR {e}")
        return None
    body = None
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {"_raw_text": r.text[:500]}
    (OUT / f"{tag}.json").write_text(json.dumps(body, indent=2))
    rl = {k: r.headers.get(k) for k in RL}
    print(f"\n### {tag}  [{r.status_code}]  {path}  params={params or {}}")
    print(f"    rate-limit: {rl}")
    if isinstance(body, dict) and "meta" in body:
        print(f"    meta: {json.dumps(body['meta'])}")
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        data = body["data"]
        print(f"    data rows: {len(data)}")
        if data:
            row = data[0]
            if isinstance(row, dict):
                schema = {k: typ(v) for k, v in row.items()}
                print(f"    row schema: {json.dumps(schema)}")
                print(f"    row[0]: {json.dumps(row)}")
    elif isinstance(body, dict):
        print(f"    body keys: {list(body.keys())}")
        print(f"    body: {json.dumps(body)[:400]}")
    time.sleep(0.3)  # be gentle; well under 120/min
    return body


def main():
    # --- inventory ---
    probe("assets_all", "/assets", {"status": "all", "page": 1, "page_size": 5})
    probe("assets_inactive", "/assets", {"status": "inactive", "page": 1, "page_size": 5})

    # --- contracts: amendment shape + does include_amendments change row count? ---
    probe("contracts_one", "/data/contracts", {"asset_id": "WND-0042"})
    probe("contracts_amd_true", "/data/contracts", {"include_amendments": "true", "page": 1, "page_size": 5})
    probe("contracts_amd_false", "/data/contracts", {"include_amendments": "false", "page": 1, "page_size": 5})

    # --- feedin: per-day shape (expect 96) + TRUE full-month total for sizing ---
    probe("feedin_day", "/data/feedin",
          {"asset_id": "WND-0042", "date_from": "2025-01-07", "date_to": "2025-01-07", "page_size": 5})
    probe("feedin_total", "/data/feedin",
          {"date_from": "2025-01-01", "date_to": "2025-01-31", "page_size": 1})

    # --- schedule: full-month total for sizing ---
    probe("schedule_total", "/data/schedule",
          {"date_from": "2025-01-01", "date_to": "2025-01-31", "page_size": 1})

    # --- prices: expect 1488 ---
    probe("prices_total", "/data/prices",
          {"date_from": "2025-01-01", "date_to": "2025-01-31", "page_size": 1})
    probe("prices_day", "/data/prices",
          {"date_from": "2025-01-07", "date_to": "2025-01-07", "product": "all", "page_size": 5})

    # --- eeg / costs / metadata ---
    probe("eeg", "/data/eeg_premium", {"month": "2025-01"})
    probe("costs_total", "/data/costs", {"month": "2025-01", "page_size": 1})
    probe("costs_one_asset", "/data/costs", {"month": "2025-01", "asset_id": "SOL-0007"})
    probe("costs_metadata", "/data/costs/metadata", {"month": "2025-01"})

    # --- report at all three granularities; grab a few real asset rows for the DB-formula anchor ---
    probe("report_asset", "/report/monthly", {"month": "2025-01", "granularity": "asset", "page_size": 5})
    probe("report_region", "/report/monthly", {"month": "2025-01", "granularity": "region"})
    probe("report_portfolio", "/report/monthly", {"month": "2025-01", "granularity": "portfolio"})

    # --- edge behaviors we must document ---
    probe("edge_out_of_window", "/data/feedin",
          {"asset_id": "WND-0042", "date_from": "2025-02-01", "date_to": "2025-02-01"})
    probe("edge_bad_enum", "/data/prices",
          {"date_from": "2025-01-01", "date_to": "2025-01-01", "product": "bogus"})
    probe("edge_pagesize_over_max", "/data/feedin",
          {"asset_id": "WND-0042", "date_from": "2025-01-07", "date_to": "2025-01-07", "page_size": 999})

    # edge: missing key -> expect 401 (fresh session, no header)
    s = requests.Session()
    r = s.get(f"{BASE}/assets", params={"status": "all"}, timeout=60)
    print(f"\n### edge_no_key  [{r.status_code}]  /assets  -> {r.text[:200]}")

    print("\n=== done; raw JSON in", OUT, "===")


if __name__ == "__main__":
    main()
