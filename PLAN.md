# Challenge Plan

## Status — 2026-07-02 (Phase 0 + reconstruction complete)

The reconstruction is done and the report reproduces to ±1 cent for all 847 assets
(`recon.asset_delta`, gated in `sql/04_checks.sql`). The three deviations are confirmed,
quantified in SQL (`recon.deviation_1_eeg`, `_2_fee`, `_3_inactive`) and root-caused in
`FINDINGS.md` (F-013, F-014, F-029):

1. **EEG correction** spread capacity-weighted across **all wind**; the `costs/metadata` note
   restricts it to **north wind only** → **6,432.51 €** shifted off north (F-014).
2. **Amendment management fee** applied the new fee model to the **whole month** instead of
   time-splitting at `amendment_effective` → net **+7,019.43 €** over-charged (F-029).
3. **Inactive-as-active**: `active_asset_count=847` (truth 835) and all cost pools divide by
   847, so 12 inactive assets absorb **1,827.73 €** of costs (F-013).

Pinned rules (F-023…F-028): feed-in excludes `raw` and truncates inactive at `contract_end`;
`gross_revenue = energy settlement + eeg_premium`; fee base includes the premium; rounding is
half-up at output. Two earlier candidates were **refuted** (F-015 inactive feed-in, F-016
validated-only) — the report was correct on both. **Remaining:** write the 7-field DSF spec;
optionally pin `availability_pct` (F-020, not on the DB critical path).

## Core Assertion

The right way to solve the challenge is to build an independent asset-level January 2025
reconciliation from the raw API sources, then compare every reconstructed component against
`/report/monthly`. Do not start from portfolio totals or generic outlier hunting: the
challenge states that exactly three deviations exist, each requires cross-referencing at
least two sources, and an early-stage error can compound through downstream aggregation.

## Deliverable

A seven-field DSF spec (`INTENT`, `TRIGGER`, `DATA INPUTS`, `BUSINESS LOGIC`, `GUARDRAILS`,
`OUTPUT`, `SUCCESS CRITERIA`) plus the three deviations, each with **source rows**,
**EUR impact**, and **root cause**. This is a forensic-reconstruction-and-spec task, not a
production pipeline. The technology exists to make the reconstruction correct, auditable,
and reproducible — nothing more.

## Technology Decision (why one engine)

- **DuckDB SQL is the single transform engine.** It ingests the raw JSON, stores typed
  tables, and expresses every join/aggregation as SQL. At this scale (feed-in is ~2.5M
  rows) DuckDB is more than fast enough, so a second DataFrame engine adds only duplicated
  logic and blurred lineage — which contradicts the repo's own principle to "avoid
  obscuring lineage by mixing engines." SQL queries also transcribe almost directly into
  the DSF `BUSINESS LOGIC` section.
- Stack: `duckdb`, `requests`, `python-dotenv`, `pytest`; `pydantic` optional (ingest
  schema/enum-drift guard); `ipykernel` optional (exploratory tracing only). Polars,
  pandas, and pyarrow are intentionally dropped (see `requirements.txt`).
- The extraction and storage systems are specified in detail in:
  - **`plans/DATA_LOADING.md`** — authenticated extraction, pagination, rate limiting,
    raw persistence, validation.
  - **`plans/STORAGE.md`** — DuckDB layout, raw/normalized/reconciliation layers, typing,
    precision, provenance, rebuild contract.

## Scope Rules

- Source of truth is the challenge API only; data is synthetic/fictitious. Do not use real
  market, EEG, company, or asset data.
- External knowledge is only for vocabulary and sanity checks: `Deckungsbeitrag`, EEG
  `Marktpraemie`, DA/ID settlement, and reporting concepts.
- There are exactly three deviations. Stop when there are three source-backed root causes
  with EUR impacts, not when there are many small rounding differences.
- Work at asset level first, then aggregate to region and portfolio.
- Defaults are explicit choices. The final DSF spec must name every endpoint parameter and
  value, including defaults left unchanged.
- Paginate all full extracts to completion; assert `sum(rows) == meta.total_records`. Never
  rely on first pages or default page sizes.
- Calculate with full precision and round only when comparing to report output.

## Sequencing: Trace One Asset, Then Bulk-Pull

The single biggest efficiency decision is **not** to pull all 2.52M feed-in rows before
understanding anything. Two phases:

### Phase 0 — Trace (cheap, a few dozen requests)

Pull all endpoints for a small, deliberately chosen asset set and reconstruct their report
rows exactly:

Five assets, all attributes **live-verified** 2026-07-01 (see `plans/DATA_LOADING.md §8` for
the table and the full inactive cohort):

