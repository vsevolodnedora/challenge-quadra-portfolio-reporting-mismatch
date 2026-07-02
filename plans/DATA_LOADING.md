# Data Loading System

Implementation-ready specification for authenticated extraction of the QUADRA challenge API
and persistence of immutable raw extracts. Companion to `plans/STORAGE.md` (which consumes
what this system produces). Follows the Design Principles in `README.md`: separate
extraction from storage, preserve immutable raw extracts, fail loudly on incomplete
pagination / schema drift / bad enums, and make everything reproducible from scripts.

All endpoint facts below were verified against the live API on 2026-07-01 (throw-away
probes: `scripts/probe_api.py`, `scripts/probe_api2.py`).

---

## 1. Responsibilities and boundaries

This system does exactly three things:

1. **Extract** every required API surface for January 2025, completely and verifiably.
2. **Persist** each HTTP response verbatim to an immutable raw store, with provenance.
3. **Hand off** to `plans/STORAGE.md` by loading raw JSON into the DuckDB `raw` schema.

It does **not** normalize units, parse timestamps, compute business logic, or reconcile.
Unit conversion (kWh→MWh), typing, and aggregation belong to the storage/normalization
layers so that lineage stays traceable to the byte-for-byte source.

---

## 2. Configuration

Load from `.env` via `python-dotenv` (never hard-code, never log values):

| Variable | Use |
| --- | --- |
| `QUADRA_API_KEY` | sent as the `X-API-Key` request header on every call |
| `QUADRA_ACCESS_CODE` | web login/submission only; **not** used by the loader |

Constants:

- `BASE_URL = "https://poc16264.quadra-energy.com/api/v1"`
- `REPORT_MONTH = "2025-01"`, `DATE_FROM = "2025-01-01"`, `DATE_TO = "2025-01-31"`
- Fail fast at startup if `QUADRA_API_KEY` is missing or empty.

---

## 3. HTTP client contract

A single `requests.Session` with `X-API-Key` set once.

- **Timeout:** 60 s per request.
- **Retries:** exponential backoff (e.g. 1s, 2s, 4s, 8s; max 5 attempts) on connection
  errors, timeouts, and `5xx`. Never retry `4xx` (they are deterministic — fix the call).
- **Rate limiting (120 req/min/key, 240/min/IP):** read `X-RateLimit-Remaining` and
  `X-RateLimit-Reset` (unix epoch seconds) from every response. When `Remaining` reaches a
  small floor (e.g. `<= 2`), sleep until `Reset + 1s`. If a `429` is ever returned, honor
  `Retry-After` if present, else sleep to `Reset`. A simple, safe default is a client-side
  cap of ~110 req/min via a token/pacing gate; the header-driven wait is the backstop.
- **Never** print the API key or full request URL with the key to logs.

### Error taxonomy (shapes confirmed live — they are not uniform)

| Status | Body shape | Meaning | Loader action |
| --- | --- | --- | --- |
| `200`, non-empty `data` | `{data:[...], meta:{...}}` | normal page | persist + paginate |
| `200`, `data:[]`, `total_records:0` | envelope | out-of-window / no rows | persist; treat as terminal (not an error) |
| `400` | `{"error":{"param":..,"message":..}}` | invalid parameter | **abort that surface**, surface `param`+`message` |
| `401` | `{"error":"invalid api_key"}` (bare string) | missing/unknown key | **abort run**, credential problem |
| `429` | (rate limit) | too many requests | wait per `Reset`/`Retry-After`, retry |
| `5xx` | varies | server error | retry with backoff |

Note the asymmetry: `400`'s `error` is an **object**, `401`'s `error` is a **string**. The
error parser must handle both.

---

## 4. Response envelope and pagination protocol

Every list endpoint returns:

```json
{ "data": [ ... ],
  "meta": { "page": 1, "page_size": 100, "total_records": N,
            "has_next_page": true, "generated_at": "..." } }
```

Pagination loop (for paginated endpoints only):

1. Request `page=1` with the chosen `page_size`.
2. Accumulate `data`; record `meta.total_records` from the first page.
3. Continue `page += 1` while `meta.has_next_page` is `true`.
4. **Completeness gate:** after the loop, assert `sum(len(data)) == total_records`.
   Mismatch → raise; do not silently proceed (silent truncation manufactures false
   deviations).

`generated_at` reflects server time at call, not the data window; do not use it as data.

---

## 5. Endpoint catalog (exact calls)

Expected counts are the live-probed truth for January 2025. `page_size` values are chosen at
each endpoint's documented maximum to minimize requests.

