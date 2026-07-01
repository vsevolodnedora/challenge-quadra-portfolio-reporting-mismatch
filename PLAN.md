# Challenge Plan

## Core Assertion

The right way to solve the challenge is to build an independent asset-level January 2025 reconciliation from the raw API sources, then compare every reconstructed component against `/report/monthly`. Do not start from portfolio totals or generic outlier hunting: the challenge states that exactly three deviations exist, each requires cross-referencing at least two sources, and early-stage errors can compound through downstream aggregation.

## Scope Rules

- Source of truth is the challenge API only; data is synthetic/fictitious. Do not use real market, EEG, company, or asset data.
- External knowledge is only useful for vocabulary and sanity checks: `Deckungsbeitrag`, EEG `Marktpraemie`, DA/ID settlement, and reporting concepts.
- There are exactly three deviations. Stop when there are three source-backed root causes with EUR impacts, not when there are many small rounding differences.
- Work at asset-level first, then aggregate to region and portfolio.
- Defaults are explicit choices. The final DSF spec must name every endpoint parameter and value.
- Paginate all full extracts. Never rely on first pages or default page sizes.
- Calculate with full precision and round only when comparing to report output.

## Data Extraction Plan

Fetch and cache all nine required source/report surfaces:

- `GET /assets?status=all&page_size=200`
- `GET /data/contracts?include_amendments=true&page_size=200`
- `GET /data/feedin?date_from=2025-01-01&date_to=2025-01-31&quality_filter=all&page_size=500`
- `GET /data/schedule?date_from=2025-01-01&date_to=2025-01-31&page_size=500`
- `GET /data/prices?date_from=2025-01-01&date_to=2025-01-31&product=all&page_size=500`
- `GET /data/eeg_premium?month=2025-01`
- `GET /data/costs?month=2025-01&page_size=200`
- `GET /data/costs/metadata?month=2025-01`
- `GET /report/monthly?month=2025-01&granularity=asset&page_size=100`
- `GET /report/monthly?month=2025-01&granularity=region`
- `GET /report/monthly?month=2025-01&granularity=portfolio`

Keep raw JSON or normalized tables so every reported deviation can be traced back to source rows.

## Normalization Assertions

- `/data/feedin.energy_kwh` is kWh; all other energy values are MWh.
- Feed-in timestamps are naive strings. Do not apply timezone conversion unless source data proves one is required.
- Aggregate quarter-hour feed-in to hourly before joining with schedules and hourly prices.
- Aggregate hourly feed-in to monthly for report columns.
- `/data/prices.id_volume_mwh` is portfolio-level intraday volume, not per asset. It must not be allocated to assets unless a source rule explicitly says so.
- Intraday delta is `actual_feed_in_mwh - scheduled_mwh`.
- Map asset technology `wind` to EEG technology `wind_onshore`; map `solar` to `solar`.
- `/assets?status=inactive` marks assets whose contract ended during January. These assets are high risk for active-day, fee, and allocation logic.

## Component Reconstruction

Reconstruct these columns independently for every asset:

- `total_feedin_mwh`
- `gross_revenue_eur`
- `eeg_premium_eur`
- `eeg_correction_eur`
- `management_fee_eur`
- `allocated_costs_eur`
- `deckungsbeitrag_eur`
- `db_per_mwh`
- `availability_pct`

Business-logic assertions to test against report rows:

- Gross revenue is likely `sum(scheduled_mwh * DA price_eur_mwh + (actual_mwh - scheduled_mwh) * ID price_eur_mwh)` by asset and hour.
- EEG premium is likely `total_feedin_mwh * premium_eur_mwh` by mapped technology.
- EEG correction is portfolio-level in `/data/eeg_premium.correction_amount_eur`; infer and verify the asset allocation rule instead of treating it as a per-asset input.
- Management fee logic lives in `/data/contracts`; inspect `fee_model`, `fee_fixed_eur_mwh`, `fee_share_pct`, `ppa`, amendments, `amendment_effective`, `contract_start`, and `contract_end`.
- Cost allocations must respect `/data/costs/metadata`, `assets_excluded`, `allocation_basis`, `total_pool_eur`, and `active_assets_in_pool`.
- The sample report row implies `deckungsbeitrag_eur = gross_revenue_eur + eeg_correction_eur - management_fee_eur - allocated_costs_eur`; it does not appear to include `eeg_premium_eur`. Verify this against actual report rows before finalizing the DSF spec.
- `db_per_mwh` should be derived from final `deckungsbeitrag_eur / total_feedin_mwh`, with report-compatible rounding.

## Deviation Search Strategy

First reconcile each component to the asset-level report. Then group non-rounding deltas by:

- technology
- region
- fee model
- amendment presence
- contract active/inactive status
- SCADA/source system
- operator/owner
- cost category
- date/hour

Expected high-risk deviation zones:

- Feed-in conversion or aggregation: kWh vs MWh, quarter-hour to hourly, timestamp handling, quality filter choice.
- Contract interpretation: amendments, effective dates, inactive assets, and fee model handling.
- Portfolio-level allocation: EEG correction, cost metadata exclusions, active-asset denominators, and capacity-weighted pools.

Each final deviation must include:

- Source rows or endpoints cross-referenced.
- A precise EUR impact.
- The root cause.
- The downstream report fields affected.

## DSF Deliverable Plan

After the three deviations are identified, write the final submission in exactly the seven required fields:

- `INTENT`
- `TRIGGER`
- `DATA INPUTS`
- `BUSINESS LOGIC`
- `GUARDRAILS`
- `OUTPUT`
- `SUCCESS CRITERIA`

The `DATA INPUTS` section should list exact API calls, parameters, page-size choices, and reasons. The `BUSINESS LOGIC` section should be specific enough for an agent to reproduce the corrected aggregation without asking questions.
