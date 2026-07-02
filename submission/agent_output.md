AGENT OUTPUT — RECONSTRUCTION RESULT AND THE THREE DEVIATIONS

Method: full immutable pull of all nine endpoints (6,811 API responses; feed-in 2,520,672 rows,
schedule 626,472, prices 1,488, costs 3,388, contracts/assets/report-asset 847 each — every
surface verified row-for-row against `meta.total_records`), loaded into a DuckDB warehouse, then
one asset traced end-to-end (a clean fixed-fee wind asset, a revenue-share solar asset, an
amended contract, an inactive asset, a north-wind correction asset) to pin each aggregation rule
before generalizing to all 847.

RECONSTRUCTION FIDELITY

The published report was reproduced column-by-column before any correction was applied, so the
deviations are provably rule-level choices, not parsing or rounding artifacts:

- `total_feedin_mwh`: rule = sum `validated`+`estimated` quarter-hours (exclude `raw`),
  truncate inactive assets at `contract_end`, kWh/1000. 832/835 active assets exact to the
  milli-MWh (3 at ±0.001 rounding); 12/12 inactive exact after truncation.
- `gross_revenue_eur` = Σ_hour[sched×DA + (actual−sched)×ID] + EEG premium: 847/847 within
  ±0.05 € (627 exact). Zero in-contract hours lack a schedule row, so no volume is silently
  settled at ID.
- `eeg_premium_eur` = feed-in × rate (wind 8.40, solar 6.20): 847/847 exact.
- `management_fee_eur`: fixed = feed-in × rate (586/586 exact); revenue_share = gross incl.
  premium × pct (258/259 exact) — the only misses are the 2 amended contracts (= Deviation 2).
- `allocated_costs_eur` = Σ of the 4 source cost rows: 847/847 exact (the source's ÷847 basis
  is Deviation 3).
- `eeg_correction_eur`: report allocates −18,400 € capacity-weighted across ALL wind
  (corr(correction, capacity) = −1.0 exactly) — reproduced 847/847 (= Deviation 1).
- `availability_pct` = 100 × validated / (validated + estimated) quarter-hours (raw excluded
  both sides): 847/847 exact — a data-quality ratio, no EUR impact, hides no fourth effect.
- `db_per_mwh` = round(published_db / published_feedin, 2): 847/847 exact — a derived column,
  cannot host a deviation.
- Deckungsbeitrag identity `db = gross + correction − fee − costs` holds for all 847 published
  rows; region = Σ assets; portfolio = Σ regions. The report is internally consistent — all
  three deviations are source-vs-report, injected early and compounded downstream.

Rounding: half-up at the output boundary (EUR 2 dp, MWh 3 dp, rates 2 dp) reproduces the report
to ±1 cent across all columns.

RECONSTRUCTED PORTFOLIO NUMBERS (2025-01)

| field | published (wrong) | correct |
|---|---|---|
| total_gross_revenue_eur | 2,580,202.06 | 2,580,202.06 |
| total_eeg_premium_eur | 238,133.90 | 238,133.90 |
| total_eeg_correction_eur | −18,400.00 | −18,400.00 (all on north wind) |
| total_management_fee_eur | 124,556.26 | 117,536.83 (−7,019.43, D2) |
| total_allocated_costs_eur | 121,143.00 | 121,143.00 (redistributed, D3) |
| total_db_eur | 2,316,102.80 | 2,323,122.23 (+7,019.43) |
| db_margin_pct | 89.76 | 90.04 |
| active_asset_count | 847 | 835 (D3) |

THE THREE DEVIATIONS