> **Caution — CHALLENGE.md sample rows are illustrative, not data.** The example JSON in
> CHALLENGE.md does not match the live API (e.g. it shows `WND-0042` as region `west`,
> `fee_fixed_eur_mwh` `4.20`, contract `2022-01-01..2027-12-31`, whereas the live asset is
> region `south`, `5.2`, ending `2028-12-23`). Treat only probed values as ground truth;
> never hard-code a number lifted from a CHALLENGE.md sample into reconciliation.

| # | Endpoint | Required params (exact) | Paginated? | page_size | Expected total | Row key | Notes / gotchas |
|---|----------|------------------------|-----------|-----------|----------------|---------|-----------------|
| 1 | `GET /assets` | `status=all` | yes | 200 (max) | 847 | `asset_id` | `status=inactive` → 12 (IDs enumerated in §8); keep `status` for inclusion logic |
| 2 | `GET /data/contracts` | `include_amendments=true` | yes | 200 (max) | 847 | `asset_id` (1 contract each in Jan) | adds nested `amendment` object; also pull `include_amendments=false` once to diff schema |
| 3 | `GET /data/feedin` | `date_from=2025-01-01`, `date_to=2025-01-31`, `quality_filter=all` | yes | 500 (max) | 2,520,672 | (`asset_id`,`timestamp`) | **chunk per `asset_id`** (see §6.1); kWh units |
| 4 | `GET /data/schedule` | `date_from`, `date_to` | yes | 500 (max) | 626,472 | (`asset_id`,`date`,`hour`) | **structured incompleteness, not random** — 626,472 = 835 active × 744 + Σ(inactive active-days × 24); the 3,696-row shortfall vs 847×31×24 is exactly the 12 inactive assets' post-contract-end hours (§6.2, verified to the row) |
| 5 | `GET /data/prices` | `date_from`, `date_to`, `product=all` | **NO** | — | 1,488 | (`date`,`hour`,`product`) | **rejects `page`/`page_size` with 400**; single call; DA 744 + ID 744 |
| 6 | `GET /data/eeg_premium` | `month=2025-01` | no | — | 2 | `technology` | `correction_amount_eur` is portfolio-level (wind_onshore = −18,400) |
| 7 | `GET /data/costs` | `month=2025-01` | yes | 200 (max) | 3,388 | (`asset_id`,`cost_category`) | 4 categories × 847 |
| 8 | `GET /data/costs/metadata` | `month=2025-01` | no | — | 1 | — | `assets_excluded` (currently `[]`); free-text `note` |
| 9 | `GET /report/monthly` (asset) | `month=2025-01`, `granularity=asset` | yes | 100 (max) | 847 | `asset_id` | the target output being reconstructed |
| 10 | `GET /report/monthly` (region) | `month=2025-01`, `granularity=region` | no (4 rows) | — | 4 | `region` | field is `asset_count` (not `active_asset_count`) |
| 11 | `GET /report/monthly` (portfolio) | `month=2025-01`, `granularity=portfolio` | no (1 row) | — | 1 | — | extra `total_*` fields incl. `total_eeg_correction_eur` |

**Every parameter is an explicit choice**, including defaults left unchanged. Record the
ones deliberately relied upon:

- `feedin.quality_filter=all` (default) — take all rows; the quality mix (`validated` /
  `estimated` / `raw`) is analyzed in normalization, not filtered at extraction.
- `contracts.include_amendments=true` (default) — needed to see the nested `amendment`.
- `prices.product=all` — one call returns both DA and ID.

---

## 6. Chunking strategies

### 6.1 Feed-in (the only heavy surface)

Do **not** deep-paginate a single 2.52M-row query (page offsets in the thousands are slow
and fragile). Instead **chunk by `asset_id`**:

- One asset for the full month = 31 × 96 = 2,976 rows = 6 pages at `page_size=500`.
- 847 assets × 6 pages ≈ **5,082 requests** for feed-in.
- Benefits: page offsets stay shallow (≤ 6), each asset is a natural cache key, the pull is
  resumable per asset, and it maps onto the "trace one asset" workflow.
- Per-asset completeness gate: assert **every** asset returns exactly 2,976 rows (96 × 31),
  including the 12 inactive ones. Feed-in is a complete physical grid independent of contract
  status — verified: `SOL-0032` returns 2,976 rows even though its contract ended 2025-01-26,
  with feed-in continuing past contract end. (Also provable: the full total 2,520,672 = 847 ×
  2,976 and 2,976 is the January maximum, so mean = max ⇒ every asset = 2,976.) An asset
  returning fewer is a real defect — investigate, do not pad.

The `asset_ids` (plural, comma-separated) parameter exists but is not used for the bulk pull
because batching assets deepens page offsets and complicates per-asset completeness checks.

