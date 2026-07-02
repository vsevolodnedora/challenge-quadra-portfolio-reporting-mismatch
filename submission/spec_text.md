INTENT

Produce the correct monthly Deckungsbeitrag (contribution-margin) report for the 847-asset
NEBELGARD renewable portfolio at asset, region, and portfolio granularity — reconciled from raw
source data — to drive investor reporting, management-fee invoicing, and P&L.

TRIGGER

Runs once per reporting month `M`, after `M` has closed AND all three upstream feeds for `M`
are final:

1. the grid-operator EEG statement for `M` is published (`/data/eeg_premium` returns `M` with any
   `correction_flag`/`correction_amount_eur` settled),
2. finance's cost allocation for `M` is finalized (`/data/costs` + `/data/costs/metadata` for `M`),
3. feed-in for the full month is delivered (`/data/feedin` returns the complete quarter-hour grid).

Schedule: 09:00 Europe/Berlin on the 1st business day of month `M+1`, gated on the three
conditions above (if any feed is not final, hold and re-check hourly; escalate to a human at
EOD). *Assumption — the source data does not pin the run time.* The only observable hint is the
challenge documentation's example `generated_at = 2025-02-03T08:14:22Z` (Monday 2025-02-03
09:14 Europe/Berlin — the 1st business day after January closed, consistent with the upstream
feeds being final: cost allocation dated 2025-01-31, ÜNB-Mitteilung dated 2025-01-28); the
schedule is anchored to that hint and the conditions dominate in any conflict. For this
deliverable, `M = 2025-01`, `date_from = 2025-01-01`, `date_to = 2025-01-31`. API `generated_at`
timestamps are UTC; the trigger time is Europe/Berlin. Do not run for a partial month, and do not
re-run silently if a feed is later restated — a restatement is a new, versioned run.

DATA INPUTS

Base URL `https://poc16264.quadra-energy.com/api/v1`; header `X-API-Key: <key>` on every call.
Every parameter is an explicit choice, defaults included. `page_size` is set to each endpoint's
documented maximum to minimize requests. Paginate every list endpoint to completion and assert
`Σ(rows) == meta.total_records` (see GUARDRAILS). D1/D2/D3 mark where an input feeds one of the
three identified deviations; the deviations themselves (source, EUR impact, root cause) are
detailed in the agent output.

| # | Call (exact) | Params & why | Paginate | Expected rows (2025-01) |
|---|---|---|---|---|
| 1 | `GET /assets` | `status=all` — need inactive assets too; `status` drives the active/inactive split (D3). `page_size=200` (max). | yes | 847 (12 `inactive`) |
| 2 | `GET /data/contracts` | `include_amendments=true` (default) — the nested `amendment` object carries the mid-month fee change (D2). `page_size=200` (max). No `active_on` filter: we need all 847 contracts, and the active/inactive decision is made from `/assets.status` + `contract_end`, not by asking the API to pre-filter. | yes | 847 |
| 3 | `GET /data/feedin` | `date_from=2025-01-01`, `date_to=2025-01-31`, `quality_filter=all` (default) — pull every row and filter quality in-process (the `validated`/`estimated`/`raw` mix is needed for both energy and `availability_pct`; filtering at the API would discard evidence). `page_size=500` (max). Chunk by `asset_id` (one asset = 2,976 rows = 6 pages) to keep page offsets shallow and give a per-asset completeness gate. `energy_kwh` is in kWh. | yes | 2,520,672 (= 847 × 2,976) |
| 4 | `GET /data/schedule` | `date_from=2025-01-01`, `date_to=2025-01-31`. `page_size=500` (max), chunk by `asset_id`. Structurally incomplete by design: inactive assets have no schedule rows after `contract_end`. | yes | 626,472 |
| 5 | `GET /data/prices` | `date_from=2025-01-01`, `date_to=2025-01-31`, `product=all` — one call returns DA + ID. This endpoint rejects `page`/`page_size` (HTTP 400); do not send them. | no | 1,488 (DA 744 + ID 744) |
| 6 | `GET /data/eeg_premium` | `month=2025-01`. No `technology` filter — need both rows. `correction_amount_eur` is portfolio-level (D1). | no | 2 (`wind_onshore`, `solar`) |
| 7 | `GET /data/costs` | `month=2025-01`. `page_size=200` (max). One row per asset × 4 categories; carries `allocation_basis`, `total_pool_eur`, `active_assets_in_pool` (D3). | yes | 3,388 (= 847 × 4) |
| 8 | `GET /data/costs/metadata` | `month=2025-01`. Carries `assets_excluded` and the free-text `note` that scopes the EEG correction (D1). | no | 1 |
| 9 | `GET /report/monthly` | `month=2025-01`, `granularity=asset`, `page_size=100` (max). The published output to reconcile/replace. | yes | 847 |
| 10 | `GET /report/monthly` | `month=2025-01`, `granularity=region`. | no | 4 |
| 11 | `GET /report/monthly` | `month=2025-01`, `granularity=portfolio`. | no | 1 |