- `WND-0042` — wind/south, `fixed` 5.2 €/MWh, no amendment (clean baseline).
- `SOL-0007` — solar/west, `revenue_share` 2.1 %, has all four cost categories (covers the
  revenue-share fee model **and** cost allocation).
- `SOL-0214` — solar/south, mid-month **amendment** `fixed` 4.2 → `revenue_share` 1.2 %
  (`AMD-2025-002`, effective `2025-01-15`); exercises fee time-splitting.
- `SOL-0032` — solar/north, `fixed` 4.0, **inactive** (contract `2023-01-12 → 2025-01-26`);
  exercises active-day / inclusion logic **and** the missing-schedule null-rule (feed-in runs
  the full month, schedule stops on the contract-end day).
- `WND-0004` — wind/north, `fixed`, active, no amendment; the clean anchor for the
  EEG-correction allocation (the −18,400 € correction is flagged wind/Region Nord).

Lock down, against real rows: the gross-revenue formula, the management-fee formula
(including amendment split), the cost-allocation denominators, the EEG-correction
allocation, and — critically — the report's **exact rounding** (see below). Only proceed to
Phase 1 once a non-deviating asset reconciles to the cent.

### Phase 1 — Bulk pull (one-time, cached)

Extract every source in full per `plans/DATA_LOADING.md`, persist raw immutably, load into
DuckDB, and reconstruct all 847 assets. Feed-in dominates: ~5,040 requests at page_size=500,
≈ 43 min at the 120 req/min limit. Cache to disk so all reconciliation reruns offline.

## Probed API Facts That Shape the Plan

Confirmed against the live API on 2026-07-01 (these correct/extend CHALLENGE.md):

- **Row counts (Jan 2025):** feed-in **2,520,672** (= 847×31×96, complete grid); schedule
  **626,472** (< full 630,168 → some asset-days have no schedule row); prices **1,488**;
  costs **3,388** (= 847 × 4 categories); contracts/assets/report-asset **847**; assets
  `status=inactive` **12**; eeg 2; report region 4; report portfolio 1.
- **`/data/prices` has no `page`/`page_size`** — one unpaginated call per product. Every
  other list endpoint paginates via `has_next_page`.
- **Rate-limit headers are real:** `X-RateLimit-Remaining` (int), `X-RateLimit-Reset` (unix
  epoch). Error shapes differ: 400 → `{"error":{"param","message"}}`; 401 → `{"error":"invalid api_key"}`;
  out-of-window date → HTTP 200 with empty `data` and `total_records:0`.
- **Contracts carry a nested `amendment` object** when `include_amendments=true` (top-level
  `amendment_id`/`amendment_effective` plus an `amendment` payload of the changed fee
  terms). `fee_model` observed only `fixed` and `revenue_share`; `ppa` is a valid enum but
  absent in January data.
- **Schedule incompleteness is structured, not random (verified):** feed-in is a complete
  2,976-row grid for every asset (including the 12 inactive), but schedule stops on each
  inactive asset's `contract_end` day. The 3,696 missing schedule rows are exactly those 154
  inactive post-contract-end asset-days × 24, reconciling to 626,472 to the row. This is both
  a completeness gate and a prime deviation site (see Deviation Search Strategy).