### 6.2 Schedule

Chunk by `asset_id` as well (same cache-key and resume benefits). Schedule's incompleteness
is **structured, not random**, and the per-asset gate is therefore exact:

- **Active assets (835):** exactly 744 rows (24 × 31).
- **Inactive assets (12):** exactly `24 × active_days_in_Jan`, where `active_days` = the
  day-of-month of `contract_end` (all inactive contracts start well before January). Schedule
  stops on the contract-end day; feed-in does not. Verified on `SOL-0032` (`contract_end`
  2025-01-26): schedule covers Jan 1–26 only (no rows Jan 27–31), while feed-in is full-month.

Per-asset gate: `rows == 24 × active_days_in_Jan` (join to `/data/contracts` for the end
date). This closes to the row across the portfolio: 835 × 744 + Σ(inactive) = 626,472, and
the 3,696 missing rows are exactly the 154 inactive post-contract-end asset-days × 24. A
shortfall on an **active** asset is a real defect; the inactive shortfall is expected data and
must be preserved (do not fabricate zero rows). This missing-schedule boundary is the crux of
the intraday-delta null-rule — see `plans/STORAGE.md §5`.

### 6.3 Everything else

Single query (optionally paginated) is fine: contracts (847), costs (3,388), assets (847),
report-asset (847), prices (1,488), and the small metadata surfaces.

---

## 7. Raw persistence contract (immutable)

Every response is written verbatim before any parsing decision, under a gitignored
`data/raw/` tree (add `data/` to `.gitignore`). One file per HTTP response:

```
data/raw/<endpoint>/<partition>/page-<NNNN>.json
data/raw/_manifest.jsonl          # one line per response, provenance
```

Examples:

```
data/raw/assets/status=all/page-0001.json
data/raw/feedin/asset_id=WND-0042/page-0001.json ... page-0006.json
data/raw/prices/product=all/page-0001.json
data/raw/report_monthly/granularity=asset/page-0001.json
```

Each file stores the **full envelope** (`data` + `meta`) exactly as received. The manifest
line records provenance (never the key):

```json
{"endpoint":"/data/feedin","params":{"asset_id":"WND-0042","date_from":"2025-01-01",
 "date_to":"2025-01-31","quality_filter":"all","page":1,"page_size":500},
 "path":"data/raw/feedin/asset_id=WND-0042/page-0001.json",
 "http_status":200,"rows":500,"total_records":2976,
 "response_generated_at":"2026-07-01T11:21:07Z","fetched_at":"2026-07-01T11:21:07Z",
 "ratelimit_remaining":113}
```

Immutability rules: raw files are write-once; a re-extraction writes to a new dated run
directory (`data/raw/` may be namespaced `data/raw/run=YYYYMMDD/...`) rather than
overwriting. Reconciliation must always be rebuildable from a raw run alone.

---

## 8. Extraction sequencing

Mirror `PLAN.md`:

- **Phase 0 — trace.** Extract all endpoints for a deliberately chosen 5-asset set (a few
  dozen requests) that together exercise every mechanic and all three deviation-candidate
  zones. All attributes below are **live-verified** (2026-07-01):

  | Asset | tech / region | fee | status | Why in the trace |
  |---|---|---|---|---|
  | `WND-0042` | wind / south | `fixed` 5.2 €/MWh, no amendment | active | clean baseline: gross revenue, EEG premium, fixed fee |
  | `SOL-0007` | solar / west | `revenue_share` 2.1 %, no amendment | active | all four cost categories + the `revenue_share` fee model |
  | `SOL-0214` | solar / south | `fixed` 4.2 → `revenue_share` 1.2 % (`AMD-2025-002`, effective 2025-01-15) | active | mid-month amendment fee time-split |
  | `SOL-0032` | solar / north | `fixed` 4.0, no amendment | **inactive** (contract 2023-01-12 → 2025-01-26) | inclusion / active-day logic **and** the missing-schedule null-rule (feed-in full-month, schedule stops Jan 26) |
  | `WND-0004` | wind / north | `fixed`, no amendment | active | EEG-correction allocation — the −18,400 € correction is flagged wind/Region Nord; this is the only clean active wind-north anchor to reverse-engineer its per-asset spread |

  The full inactive cohort (all 12, for the whole-cohort inclusion check in Phase 1) is:
  `SOL-0032` (end 01-26), `SOL-0040` (01-11), `SOL-0059` (01-28), `SOL-0167` (01-06),
  `SOL-0232` (01-09), `WND-0011` (01-09), `WND-0047` (01-12), `WND-0175` (01-25),
  `WND-0389` (01-22), `WND-0395` (01-15), `WND-0475` (01-27), `WND-0565` (01-28). Note the
  north-wind concentration (`WND-0175/0389/0395/0475/0565`), which overlaps the EEG-correction
  cohort — a place where two candidate rules can confound and must be separated.

  Lock formulas + rounding on this set before spending the feed-in budget.