Constants read from the API for 2025-01 (do NOT hard-code across months): premium rates
`wind_onshore = 8.40`, `solar = 6.20` €/MWh; EEG correction `wind_onshore = −18,400.00` €
(`correction_flag=true`); cost pools `monitoring 10,587.50` (flat), `insurance 31,400.00`
(capacity_weighted), `data_fees 18,200.00` (capacity_weighted), `grid_fees 60,955.50` (per_asset);
`assets_excluded = []`; the metadata `note` scopes the correction to *"ausschließlich Windanlagen
Region Nord"* (north-wind only). The process reads these from the API each run.

BUSINESS LOGIC

This is the CORRECT aggregation; where the published January-2025 report deviates from it, the
step points to the deviation by number (D1/D2/D3 — see agent output). Compute at full DOUBLE
precision; round only at the output boundary (see step 8). Technology map: asset `wind` → EEG
`wind_onshore`; asset `solar` → `solar`. All energy is MWh except `/data/feedin.energy_kwh`,
which is converted once: `energy_mwh = energy_kwh / 1000`.

Step 1 — Feed-in per asset-hour (report volume).
From `/data/feedin`, keep quarter-hours where `quality_flag ∈ {validated, estimated}` (exclude
`raw`). For each asset, keep only timestamps with `date ≤ contract_end` (active assets:
`contract_end` is after 2025-01-31, so this is a no-op; the 12 inactive assets are truncated at
their contract-end day). Sum quarter-hours to hourly `actual_mwh`, and to monthly
`total_feedin_mwh` (report column, 3 dp).

Step 2 — Energy settlement per asset-hour.
Join hourly `actual_mwh` to `/data/schedule.scheduled_mwh` (asset, date, hour) and to
`/data/prices` pivoted to `(date, hour) → {DA, ID}`. Every in-contract hour has a schedule row, so:
`energy_eur = scheduled_mwh × DA_price + (actual_mwh − scheduled_mwh) × ID_price`.
(Scheduled volume settles at day-ahead; the intraday imbalance settles at the intraday price.)

Step 3 — EEG market premium (per asset).
`eeg_premium_eur = total_feedin_mwh × premium_eur_mwh[mapped technology]`
(2025-01: wind 8.40, solar 6.20).

Step 4 — Gross revenue (per asset).
`gross_revenue_eur = Σ_hour(energy_eur) + eeg_premium_eur`. The published "gross" folds the EEG
market premium into revenue; the same amount is also reported standalone as `eeg_premium_eur`.

Step 5 — Management fee (per asset). Fee terms live in `/data/contracts`.
- If the contract has NO amendment:
  - `if fee_model == 'fixed'` → `management_fee_eur = total_feedin_mwh × fee_fixed_eur_mwh`
  - `if fee_model == 'revenue_share'` → `management_fee_eur = gross_revenue_eur × fee_share_pct / 100`
    (base is gross INCLUDING the EEG premium)
  - `if fee_model == 'ppa'` → not present in 2025-01; stop and ask (no rule pinned).
- If the contract HAS an amendment (`amendment_id` set, `amendment_effective` = mid-month): apply
  a time-split at `amendment_effective` — the pre-effective model on pre-effective activity plus
  the post-effective model on post-effective activity. For 2025-01 both amended contracts flip
  `fixed 4.2` → `revenue_share`, effective 2025-01-15:
  `management_fee_eur = (Σ feed-in for date < amendment_effective) × fee_fixed_eur_mwh`
  `                   + (Σ gross for date ≥ amendment_effective) × amendment.fee_share_pct / 100`.
  The published report skips this split and applies the new model to the whole month → D2.