D1 — EEG correction spread across all wind (should be north-wind only)
- Sources (≥2): `/data/eeg_premium` (`correction_amount_eur = −18,400 €`, `wind_onshore`,
  `correction_flag=true`) × `/data/costs/metadata.note` ("ausschließlich Windanlagen Region
  Nord") × `/data/contracts` (region, technology, capacity).
- EUR impact: 6,432.51 € shifted off north wind onto east/south/west wind. Reported region
  correction north −11,967.49 / east −1,747.20 / south −3,168.17 / west −1,517.14; correct
  north −18,400 / others 0. Portfolio total unchanged (−18,400); region totals and all 612
  wind-asset `deckungsbeitrag_eur` wrong.
- Root cause: the correction was allocated capacity-weighted across ALL wind
  (corr(reported_correction, capacity) = −1.0), ignoring the metadata note that scopes it to
  north wind. A cross-source rule (the free-text note) was not applied.

D2 — Amended management fee applied whole-month (should be time-split)
- Sources (≥2): `/data/contracts` (`amendment_id`, `amendment_effective=2025-01-15`, nested
  `amendment.fee_share_pct`) × `/data/feedin` + `/data/prices` (activity before/after the
  effective date).
- EUR impact: net +7,019.43 € over-charged (portfolio DB understated by the same — the only
  deviation that moves the portfolio total). WND-0089 reported 26,440.14 vs. correct 19,278.21
  (+7,161.93); SOL-0214 reported 96.53 vs. correct 239.03 (−142.50). Both flip `fixed 4.2` →
  `revenue_share` (12.0 % / 1.2 %) effective 2025-01-15.
- Root cause: the report applied the amended `revenue_share` model to the entire month instead
  of time-splitting at `amendment_effective` (fixed rate on pre-15th feed-in, new share on
  post-15th gross). Dominated by WND-0089, whose 12.0 % share is itself an outlier.

D3 — 12 inactive assets counted as active (count + cost denominator)
- Sources (≥2): `/assets` (`status=inactive` → 12) × `/report/monthly` (`active_asset_count=847`,
  region `asset_count` includes inactive) × `/data/costs` (`active_assets_in_pool=847`) +
  `/data/contracts` (`contract_end` within January). Corroborated by
  `/data/contracts?active_on=2025-01-31` → 835 (847 on 01-01, stepping down through the month):
  the API's own active-contract semantics put the month-end active count at 835.
- EUR impact: `active_asset_count` 847 vs 835 (a count error, 0 € on DB). Cost redistribution:
  893.74 € of pooled cost sits on the 12 inactive that should fall on the 835 active —
  monitoring 150.00 (flat), insurance 470.84 + data_fees 272.90 (capacity_weighted); onto
  active by region north +467.99 / south +180.31 / east +128.10 / west +117.34. Portfolio total
  unchanged (pools are fixed totals). `grid_fees` (`per_asset`, 934.00 € on inactive) is a
  bespoke per-asset fee, not a pooled ÷count — it does NOT redistribute and is excluded from
  the impact. Alternative pro-rata-by-active-days rule → 315.41 € (assumption flagged in the
  spec, BUSINESS LOGIC step 6).
- Root cause: the 12 assets whose contracts ended during January were still counted as active —
  in the headline `active_asset_count` / region `asset_count`, and in the flat and
  capacity-weighted cost denominators (`active_assets_in_pool=847`). An early "active count"
  error that compounds into every asset's `allocated_costs_eur` → `deckungsbeitrag_eur`.
  (`costs/metadata.assets_excluded=[]` documents the erroneous "excluded nobody" choice rather
  than justifying it.)

CANDIDATES TESTED AND REJECTED (why it is exactly these three)

- "Inactive assets' post-contract-end feed-in is counted" — refuted: the report correctly
  truncates at `contract_end`; all 12 inactive assets reconcile to a 0.000 MWh delta.
- "Report counts only validated feed-in" — refuted: the rule is exclude-`raw`-only; validated-only
  would leave a −866 MWh deficit (the 876.5 MWh of `estimated` is real report volume).
- Gross-revenue gap — resolved: the published "gross" folds in the EEG market premium; with that
  term the settlement formula reconciles 847/847. Not a deviation.
- `availability_pct` and `db_per_mwh` — both fully reconstructed (847/847 exact), so no fourth
  systematic effect is hiding in the two remaining columns.

ASSUMPTIONS SURFACED (not resolvable from the data; flagged in the spec rather than silently chosen)

- D1 asset-level split within north wind: capacity-weighted over all north-wind assets
  (including the 5 inactive ones, which earned premium pre-`contract_end`). Excluding them
  would move ≈313.38 € among north-wind assets without changing the −18,400 € region total.
- D3 corrected denominator: binary exclusion (÷835, matching the integer count semantics) vs.
  pro-rata by active-days (893.74 € vs. 315.41 € redistributed); portfolio total unchanged
  either way.
- Run schedule (TRIGGER): anchored to the observable `generated_at = 2025-02-03T08:14:22Z`
  hint; the upstream-feeds-final conditions dominate.