- **Phase 1 — full pull.** Extract all surfaces completely (table §5), feed-in and schedule
  chunked per asset (§6). Resumable: skip an `(endpoint, partition)` whose pages already
  exist and pass their completeness gate.

---

## 9. Validation and guardrails (fail loudly)

Before handing raw to storage, assert:

1. **Credentials present** — abort if `QUADRA_API_KEY` missing.
2. **Pagination complete** — per surface and per chunk, `sum(rows) == total_records`.
3. **Feed-in grid** — `sum(feedin rows) == 2,520,672`; per asset == 2,976 (every asset,
   active or inactive).
3b. **Schedule grid** — `sum(schedule rows) == 626,472`; per asset == `24 × active_days_in_Jan`
   (744 for active, `24 × contract_end_day` for inactive). Any active asset with < 744 is a
   defect; the inactive shortfall is expected (§6.2).
4. **No duplicate keys** — unique on each endpoint's row key (§5).
5. **Enum sanity** — `quality_flag ∈ {validated,estimated,raw}`,
   `source_system ∈ {SCADA_v3,SCADA_legacy,manual_entry}`,
   `fee_model ∈ {fixed,revenue_share,ppa}`,
   `allocation_basis ∈ {capacity_weighted,flat,per_asset}`,
   `product ∈ {DA,ID}`, `region ∈ {north,south,east,west}`. Unknown value → raise
   (schema-drift guard; optionally enforced with Pydantic models at ingest).
6. **No unexpected nulls** in keys/quantities (asset_id, timestamp, energy_kwh, price,
   scheduled_mwh); document any intentional nullable (`fee_fixed_eur_mwh` /
   `fee_share_pct` are model-dependent).
7. **Out-of-window sanity** — a February probe returns `total_records:0`; use as a control
   that the date window is applied.

A failed guardrail stops the pipeline with a specific message; it never degrades silently.

---

## 10. Hand-off to storage

After Phase 1 passes validation, load `data/raw/**/*.json` into the DuckDB `raw` schema
(one table per endpoint) using DuckDB's native JSON reader, e.g.:

```sql
CREATE OR REPLACE TABLE raw.feedin AS
SELECT unnest(data, recursive := true) AS row, meta, filename
FROM read_json('data/raw/feedin/**/page-*.json', filename := true);
```

Exact table shapes, typing, and provenance columns are defined in `plans/STORAGE.md §3`.
The loader's job ends at "raw JSON is on disk and loaded verbatim"; storage owns typing.

---

## 11. Budget and runtime

At a safe ~110 req/min:

| Surface | Requests | Notes |
| --- | --- | --- |
| feed-in | ~5,082 | dominant cost (847 assets × 6 pages) |
| schedule | ~850–1,700 | 1–2 pages/asset (≤ 744 rows/asset; fewer where grid is sparse) |
| all others | < 60 | contracts, costs, prices, eeg, assets, report, metadata |

Total ≈ 6,000–6,900 requests ≈ **55–65 min** one-time for the full pull; Phase 0 is minutes.
Because raw is cached immutably, this cost is paid once.

---

## 12. Module / CLI surface

Suggested (illustrative) shape — one extraction module, thin CLI:

```
src/quadra/loading/client.py     # Session, get_json(path, params) -> envelope; rate-limit + retry + error taxonomy
src/quadra/loading/paginate.py   # iter_pages(path, params, page_size) -> yields pages; completeness gate
src/quadra/loading/endpoints.py  # one typed function per endpoint from §5 (exact params baked in)
src/quadra/loading/raw_store.py  # write_response(...), append_manifest(...), resume checks
src/quadra/loading/extract.py    # Phase 0 / Phase 1 orchestration + validation (§9)
scripts/extract.py               # CLI: --phase {trace,full} [--asset-id ...] [--run YYYYMMDD]
```

CLI examples:

```bash
python scripts/extract.py --phase trace                     # Phase 0: WND-0042, SOL-0007, SOL-0214, SOL-0032, WND-0004
python scripts/extract.py --phase full                      # Phase 1: complete pull, resumable
python scripts/extract.py --phase full --only feedin        # re-pull one surface
```

`scripts/probe_api.py` and `scripts/probe_api2.py` are throw-away discovery tools that
informed this spec and may be deleted; the durable extractor is `scripts/extract.py` + the
`src/quadra/loading` package.