Step 6 — Allocated costs (per asset). Sum the four `/data/costs` rows per asset, but compute
each pool against the correct active-asset basis, not the source `active_assets_in_pool=847`.
First determine the active set: an asset is active for the month iff `status == 'active'`
(equivalently `contract_end > 2025-01-31`); 2025-01 has 835 active, 12 inactive. Then per category:
- `flat` (monitoring): `allocated = total_pool_eur / N_active` for each active asset, `0` for inactive
  (2025-01: `10,587.50 / 835 = 12.68`; the source used `/847 = 12.50` → D3).
- `capacity_weighted` (insurance, data_fees): `allocated = total_pool_eur × capacity_i / Σ(capacity over active)`;
  inactive get `0` (source used `Σ over all 847` → D3).
- `per_asset` (grid_fees): use the asset's own `allocated_amount_eur` AS GIVEN — it is a bespoke
  per-asset fee, not a pooled ÷count, so it does NOT redistribute when the active set changes.
`allocated_costs_eur` = sum of the (corrected) four categories.
*Assumption (state it):* corrected allocation uses binary active/inactive exclusion, matching the
integer `active_assets_in_pool`/`active_asset_count` semantics. If finance intends pro-rata by
active-days instead, substitute weight `capacity_i × active_days_i/31` (flat: `active_days_i/31`);
that yields a smaller redistribution (see D3). Do not silently pick one — surface the assumption.

Step 7 — EEG correction (per asset) and Deckungsbeitrag.
The portfolio EEG correction (`/data/eeg_premium.correction_amount_eur`, 2025-01: −18,400 € for
`wind_onshore`) is allocated ONLY to north-wind assets, per the `costs/metadata` note
("ausschließlich Windanlagen Region Nord"), capacity-weighted within north wind:
`eeg_correction_eur = −18,400 × capacity_i / Σ(capacity over north-wind)` for north wind, else `0`.
The published report spreads it capacity-weighted across all wind in all regions → D1.
*Assumption (state it):* the within-north-wind base is capacity over ALL north-wind assets,
mirroring the mechanism the report itself uses. The region-level result (−18,400 € to north, 0
elsewhere) is robust. The asset-level split has two unresolvable-from-data sub-choices: (a)
capacity vs feed-in weighting, and (b) whether to include the 5 INACTIVE north-wind assets.
Including them (as here) is defensible — they earned premium on their pre-`contract_end` feed-in;
excluding them (consistent with D3) would move ≈313.38 € among north-wind assets WITHOUT
changing the region total. Surface this choice; do not treat the asset-level split as certain.
Then the contribution margin:
`deckungsbeitrag_eur = gross_revenue_eur + eeg_correction_eur − management_fee_eur − allocated_costs_eur`.
(`eeg_premium_eur` is reported but is NOT subtracted again — it already entered via gross.)
`db_per_mwh = deckungsbeitrag_eur / total_feedin_mwh`.

Step 8 — Availability, rounding, and roll-up.
`availability_pct = 100 × validated_quarters / (validated_quarters + estimated_quarters)` per asset
(a feed-in data-quality ratio; `raw` excluded from both sides; no EUR impact).
Round half-up at the output boundary only: EUR 2 dp, MWh 3 dp, `db_per_mwh`/`availability_pct`
/rates 2 dp. Region = Σ of member-asset rows; portfolio = Σ of region rows.
`active_asset_count = 835` (count of `status=active`; the published 847 is wrong → D3);
`db_margin_pct = 100 × total_db_eur / total_gross_revenue_eur`.

GUARDRAILS

The agent must NOT:
- filter feed-in at the API (`quality_filter=validated_only`) — pull `all` and filter in-process;
- send `page`/`page_size` to `/data/prices` (HTTP 400);
- trust `active_assets_in_pool`, `active_asset_count`, or region `asset_count` as the active basis —
  derive the active set from `/assets.status` (D3);
- allocate `id_volume_mwh` to assets — it is portfolio-level intraday volume, not per-asset;
- redistribute `per_asset` (grid_fees) costs when the active set changes;
- apply an amended fee model to the whole month (D2), or spread the EEG correction beyond north wind (D1);
- round mid-computation, or convert timestamps to another timezone (January has no German DST; feed-in
  timestamps are naive and used as-is);
- re-run for a partial month or hard-code the month's rates/pools/correction across other months.

