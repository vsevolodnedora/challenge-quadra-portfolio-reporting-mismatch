#!/usr/bin/env python3
"""THROW-AWAY probe #2: fills gaps from probe_api.py.
1) /data/prices has NO page/page_size params -> confirm single-response + true count.
2) Find the nested `amendment` object shape and one row per fee_model (incl. ppa).
Reads key from .env, writes raw JSON to PROBE_OUT, prints secret-free summary.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import requests
from dotenv import dotenv_values

BASE = "https://poc16264.quadra-energy.com/api/v1"
OUT = pathlib.Path(os.environ["PROBE_OUT"])
OUT.mkdir(parents=True, exist_ok=True)
KEY = dotenv_values(".env")["QUADRA_API_KEY"]
S = requests.Session()
S.headers["X-API-Key"] = KEY


def get(path, params):
    r = S.get(f"{BASE}{path}", params=params, timeout=60)
    time.sleep(0.3)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {"_raw": r.text[:400]}


# 1) prices, correct call (no page_size)
for prod in ("all", "DA", "ID"):
    sc, body = get("/data/prices", {"date_from": "2025-01-01", "date_to": "2025-01-31", "product": prod})
    meta = body.get("meta", {}) if isinstance(body, dict) else {}
    n = len(body.get("data", [])) if isinstance(body, dict) else 0
    print(f"prices product={prod}: [{sc}] rows_returned={n} meta={json.dumps(meta)}")
    (OUT / f"prices_{prod}.json").write_text(json.dumps(body, indent=2))
    if prod == "all" and n:
        print("  first row:", json.dumps(body["data"][0]))

# 2) scan contracts for amendment shape + one row per fee_model
seen_models = {}
amendment_example = None
for page in range(1, 6):  # 847 / 200 -> 5 pages
    sc, body = get("/data/contracts", {"include_amendments": "true", "page": page, "page_size": 200})
    rows = body.get("data", []) if isinstance(body, dict) else []
    for row in rows:
        fm = row.get("fee_model")
        if fm not in seen_models:
            seen_models[fm] = row
        amd = row.get("amendment")
        if amd not in (None, [], {}) and amendment_example is None:
            amendment_example = row
    if amendment_example and {"fixed", "revenue_share", "ppa"} <= set(seen_models):
        break

print("\nfee_model examples:")
for fm, row in seen_models.items():
    slim = {k: row.get(k) for k in
            ("asset_id", "fee_model", "fee_fixed_eur_mwh", "fee_share_pct", "amendment_id", "amendment_effective", "amendment")}
    print(f"  {fm}: {json.dumps(slim)}")

print("\namendment example (full row):")
print(json.dumps(amendment_example, indent=2) if amendment_example else "  none found in scanned pages")
if amendment_example:
    (OUT / "contract_with_amendment.json").write_text(json.dumps(amendment_example, indent=2))
