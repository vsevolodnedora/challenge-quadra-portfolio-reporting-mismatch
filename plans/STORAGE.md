# Storage System

Implementation-ready specification for the analytical store that holds the QUADRA raw
extracts and the reconstructed January 2025 report. Companion to `plans/DATA_LOADING.md`
(which produces the immutable raw JSON this system loads). Follows the Design Principles in
`README.md`: preserve immutable raw extracts, persist normalized/derived tables in DuckDB,
use **one** transform engine (DuckDB SQL) so lineage stays auditable, calculate at full
precision and round only at the comparison boundary, and rebuild all outputs from raw via
scripts/queries.

Column types below are chosen from the live-probed source shapes (2026-07-01).

---

## 1. Design intent

- **DuckDB is the single engine.** Ingestion, typing, joins, aggregation, and reconciliation
  are all SQL. The reconstruction SQL is meant to transcribe directly into the DSF
  `BUSINESS LOGIC` section — so the store *is* the spec.
- **Three layers, one direction of flow:** `raw → stg → recon`. Never write upward.
- **Raw is source-faithful; interpretation is deferred.** The `raw` schema mirrors the API
  byte-for-byte (dates kept as strings, `amendment` kept as JSON). Unit conversion, date
  parsing, and business rules happen in `stg`/`recon`, so any number is traceable back to a
  raw row and its source file.
- **Deterministic rebuild.** Every `stg`/`recon` object is `CREATE OR REPLACE` from raw;
  wiping and rebuilding the warehouse from a raw run yields identical results.

---

## 2. On-disk layout

```
data/
  raw/                     # immutable JSON extracts written by the loader (gitignored)
    <endpoint>/<partition>/page-*.json
    <endpoint>/<partition>/_done.json   # resume marker (excluded from the load glob)
    _manifest.jsonl                     # provenance, one line per response
    _findings.json                      # loader's recorded (non-fatal) content findings
  warehouse.duckdb         # the analytical database (gitignored)
  warehouse_findings.json  # storage 'observe' anomalies = candidate deviations (gitignored)
src/quadra/storage/load_raw.py  # raw load (verbatim JSON via read_json_objects) — replaces 01_raw.sql
sql/
  02_stg.sql      # normalized/typed layer (all interpretation lives here)
  03_recon.sql    # reconstruction + delta views (+ rounding macros)
  04_checks.sql   # recon.checks: gate (must-pass) + observe (recorded) validations
```

Raw loading is done in Python (`load_raw.py`) rather than a static `01_raw.sql`: the verbatim
`read_json_objects` pattern is uniform across all 11 endpoints and benefits from per-table
existence checks, so a generated load is DRY-er than hand-written DDL. `build_warehouse.py`
runs `load_raw.load_all()` then the three SQL files in order.

Add `data/` to `.gitignore` (raw extracts and the DuckDB file are reproducible artifacts and
may contain the full dataset — never commit them).

Schemas inside the database: `raw`, `stg`, `recon`.

---

## 3. Raw layer (`raw.*`)