- **CHALLENGE.md sample JSON is illustrative, not data.** Live `WND-0042` is wind/**south**,
  `fee_fixed_eur_mwh` **5.2**, contract ending **2028-12-23** — none of which match the
  CHALLENGE.md example (west / 4.20 / 2027-12-31). Only probed values are ground truth.

## Normalization Assertions

- `/data/feedin.energy_kwh` is kWh; all other energy values are MWh. Convert feed-in to MWh
  (÷1000) exactly once, at the normalization boundary.
- Feed-in timestamps are naive strings (`2025-01-07T00:00:00`). Do not apply timezone
  conversion unless source data proves one is required. January has no DST change in
  Germany, so all days have 24 clean hours.
- Aggregate quarter-hour feed-in to hourly before joining schedules and hourly prices;
  aggregate hourly to monthly for report columns.
- `/data/prices.id_volume_mwh` is portfolio-level intraday volume, not per asset. Do not
  allocate it to assets unless a source rule explicitly says so.
- Intraday delta is `actual_feed_in_mwh - scheduled_mwh` per asset-hour. Missing schedule
  rows must be handled explicitly (LEFT JOIN, decide the null rule), given schedule is not a
  complete grid.
- Map asset technology `wind` → EEG technology `wind_onshore`; `solar` → `solar`.
- `/assets?status=inactive` marks assets whose contract ended during January. These assets
  are high risk for active-day, fee, and allocation logic.

## Rounding Rigor (a real deviation vs. a rounding artifact)

The report is published at fixed decimals (EUR to cents; `db_per_mwh` to 2 dp;
`total_feedin_mwh` to 3 dp). Before any delta is called a deviation, the report's rounding
must be **replicated exactly** — decimals per column, rounding mode (half-up vs.
banker's/half-even), and whether rounding is applied per step or only at output. Infer this
by matching many *non-deviating* assets to the cent; a rule that reproduces them is the rule
to use everywhere. Compute in full precision; apply the inferred rounding only at the
compare boundary. Without this, near-`.005` values generate false positives.

## Component Reconstruction

Reconstruct these columns independently for every asset and diff against the report:

- `total_feedin_mwh`, `gross_revenue_eur`, `eeg_premium_eur`, `eeg_correction_eur`,
  `management_fee_eur`, `allocated_costs_eur`, `deckungsbeitrag_eur`, `db_per_mwh`,
  `availability_pct`.

Business-logic assertions to test against report rows:

- **Deckungsbeitrag identity — confirmed on live data** at asset (`SOL-0001`), region
  (`north`), and portfolio granularity:
  `deckungsbeitrag_eur = gross_revenue_eur + eeg_correction_eur − management_fee_eur − allocated_costs_eur`.
  `eeg_premium_eur` is reported but **not** part of the DB total. Treat this identity as the
  backbone and verify it holds per asset.
- Gross revenue is likely
  `sum(scheduled_mwh * DA price + (actual_mwh − scheduled_mwh) * ID price)` by asset-hour.
- EEG premium is likely `total_feedin_mwh * premium_eur_mwh` by mapped technology.
- EEG correction is portfolio-level in `/data/eeg_premium.correction_amount_eur`
  (−18,400 EUR for `wind_onshore`); infer and verify the per-asset/region allocation rule
  rather than treating it as a per-asset input.
- Management-fee logic lives in `/data/contracts`: `fee_model`, `fee_fixed_eur_mwh`,
  `fee_share_pct`, and the nested `amendment` with `amendment_effective` — fees may need a
  before/after-effective time split within the month.
- Cost allocations must respect `/data/costs/metadata` (`assets_excluded`, currently `[]`),
  `allocation_basis` (`capacity_weighted`/`flat`/`per_asset`), `total_pool_eur`, and
  `active_assets_in_pool`.
- `db_per_mwh` is derived from final `deckungsbeitrag_eur / total_feedin_mwh` with
  report-compatible rounding.

## Deviation Search Strategy

The three deviations are **systematic rules**, not random per-asset noise ("comparing totals
and hunting outliers is expected to find noise"). Reconcile each component to the
asset-level report, then look for a *consistent* delta tied to one rule/cohort:

- technology / region / fee model / amendment presence / active-inactive status /
  SCADA source system / operator / cost category / date-hour.
- The "early error that compounds" is most likely an **input-stage** rule (feed-in
  conversion, a premium rate, an allocation denominator) that propagates downstream.

Concrete tension anchors observed while probing (candidate zones to test — not asserted
deviations):

- **Inactive inclusion:** `/assets` reports 12 inactive assets, yet portfolio
  `active_asset_count = 847`. Check whether inactive assets are wrongly included in totals,
  denominators, or fee/cost allocation.
- **EEG correction allocation:** portfolio correction is −18,400 EUR; the cost-metadata note
  says the correction "betrifft ausschliesslich Windanlagen Region Nord," yet the report
  spreads `eeg_correction_eur` across regions (north only −11,967.49). Verify the correct
  allocation base (wind-north only vs. broader).
- **Amendment time-split:** mid-month fee-model changes (e.g. `SOL-0214`) are prime
  management-fee error sites.
- **Schedule gaps = inactive contract-end boundary (verified):** the 3,696 missing schedule
  rows (626,472 vs 630,168) are *not* random — they are exactly the 12 inactive assets' hours
  after `contract_end` (835 active × 744 + Σ inactive active-days × 24 = 626,472, to the row).
  Feed-in, by contrast, stays a full 2,976/asset even after contract end. So an inactive asset
  keeps producing measured feed-in with no schedule to settle it against — the intraday-delta
  join must decide whether that post-contract volume settles at ID (COALESCE→0), at DA, or is
  dropped. This single fact links the "inactive inclusion" and "schedule gaps" zones; resolve
  it by tracing `SOL-0032` in Phase 0 (see `plans/STORAGE.md §5`).

Each final deviation must include the source rows/endpoints cross-referenced, a precise EUR
impact, the root cause, and the downstream report fields affected.

## DSF Deliverable Plan

After the three deviations are identified, write the submission in exactly the seven
required fields. `DATA INPUTS` must list exact API calls, parameters, page-size choices, and
reasons (defaults included). `BUSINESS LOGIC` must be specific enough for an agent to
reproduce the corrected aggregation without asking questions — ideally transcribed from the
DuckDB SQL that reconstructs the report.
