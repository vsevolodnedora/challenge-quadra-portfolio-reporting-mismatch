#!/usr/bin/env python3
"""THROW-AWAY validation: run the load/SQL patterns documented in plans/STORAGE.md against
the real probe JSON captured in $PROBE_OUT. Proves the DDL types, the read_json+UNNEST load,
the amendment extraction, the prices pivot, the kWh->MWh + hourly aggregation, and the
confirmed Deckungsbeitrag identity are implementable. Prints PASS/FAIL per check.
"""
from __future__ import annotations

import os
import pathlib

import duckdb

P = pathlib.Path(os.environ["PROBE_OUT"])
con = duckdb.connect()
fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(name)


# 1) report_asset: envelope -> UNNEST -> typed columns; DB identity holds
con.execute(f"""
CREATE OR REPLACE TABLE report_asset AS
WITH j AS (SELECT unnest(data) AS r FROM read_json('{P}/report_asset.json'))
SELECT r.asset_id, r.gross_revenue_eur, r.eeg_premium_eur, r.eeg_correction_eur,
       r.management_fee_eur, r.allocated_costs_eur, r.deckungsbeitrag_eur,
       r.db_per_mwh, r.total_feedin_mwh, r.availability_pct
FROM j;
""")
n, = con.execute("SELECT count(*) FROM report_asset").fetchone()
check("report_asset load + UNNEST", n == 5, f"{n} rows")
bad, = con.execute("""
SELECT count(*) FROM report_asset
WHERE abs(deckungsbeitrag_eur
          - (gross_revenue_eur + eeg_correction_eur - management_fee_eur - allocated_costs_eur)) > 0.005
""").fetchone()
check("Deckungsbeitrag identity (db = gross + corr - fee - costs)", bad == 0, f"{bad} violations")

# 2) prices (full 1488): pivot to one row per (date,hour) with DA/ID
con.execute(f"""
CREATE OR REPLACE TABLE prices AS
WITH j AS (SELECT unnest(data) AS r FROM read_json('{P}/prices_all.json'))
SELECT CAST(r.date AS DATE) AS date, r.hour, r.product, r.price_eur_mwh, r.id_volume_mwh FROM j;
CREATE OR REPLACE TABLE prices_hourly AS
SELECT date, hour,
       max(price_eur_mwh) FILTER (WHERE product='DA') AS da_price_eur_mwh,
       max(price_eur_mwh) FILTER (WHERE product='ID') AS id_price_eur_mwh,
       max(id_volume_mwh) AS id_volume_mwh
FROM prices GROUP BY 1,2;
""")
np, = con.execute("SELECT count(*) FROM prices").fetchone()
nh, = con.execute("SELECT count(*) FROM prices_hourly").fetchone()
nnull, = con.execute("SELECT count(*) FROM prices_hourly WHERE da_price_eur_mwh IS NULL OR id_price_eur_mwh IS NULL").fetchone()
check("prices load (1488) + hourly pivot (744)", np == 1488 and nh == 744, f"prices={np} hourly={nh}")
check("every hour has both DA and ID", nnull == 0, f"{nnull} incomplete hours")

# 3) contract amendment: struct access AND robust json_extract via to_json
con.execute(f"""
CREATE OR REPLACE TABLE contract_amd AS
SELECT asset_id, amendment_id, amendment_effective,
       amendment.fee_model AS amd_fee_model_struct,
       json_extract_string(to_json(amendment), '$.fee_model') AS amd_fee_model_json,
       TRY_CAST(json_extract(to_json(amendment), '$.fee_share_pct') AS DOUBLE) AS amd_fee_share_pct
FROM read_json('{P}/contract_with_amendment.json');
""")
row = con.execute("SELECT amd_fee_model_struct, amd_fee_model_json, amd_fee_share_pct, amendment_effective FROM contract_amd").fetchone()
check("amendment struct access", row[0] == "revenue_share", f"got {row[0]}")
check("amendment json_extract(to_json())", row[1] == "revenue_share", f"got {row[1]}")
check("amendment fee_share_pct extracted", abs((row[2] or 0) - 1.2) < 1e-9, f"got {row[2]}")
check("amendment_effective present", str(row[3]) == "2025-01-15", f"got {row[3]}")

# 4) feedin: kWh->MWh + hourly aggregation mechanics (sample of 5 rows)
con.execute(f"""
CREATE OR REPLACE TABLE feedin_qh AS
WITH j AS (SELECT unnest(data) AS r FROM read_json('{P}/feedin_day.json'))
SELECT r.asset_id, CAST(r.timestamp AS TIMESTAMP) AS ts, CAST(r.timestamp AS DATE) AS date,
       EXTRACT(hour FROM CAST(r.timestamp AS TIMESTAMP)) AS hour,
       r.energy_kwh/1000.0 AS energy_mwh, r.quality_flag, r.source_system FROM j;
""")
dup, = con.execute("SELECT count(*)-count(DISTINCT (asset_id, ts)) FROM feedin_qh").fetchone()
mwh, = con.execute("SELECT round(sum(energy_mwh),6) FROM feedin_qh").fetchone()
check("feedin kWh->MWh + timestamp parse", dup == 0 and mwh is not None, f"sum_mwh={mwh}, dup_keys={dup}")

# 5) eeg + costs_metadata shapes
eeg = con.execute(f"""
WITH j AS (SELECT unnest(data) AS r FROM read_json('{P}/eeg.json'))
SELECT r.technology, r.premium_eur_mwh, r.correction_flag, r.correction_amount_eur FROM j
WHERE r.technology='wind_onshore'
""").fetchone()
check("eeg wind_onshore correction = -18400", eeg is not None and abs(eeg[3] - (-18400)) < 1e-6, f"{eeg}")
meta = con.execute(f"""
WITH j AS (SELECT unnest(data) AS r FROM read_json('{P}/costs_metadata.json'))
SELECT r.assets_excluded, len(r.assets_excluded) AS n FROM j
""").fetchone()
check("costs_metadata assets_excluded is a list (len 0)", meta[1] == 0, f"assets_excluded={meta[0]}")

print()
if fails:
    print("VALIDATION FAILED:", fails)
    raise SystemExit(1)
print("ALL STORAGE SQL PATTERNS VALIDATED against real probe JSON.")