**Implemented design (verbatim JSON, zero coercion).** Each `raw.*` table holds exactly two
columns: `_record JSON` (the row's *byte-verbatim* source JSON) and `_src_file VARCHAR`
(provenance). The load uses `read_json_objects` (not typed `read_json`), so no field is
dropped, renamed, or type-coerced at this layer — an integer `0` stays `0`, `2.769` stays
`2.769`, and a nested `amendment` object stays intact. This is the deliberate consequence of
the project directive to record everything faithfully and defer *all* interpretation to `stg`
(where a raw-data quirk might itself be one of the three deviations). Implemented in
`src/quadra/storage/load_raw.py`:

```sql
CREATE OR REPLACE TABLE raw.feedin AS
SELECT je.value AS _record, d.filename AS _src_file
FROM read_json_objects('data/raw/feedin/**/page-*.json', filename := true) AS d,
     LATERAL unnest(CAST(json_extract(d.json, '$.data') AS JSON[])) AS je(value);
```

All typing (kWh→MWh, timestamp/date parse, amendment flattening, enum reading) happens once in
`stg` (`sql/02_stg.sql`) via `json_extract` / `json_extract_string` on `_record`. The
`_manifest.jsonl` on disk holds the remaining provenance (params, fetched_at, ratelimit).

The column lists below therefore document the **`stg`-facing shapes** (what `stg` extracts from
`_record`), not literal `raw` DDL. Because raw is untyped JSON, the amendment-inference caveat
no longer applies: `json_extract_string(_record, '$.amendment.fee_model')` reads it directly.

### Column types (source-faithful)

Dates/timestamps stay `VARCHAR` in raw (parsed in `stg`). Numerics that arrive as int-or-float
are stored `DOUBLE`.

```sql
-- raw.assets            (847;  status=inactive -> 12)
asset_id VARCHAR, technology VARCHAR, region VARCHAR,
installed_capacity_kw DOUBLE, commissioning_date VARCHAR, status VARCHAR, _src_file VARCHAR

-- raw.contracts         (847;  include_amendments=true)
asset_id VARCHAR, contract_id VARCHAR, owner_name VARCHAR, technology VARCHAR, region VARCHAR,
installed_capacity_kw DOUBLE, commissioning_date VARCHAR, contract_start VARCHAR,
contract_end VARCHAR, fee_model VARCHAR, fee_fixed_eur_mwh DOUBLE, fee_share_pct DOUBLE,
operator_id VARCHAR, scada_system VARCHAR, amendment_id VARCHAR, amendment_effective VARCHAR,
amendment JSON, _src_file VARCHAR
-- amendment is null or e.g. {"fee_model":"revenue_share","fee_share_pct":1.2}

-- raw.feedin            (2,520,672)
asset_id VARCHAR, timestamp VARCHAR, energy_kwh DOUBLE,
quality_flag VARCHAR, source_system VARCHAR, _src_file VARCHAR

-- raw.schedule          (626,472  -- NOT a complete grid)
asset_id VARCHAR, date VARCHAR, hour INTEGER, scheduled_mwh DOUBLE, _src_file VARCHAR

-- raw.prices            (1,488;  DA 744 + ID 744)
date VARCHAR, hour INTEGER, product VARCHAR, price_eur_mwh DOUBLE,
id_volume_mwh DOUBLE, _src_file VARCHAR

-- raw.eeg_premium       (2)
month VARCHAR, technology VARCHAR, premium_eur_mwh DOUBLE,
correction_flag BOOLEAN, correction_amount_eur DOUBLE, _src_file VARCHAR

-- raw.costs             (3,388  = 847 x 4 categories)
asset_id VARCHAR, cost_category VARCHAR, allocated_amount_eur DOUBLE, allocation_basis VARCHAR,
total_pool_eur DOUBLE, active_assets_in_pool INTEGER, _src_file VARCHAR

-- raw.costs_metadata    (1)
allocation_date VARCHAR, note VARCHAR, assets_excluded JSON, _src_file VARCHAR

-- raw.report_asset      (847)  -- the target being reconstructed
asset_id VARCHAR, gross_revenue_eur DOUBLE, eeg_premium_eur DOUBLE, eeg_correction_eur DOUBLE,
management_fee_eur DOUBLE, allocated_costs_eur DOUBLE, deckungsbeitrag_eur DOUBLE,
db_per_mwh DOUBLE, total_feedin_mwh DOUBLE, availability_pct DOUBLE, _src_file VARCHAR

-- raw.report_region     (4)   -- note: field is asset_count
region VARCHAR, gross_revenue_eur DOUBLE, eeg_premium_eur DOUBLE, eeg_correction_eur DOUBLE,
management_fee_eur DOUBLE, allocated_costs_eur DOUBLE, deckungsbeitrag_eur DOUBLE,
total_feedin_mwh DOUBLE, asset_count INTEGER, _src_file VARCHAR

-- raw.report_portfolio  (1)
total_gross_revenue_eur DOUBLE, total_eeg_premium_eur DOUBLE, total_eeg_correction_eur DOUBLE,
total_management_fee_eur DOUBLE, total_allocated_costs_eur DOUBLE, total_db_eur DOUBLE,
total_feedin_mwh DOUBLE, db_margin_pct DOUBLE, active_asset_count INTEGER, _src_file VARCHAR
```

Raw tables are treated as read-only after load.

---

## 4. Normalized layer (`stg.*`)

Typed, unit-corrected, keyed. This is where the two normalization rules with the highest
error potential are applied **once**: kWh→MWh and the naive-timestamp parse.

```sql
-- stg.assets: typed dates + inactive flag
CREATE OR REPLACE TABLE stg.assets AS
SELECT asset_id, technology, region, installed_capacity_kw,
       CAST(commissioning_date AS DATE) AS commissioning_date,
       status, (status = 'inactive') AS is_inactive
FROM raw.assets;

-- stg.contracts: parse dates, flatten the nested amendment
CREATE OR REPLACE TABLE stg.contracts AS
SELECT asset_id, contract_id, owner_name, technology, region, installed_capacity_kw,
       CAST(contract_start AS DATE) AS contract_start,
       CAST(contract_end   AS DATE) AS contract_end,
       fee_model, fee_fixed_eur_mwh, fee_share_pct, operator_id, scada_system,
       amendment_id,
       TRY_CAST(amendment_effective AS DATE) AS amendment_effective,
       (amendment IS NOT NULL) AS has_amendment,
       -- amendment stored as JSON (§3); wrap defensively so this works whether the column
       -- is JSON or STRUCT. Validated in scripts/validate_storage_sql.py.
       json_extract_string(to_json(amendment), '$.fee_model')                  AS amd_fee_model,
       TRY_CAST(json_extract(to_json(amendment), '$.fee_fixed_eur_mwh') AS DOUBLE) AS amd_fee_fixed_eur_mwh,
       TRY_CAST(json_extract(to_json(amendment), '$.fee_share_pct')     AS DOUBLE) AS amd_fee_share_pct
FROM raw.contracts;

-- stg.feedin_qh: kWh -> MWh, parse naive timestamp; grain = asset x quarter-hour
CREATE OR REPLACE TABLE stg.feedin_qh AS
SELECT asset_id,
       CAST(timestamp AS TIMESTAMP) AS ts,           -- naive; no tz shift (no DST in Jan)
       CAST(timestamp AS DATE)      AS date,
       EXTRACT(hour FROM CAST(timestamp AS TIMESTAMP)) AS hour,
       energy_kwh / 1000.0          AS energy_mwh,    -- the one kWh->MWh conversion
       quality_flag, source_system
FROM raw.feedin;
-- UNIQUE (asset_id, ts)

-- stg.feedin_hourly: join grain with schedule + prices
CREATE OR REPLACE TABLE stg.feedin_hourly AS
SELECT asset_id, date, hour, SUM(energy_mwh) AS actual_mwh,
       COUNT(*) AS n_quarters,
       BOOL_OR(quality_flag <> 'validated') AS has_nonvalidated
FROM stg.feedin_qh GROUP BY asset_id, date, hour;

-- stg.feedin_monthly: report column total_feedin_mwh
CREATE OR REPLACE TABLE stg.feedin_monthly AS
SELECT asset_id, SUM(energy_mwh) AS total_feedin_mwh
FROM stg.feedin_qh GROUP BY asset_id;

-- stg.schedule: typed
CREATE OR REPLACE TABLE stg.schedule AS
SELECT asset_id, CAST(date AS DATE) AS date, hour, scheduled_mwh FROM raw.schedule;

-- stg.prices_hourly: pivot DA/ID onto one row per (date,hour)
CREATE OR REPLACE TABLE stg.prices_hourly AS
SELECT CAST(date AS DATE) AS date, hour,
       MAX(price_eur_mwh) FILTER (WHERE product='DA') AS da_price_eur_mwh,
       MAX(price_eur_mwh) FILTER (WHERE product='ID') AS id_price_eur_mwh,
       MAX(id_volume_mwh)                              AS id_volume_mwh  -- portfolio-level; not allocated
FROM raw.prices GROUP BY 1,2;

-- stg.eeg_premium: keep EEG technology domain (wind_onshore/solar)
CREATE OR REPLACE TABLE stg.eeg_premium AS
SELECT technology, premium_eur_mwh, correction_flag, correction_amount_eur FROM raw.eeg_premium;

-- stg.costs: typed passthrough
CREATE OR REPLACE TABLE stg.costs AS
SELECT asset_id, cost_category, allocated_amount_eur, allocation_basis,
       total_pool_eur, active_assets_in_pool FROM raw.costs;
```

Technology mapping (asset `wind`↔EEG `wind_onshore`, `solar`↔`solar`) is applied when joining
`stg.contracts`/`stg.assets` to `stg.eeg_premium`.

---

## 5. Reconstruction layer (`recon.*`)

Rebuilds each report column from `stg`, then diffs against `raw.report_*`. Computed in full
precision (`DOUBLE`); rounding is applied only in the delta views (§6).

```sql
-- recon.asset_hour: intraday-delta gross revenue per asset-hour
CREATE OR REPLACE TABLE recon.asset_hour AS
SELECT f.asset_id, f.date, f.hour,
       f.actual_mwh,
       s.scheduled_mwh,                              -- NULL where schedule row is absent
       p.da_price_eur_mwh, p.id_price_eur_mwh,
       COALESCE(s.scheduled_mwh,0) * p.da_price_eur_mwh
         + (f.actual_mwh - COALESCE(s.scheduled_mwh,0)) * p.id_price_eur_mwh
         AS gross_revenue_eur
FROM stg.feedin_hourly f
LEFT JOIN stg.schedule s USING (asset_id, date, hour)   -- LEFT: schedule is not a full grid
LEFT JOIN stg.prices_hourly p USING (date, hour);
-- WHERE the schedule row is missing is now KNOWN, not random: it is exactly the 12 inactive
-- assets' hours AFTER their contract_end (verified — 835 active × 744 + Σ inactive
-- active-days×24 = 626,472 to the row; feed-in stays full-month while schedule stops on the
-- contract-end day). So this null-rule IS the inactive-boundary settlement question, and it
-- is the exact thing SOL-0032 (contract_end 2025-01-26) is traced for in Phase 0. Candidate
-- rules to diff against the report:
--   (a) COALESCE(schedule,0) -> the whole post-contract-end volume settles at ID price (as coded);
--   (b) no-schedule => settle that volume at DA price instead;
--   (c) drop post-contract-end hours entirely (asset off management after contract_end).
-- Do NOT freeze this until a non-deviating and an inactive asset both reconcile in Phase 0.

-- recon.asset: assemble every report column (formulas confirmed/hypothesized in PLAN.md)
--   total_feedin_mwh      <- stg.feedin_monthly
--   gross_revenue_eur     <- SUM(recon.asset_hour.gross_revenue_eur)
--   eeg_premium_eur       <- total_feedin_mwh * premium_eur_mwh (mapped technology)
--   management_fee_eur     <- fee model, time-split at amendment_effective within the month
--   allocated_costs_eur    <- SUM over cost categories (respect allocation_basis + exclusions)
--   eeg_correction_eur     <- allocation of portfolio correction (base to be verified)
--   deckungsbeitrag_eur    <- gross_revenue + eeg_correction - management_fee - allocated_costs
--   db_per_mwh             <- deckungsbeitrag_eur / total_feedin_mwh
--   availability_pct       <- definition TBD from sources (flag as open question)
```

`recon.asset` is intentionally left as documented column-by-column derivations rather than a
single frozen query: the exact management-fee split, EEG-correction allocation base, and
`availability_pct` definition are the open business rules to be pinned in Phase 0. Each is a
named CTE so it can be diffed independently.

---

## 6. Delta / reconciliation views

The comparison boundary — the **only** place rounding is applied.

```sql
-- round to the report's published precision; MODE must be confirmed empirically in Phase 0
-- (half-up vs banker's). Provide one function and use it everywhere:
--   round_eur(x)  -> 2 dp ;  round_mwh(x) -> 3 dp ;  round_rate(x) -> 2 dp
CREATE OR REPLACE TABLE recon.asset_delta AS
SELECT a.asset_id,
       round_eur(a.gross_revenue_eur)  AS gross_calc, r.gross_revenue_eur AS gross_rep,
       round_eur(a.gross_revenue_eur) - r.gross_revenue_eur AS gross_delta,
       -- ... one triple (calc, rep, delta) per column ...
       round_eur(a.deckungsbeitrag_eur) - r.deckungsbeitrag_eur AS db_delta
FROM recon.asset a JOIN raw.report_asset r USING (asset_id);

-- recon.region_delta: SUM(recon.asset) by region vs raw.report_region
-- recon.portfolio_delta: SUM(recon.asset) vs raw.report_portfolio
```

A deviation is a **non-rounding, systematic** pattern in these deltas (see PLAN.md deviation
strategy). The delta views are grouped/filtered by cohort (technology, region, fee_model,
has_amendment, is_inactive, source_system, cost_category) to isolate the rule behind each.

---

## 7. Typing and precision policy

- **Energy:** `DOUBLE`. kWh→MWh conversion happens exactly once in `stg.feedin_qh`.
- **Money (compute):** `DOUBLE` for reconstruction; sufficient given values ≪ 2^53 and full
  precision retained until the compare boundary.
- **Money (compare):** round with the empirically confirmed rule at the delta boundary only.
  If float noise near `.005` ever makes a match ambiguous, recompute the affected column in
  `DECIMAL(18,6)` — do not sprinkle intermediate rounding, which would hide real deviations.
- **Dates:** parsed to `DATE`/`TIMESTAMP` in `stg` only; raw keeps strings.
- **Enums:** validated at load (see §9); stored as `VARCHAR`.

---

## 8. Rebuild contract

```
scripts/build_warehouse.py --db data/warehouse.duckdb --raw data/raw
```

Runs `sql/01_raw.sql` → `02_stg.sql` → `03_recon.sql` in order, each object
`CREATE OR REPLACE`. Idempotent and deterministic: dropping `warehouse.duckdb` and rerunning
against the same raw run reproduces byte-identical tables. `notebooks/` are exploratory only
and never feed `recon`.

---

## 9. Validation hooks (tests in `04_checks.sql`, asserted via pytest)

1. **Raw completeness:** `count(raw.feedin) = 2,520,672`; `raw.schedule = 626,472`;
   `raw.prices = 1,488`; `raw.costs = 3,388`; `raw.assets = raw.contracts = raw.report_asset = 847`.
2. **Grain uniqueness:** `stg.feedin_qh` unique on `(asset_id, ts)`;
   `stg.prices_hourly` unique on `(date, hour)`; `raw.report_asset` unique on `asset_id`.
3. **Feed-in grid:** every asset has exactly 2,976 quarter-hours (96 × 31) — active *and*
   inactive; flag exceptions.
3b. **Schedule grid:** schedule rows per asset == `24 × active_days_in_Jan` (744 for active;
   `24 × day_of_month(contract_end)` for the 12 inactive). Join `stg.schedule` to
   `stg.contracts`; assert every active asset has 744 and every inactive asset stops on its
   contract-end day. This makes the intraday-delta null-rule (§5) auditable rather than a
   silent LEFT-JOIN gap.
4. **Report internal consistency (independent of our formulas):**
   `report_asset.deckungsbeitrag_eur ≈ gross + eeg_correction − management_fee − allocated_costs`
   (confirmed live); region = Σ assets; portfolio = Σ regions. These catch load errors before
   reconciliation.
5. **Enum domains:** `quality_flag`, `source_system`, `fee_model`, `allocation_basis`,
   `product`, `region`, `technology` within their allowed sets (§ `plans/DATA_LOADING.md`).
6. **Null policy:** no nulls in keys/quantities; `fee_fixed_eur_mwh`/`fee_share_pct` nullable
   by fee model only.
7. **Reconciliation summary:** count of assets with `|db_delta| > tolerance`, grouped by
   cohort — the working surface for finding the three deviations.

---

## 10. Sizing

- `raw.feedin` 2.52M rows × 5 narrow columns ≈ tens of MB in DuckDB; full warehouse well
  under ~300 MB. Raw JSON on disk is a few hundred MB. Trivial for a single-file DuckDB; no
  partitioning or external engine needed — reinforcing the single-engine choice.

---

## 11. Storage API surface

```
src/quadra/storage/load_raw.py       # read_json(...) -> raw.* tables (idempotent)
sql/01_raw.sql 02_stg.sql 03_recon.sql 04_checks.sql
scripts/build_warehouse.py           # CLI orchestrator (§8)
src/quadra/storage/db.py             # connect(db_path), run_sql_file(...), query(...) helpers
tests/test_storage.py                # asserts §9 checks
```

Consumers (reconciliation, the DSF write-up) read `recon.*` and `raw.report_*` only; they
never touch `data/raw/` files directly — DuckDB is the query surface.