Stop and ask a human when:
- any completeness gate fails (below);
- a `fee_model=='ppa'` contract appears (no pinned rule) or any enum is outside its documented set;
- `costs/metadata.assets_excluded` is non-empty (it changes the active/cost basis and must be reconciled
  against `/assets.status`);
- `correction_flag=true` but the metadata `note` does NOT scope the correction (the north-wind scope
  is what makes D1's target well-defined);
- more than one contract per asset is active in the month, or an amendment lacks `amendment_effective`.

Data-quality checks that must pass before aggregating:
- pagination complete per surface/chunk: `Σ(rows) == meta.total_records`;
- feed-in is a full grid: 2,520,672 rows total and exactly 2,976 quarter-hours per asset (all 847);
- schedule = 24 × active-days per asset (744 for active; `24 × contract_end_day` for inactive);
- prices: 744 hourly rows, every hour has both a DA and an ID price;
- no duplicate row keys; no nulls in feed-in `asset_id`/`timestamp`/`energy_kwh`, prices, or `scheduled_mwh`;
- internal identity holds on the published report used for reconciliation:
  `deckungsbeitrag == gross + eeg_correction − management_fee − allocated_costs` for every asset.

OUTPUT

A three-granularity Deckungsbeitrag report for month `M`, delivered to finance (fee invoicing),
investor reporting, and P&L — same schema as `/report/monthly` (asset: `gross_revenue_eur`,
`eeg_premium_eur`, `eeg_correction_eur`, `management_fee_eur`, `allocated_costs_eur`,
`deckungsbeitrag_eur`, `db_per_mwh`, `total_feedin_mwh`, `availability_pct`; region: those totals +
`asset_count`; portfolio: those totals + `total_gross_revenue_eur`, `total_db_eur`, `db_margin_pct`,
`active_asset_count`), plus a deviation note versus the published report.

Correct portfolio-level output for 2025-01 (unchanged vs. published where a deviation only
redistributes; D1 and D3 net to zero at portfolio, D2 lifts DB):

| field | published (wrong) | correct |
|---|---|---|
| `total_gross_revenue_eur` | 2,580,202.06 | 2,580,202.06 |
| `total_eeg_premium_eur` | 238,133.90 | 238,133.90 |
| `total_eeg_correction_eur` | −18,400.00 | −18,400.00 (all on north wind) |
| `total_management_fee_eur` | 124,556.26 | 117,536.83 (−7,019.43, D2) |
| `total_allocated_costs_eur` | 121,143.00 | 121,143.00 (redistributed, D3) |
| `total_db_eur` | 2,316,102.80 | 2,323,122.23 (+7,019.43) |
| `db_margin_pct` | 89.76 | 90.04 |
| `active_asset_count` | 847 | 835 (D3) |

At region/asset level, D1 corrects wind `eeg_correction_eur` in all four regions, D2 corrects the two
amended assets' fees, and D3 shifts 893.74 € of pooled cost off the 12 inactive onto the 835 active.

SUCCESS CRITERIA

- Given the complete 2025-01 raw feeds, when the process runs, then every non-deviating
  published column is reproduced exactly — `total_feedin_mwh` to ±0.001 MWh and `gross_revenue_eur`,
  `eeg_premium_eur`, and non-amended `management_fee_eur` to ±0.01 € for all 847 assets — confirming
  the aggregation is understood before corrections are applied.
- Given the EEG statement and `costs/metadata` note, when the correction is allocated, then
  the full −18,400 € lands on north-wind assets only (region: north −18,400; east/south/west 0) and the
  portfolio `total_eeg_correction_eur` stays −18,400 € (D1 redistributes, not creates).
- Given the two amended contracts (effective 2025-01-15), when the fee is time-split, then
  `total_management_fee_eur` falls by 7,019.43 € (WND-0089 −7,161.93, SOL-0214 +142.50) and
  `total_db_eur` rises by the same 7,019.43 € (D2).
- Given `/assets.status`, when costs are allocated on the 835 active assets, then
  `active_asset_count = 835`, no inactive asset carries flat/capacity-weighted pool cost, and the
  portfolio `total_allocated_costs_eur` is unchanged (D3 redistributes 893.74 € within the portfolio).
- Given all corrections, when rolled up, then the DB identity holds at asset, region, and
  portfolio granularity, and portfolio `total_db_eur = 2,323,122.23 €`.
