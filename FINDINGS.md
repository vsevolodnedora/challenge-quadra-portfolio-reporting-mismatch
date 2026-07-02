# FINDINGS — QUADRA January-2025 Deckungsbeitrag Reconciliation Ledger

A running, append-only record of what we have established about the challenge data and the
published report. It exists so that evidence accumulates rather than scatters: later work
**corroborates**, **refutes**, **refines**, or **supersedes** earlier entries by reference,
and the trail stays auditable.

The deliverable is a 7-field DSF spec + **exactly three** root-caused deviations (source, EUR
impact, root cause). This ledger is where candidates are held and narrowed to those three.

## How to use this ledger

- **Append, don't rewrite.** Add new findings with the next free `F-NNN`. When new evidence
  bears on an existing finding, add a dated bullet under its **Log** and update its **Status**
  — never silently edit away the earlier claim.
- **Every finding is falsifiable and sourced.** State it, then say exactly how it was
  established (endpoint + params + date, a build check in `recon.checks`, an arithmetic
  reconciliation, or specific source rows). "Verified live" means probed against
  `https://poc16264.quadra-energy.com/api/v1`.
- **Cross-reference by ID.** Use `corroborates F-00X` / `refutes F-00X` / `refines F-00X` /
  `depends on F-00X` so the graph of evidence is explicit.

### Status vocabulary

| Status | Meaning |
| --- | --- |
| `established` | Verified fact; treated as ground truth unless later refuted. |
| `candidate-deviation` | A source-vs-report tension that could be one of the three deviations. |
| `signal` | A measured delta/pattern that needs a root cause before it becomes a deviation. |
| `open-question` | A business rule that must be pinned (usually in Phase 0) before reconciliation is trustworthy. |
| `confirmed-deviation` | Root-caused, EUR-quantified, one of the final three. |
| `refuted` / `superseded` | Shown false, or replaced by a better finding (link to it). |

### Evidence provenance

All facts below were established on **2026-07-01** via: (a) throw-away live API probes;
(b) the full immutable pull (`data/raw`, feed-in 2,520,672 + schedule 626,472 rows, verified
to the row); and (c) the DuckDB warehouse build (`recon.checks`, `recon.asset_delta`). Numbers
are reproducible by rebuilding: `python scripts/build_warehouse.py`.

---

## Index

| ID | Finding | Type | Status |
| --- | --- | --- | --- |
| F-001 | API row counts (Jan 2025) | ground-truth | established |
| F-002 | Error taxonomy, rate-limit headers, out-of-window behavior | ground-truth | established |
| F-003 | `/data/prices` has no pagination | ground-truth | established |
| F-004 | Contracts carry a nested `amendment` object | ground-truth | established |
| F-005 | Deckungsbeitrag identity (EEG premium excluded) | ground-truth | established |
| F-006 | CHALLENGE.md sample JSON is illustrative, not data | ground-truth | established |
| F-007 | No schema drift; enums clean; no unexpected nulls | data-quality | established |
| F-008 | Feed-in is a complete grid for **all** 847 assets incl. inactive | structural | established |
| F-009 | Schedule incompleteness = the inactive contract-end boundary (reconciles to the row) | structural | established |
| F-010 | The 12 inactive assets: IDs, contract-end dates, north-wind cluster | structural | established |
| F-011 | Verified Phase-0 trace anchors | reference | established |
| F-012 | The published report is **internally** consistent | structural | established |
| F-013 | **DEVIATION 3** — inactive assets counted as active (`active_asset_count=847`; cost pools ÷847) | deviation | **confirmed** |
| F-014 | **DEVIATION 1** — EEG correction spread across all wind, should be north-wind only | deviation | **confirmed** |
| F-015 | Inactive assets' post-contract-end feed-in appears counted | candidate-deviation | **refuted** (F-023) |
| F-016 | Universal small active-asset feed-in surplus (validated-only hypothesis) | candidate-deviation | **refuted** (F-023) |
| F-017 | Gross-revenue deltas are entangled with the unpinned settlement rule | signal | **resolved** (F-024) |
| F-018 | Management-fee amendment time-split rule unpinned | open-question | **resolved** (F-029) |
| F-019 | EEG-correction allocation base unpinned | open-question | **resolved** (F-014) |
| F-020 | `availability_pct` definition unknown | open-question | **resolved** (F-030) |
| F-021 | Report rounding mode not yet replicated | open-question | **resolved** (F-028) |
| F-022 | Missing-schedule null-rule (settle ID / DA / drop) unpinned | open-question | **resolved** (F-023) |
| F-023 | Feed-in report rule: validated+estimated (exclude `raw`), truncate inactive at `contract_end` | reconstruction | established |
| F-024 | `gross_revenue_eur` = energy settlement **+** EEG premium (backbone unblocked) | reconstruction | established |
| F-025 | `eeg_premium_eur` = feed-in × premium rate (mapped tech) | reconstruction | established |
| F-026 | `management_fee_eur`: fixed = feed-in×rate; revenue_share = **gross(incl premium)**×pct | reconstruction | established |
| F-027 | `allocated_costs_eur` = Σ source cost rows; pools use the full **847-asset** basis | reconstruction | established |
| F-028 | The **entire** report reproduces to ±1 cent — deviations are rule-level, not parsing | reconstruction | established |
| F-029 | **DEVIATION 2** — amendment fee applied whole-month instead of time-split at `amendment_effective` | deviation | **confirmed** |
| F-030 | `availability_pct` = validated / (validated + estimated) × 100 (raw excluded both sides) | reconstruction | established |
| F-031 | DEVIATION 3 EUR impact corrected: redistributable **893.74 €** (not 1,827.73); `grid_fees` is `per_asset` and does not redistribute; binary vs pro-rata | refinement | established |
| F-032 | `db_per_mwh` = round(published_db / published_feedin, 2) — a derived column, not independent; `active_on` probe confirms 835 | reconstruction | established |
| F-033 | Independent audit pass: extraction/rebuild clean, no hidden ID-settlement trick; D1 within-north-wind asset split is an open assumption (±313.38 €) | audit | established |

---

## A. Ground truth — API & data

### F-001 — API row counts (January 2025)
- **Status:** established
- **Claim:** feed-in **2,520,672** (= 847×31×96); schedule **626,472**; prices **1,488**
  (DA 744 + ID 744); costs **3,388** (= 847×4 categories); contracts / assets / report-asset
  **847**; `status=inactive` assets **12**; eeg_premium **2**; report region **4**; report
  portfolio **1**.
- **Evidence:** `meta.total_records` per endpoint (live probe) and confirmed to the row by the
  full pull manifest (`data/raw/_manifest.jsonl`) and `recon.checks`.
- **Implication:** these are the completeness gates for extraction; any shortfall is a pipeline
  bug, not data. Corroborates F-008.

### F-002 — Error taxonomy, rate limiting, out-of-window
- **Status:** established
- **Claim:** `400` → `{"error":{"param","message"}}` (object); `401` → `{"error":"invalid
  api_key"}` (bare string); a date window outside January → HTTP `200` with `data:[]`,
  `total_records:0`. Rate-limit headers are real: `X-RateLimit-Remaining` (int),
  `X-RateLimit-Reset` (unix epoch); limit 120/min/key.
- **Evidence:** live edge probes.
- **Implication:** the loader's error parser handles both error shapes; out-of-window is a
  control, not an error.

### F-003 — `/data/prices` has no pagination
- **Status:** established
- **Claim:** `/data/prices` rejects `page`/`page_size` (returns 400); one call returns all rows
  for the chosen `product` (`all` → 1,488). Every other list endpoint paginates via
  `has_next_page`.
- **Evidence:** live probe (page_size param → 400; no-param call → 1,488).

### F-004 — Contracts carry a nested `amendment` object
- **Status:** established
- **Claim:** with `include_amendments=true` (default), a contract row adds top-level
  `amendment_id` / `amendment_effective` **and** a nested `amendment` payload of the changed
  fee terms. `fee_model` observed only `fixed` and `revenue_share`; `ppa` is a valid enum but
  absent in January.
- **Evidence:** live probe; e.g. SOL-0214 (see F-011).
- **Implication:** fee logic and its mid-month changes live here → drives F-018.

### F-005 — Deckungsbeitrag identity
- **Status:** established
- **Claim:** `deckungsbeitrag_eur = gross_revenue_eur + eeg_correction_eur − management_fee_eur
  − allocated_costs_eur`. `eeg_premium_eur` is reported but is **not** part of the DB total.
- **Evidence:** holds for all 847 report rows (`recon.checks: db_identity_violations = 0`).
- **Implication:** the backbone of reconstruction; the sign on `eeg_correction` is **+**.

### F-006 — CHALLENGE.md sample JSON is illustrative, not data
- **Status:** established
- **Claim:** the example rows in CHALLENGE.md do not match the live API — e.g. it shows
  `WND-0042` as region `west`, `fee_fixed_eur_mwh` `4.20`, contract ending `2027-12-31`, but
  the live asset is region `south`, `5.2`, ending `2028-12-23`.
- **Evidence:** live probe of WND-0042.
- **Implication:** never hard-code a number lifted from a CHALLENGE.md sample. Refines F-011.

### F-007 — No schema drift; enums clean; no unexpected nulls
- **Status:** established
- **Claim:** every endpoint's rows share one JSON key-shape (schema survey: 1 shape/endpoint,
  0 off-modal rows). All enum domains are within their documented sets (`quality_flag`,
  `source_system`, `fee_model`, `allocation_basis`, `region`, `technology`). No nulls in
  feed-in key/quantity fields. `costs.metadata.assets_excluded = []`.
- **Evidence:** `recon.checks` (all `enum_*`, `null_feedin_key_qty`, `costs_metadata_excluded`
  OK) + `recon.schema_survey`.
- **Implication:** the three deviations are **not** raw parsing/enum artifacts — they live in
  the aggregation logic. Narrows the search toward F-013…F-016.

---

## B. Ground truth — structural

### F-008 — Feed-in is a complete grid for all 847 assets, including inactive
- **Status:** established
- **Claim:** every asset has exactly **2,976** quarter-hours (96×31), inactive ones included;
  feed-in continues past an inactive asset's `contract_end`.
- **Evidence:** total 2,520,672 = 847×2,976 and 2,976 is the January maximum ⇒ mean = max ⇒
  all equal (deductive). Direct: SOL-0032 returns 2,976 with feed-in through Jan 31 though its
  contract ended 2025-01-26. `recon.checks: feedin_per_asset_2976 = 0` offenders.
- **Implication:** feed-in exists for periods the asset was no longer under contract → sets up
  F-015. Corroborates F-001.

### F-009 — Schedule incompleteness is the inactive contract-end boundary (exact)
- **Status:** established
- **Claim:** schedule is complete (744 rows) for all 835 active assets; each of the 12 inactive
  assets has schedule only through its `contract_end` day. The 3,696 missing rows are exactly
  the inactive post-contract-end hours.
- **Evidence:** arithmetic reconciles **to the row**: 835×744 + Σ(inactive active-days×24) =
  621,240 + 5,232 = **626,472** (= F-001); the 3,696 gap = 154 asset-days × 24. Direct:
  SOL-0032 schedule covers Jan 1–26 only. `recon.checks: schedule_vs_active_days = 0` offenders.
- **Implication:** "missing schedule" is not random — it is precisely where an inactive asset
  kept producing feed-in with nothing to settle against. Directly drives F-015, F-022.

### F-010 — The 12 inactive assets
- **Status:** established
- **Claim:** IDs and `contract_end` day-of-month: SOL-0032(26), SOL-0040(11), SOL-0059(28),
  SOL-0167(06), SOL-0232(09), WND-0011(09), WND-0047(12), WND-0175(25), WND-0389(22),
  WND-0395(15), WND-0475(27), WND-0565(28). Σ active-days = 218. Five are **north wind**
  (WND-0175/0389/0395/0475/0565).
- **Evidence:** live probe `GET /assets?status=inactive` + per-asset contracts.
- **Implication:** the north-wind cluster overlaps the EEG-correction cohort (F-014) — the
  "inactive" rule and the "EEG-correction" rule can confound and must be separated.

### F-011 — Verified Phase-0 trace anchors
- **Status:** established
- **Claim (all live-verified):**
  - `WND-0042` — wind/south, `fixed` 5.2 €/MWh, no amendment, active (clean baseline).
  - `SOL-0007` — solar/west, `revenue_share` 2.1 %, all four cost categories, active.
  - `SOL-0214` — solar/south, `fixed` 4.2 → `revenue_share` 1.2 % (`AMD-2025-002`, effective
    2025-01-15), active (amendment time-split).
  - `SOL-0032` — solar/north, `fixed` 4.0, **inactive** (2023-01-12 → 2025-01-26).
  - `WND-0004` — wind/north, `fixed`, active (EEG-correction anchor).
- **Evidence:** live probe.
- **Implication:** these five exercise every mechanic + all candidate-deviation cohorts.

### F-012 — The published report is internally consistent
- **Status:** established
- **Claim:** within the report, aggregation is self-consistent: the DB identity holds for all
  847 rows (F-005); region totals = Σ member-asset rows; portfolio totals = Σ region rows.
- **Evidence:** `recon.checks: db_identity_violations = 0`, `region_db_sum_matches_assets = 0`,
  `portfolio_db_sum_matches_regions = 0.0`.
- **Implication:** the three deviations are **source-vs-report**, not arithmetic mistakes in
  the report's own roll-up. An early-stage input error that compounds downstream is the model.

---

## C. Candidate deviations — converge to exactly three

> These are tensions, not yet confirmed deviations. The final answer is **exactly three**
> root causes; some candidates below may collapse into one, or be refuted, as rules are pinned.

### F-013 — DEVIATION 3: inactive assets counted as active
- **Status:** confirmed-deviation
- **Claim:** the portfolio report publishes `active_asset_count = 847`, but 12 assets are
  `status=inactive` (contract ended mid-January) ⇒ only 835 were active. The same "treat 847 as
  active" error propagates into **cost allocation**: all four cost pools carry
  `active_assets_in_pool = 847`, and the allocation math uses the full 847-asset basis (flat
  divisor `pool/847`, capacity-weighted denominator = Σ capacity over all 847). So the 12
  inactive assets absorb cost that should fall on the 835 active.
- **Evidence:** `active_count_vs_inactive` FAIL (847 vs 835); WND-0042 (500 kW) insurance
  `20.56 = 31400 × 500/Σcap_847` (not `/Σcap_835`); monitoring flat `12.50 = 10587.5/847`. The
  12 inactive bear **1,827.73 €** of costs. Cross-refs `assets.status` + `report.portfolio` +
  `costs.active_assets_in_pool` (≥2 sources ✓).
- **EUR impact:** headline count 847 vs 835 (zero €-impact on DB — it is a count field). Cost
  impact **corrected to 893.74 €** (see F-031); the earlier **1,827.73 €** was an over-statement.
  Compounds into `allocated_costs_eur` → `deckungsbeitrag_eur` for the affected assets, but nets
  to **0 at portfolio level** (the pools are fixed totals, only redistributed).
- **Log:**
  - 2026-07-02 — promoted candidate → confirmed. Pinned the 847-basis in the cost math (F-027).
  - 2026-07-03 — **corroborated at region granularity.** `report_region.asset_count` also counts
    inactive: east 187 (183 active), north 280 (274), south 210 (210), west 170 (168) → the 4+6+0+2
    = 12 inactive are counted as active at both region and portfolio level. Zero €-impact (count
    field) but broadens the source-vs-report count evidence.
  - 2026-07-03 — **EUR impact refined (F-031).** The 1,827.73 € was Σ of *all four* cost rows on
    the 12 inactive, but two of those components do not actually redistribute: `grid_fees` is
    `per_asset` (bespoke, 934.00 € — each asset's own fee, no ÷count). Only the flat + two
    capacity-weighted pools redistribute when 847→835: **893.74 €** (binary) or **315.41 €**
    (pro-rata by active-days). Corrected rule for the spec = binary ÷835 (matches the integer
    `active_asset_count`/`active_assets_in_pool` semantics). See F-031.
- **Related:** depends on F-010, F-027; refined by F-031; one of the challenge's three hinted
  deviations.

### F-014 — DEVIATION 1: EEG correction spread across all wind, should be north-wind only
- **Status:** confirmed-deviation
- **Claim:** `/data/eeg_premium` reports a −18,400 € `wind_onshore` correction; the
  `costs/metadata` note states it *"betrifft ausschließlich Windanlagen Region Nord"* (applies
  exclusively to north wind). The report instead spreads the −18,400 € across **all four
  regions' wind, weighted by installed capacity** (`corr(correction, capacity) = −1.0` exactly;
  equal within region). Correct allocation puts the full −18,400 € on north wind only.
- **Evidence:** report region correction: north −11,967.49, east −1,747.20, south −3,168.17,
  west −1,517.14 (Σ = −18,400; solar 0). Correct = north −18,400, others 0. Cross-refs
  `eeg_premium.correction_amount` + `costs/metadata.note` + `contracts.region`/`technology`
  (≥2 sources ✓).
- **EUR impact:** **6,432.51 €** of correction shifted off north (should be −18,400, reported
  −11,967.49) onto east/south/west wind. Portfolio `total_eeg_correction` net unchanged, but
  region totals and all 612 wind-asset `deckungsbeitrag_eur` are wrong.
- **Log:**
  - 2026-07-02 — pinned the report's basis (capacity-weighted across all wind, F-019 resolved);
    promoted candidate → confirmed.
- **Related:** north-wind cohort overlaps F-010; one of the challenge's three hinted deviations.

### F-015 — Inactive assets' post-contract-end feed-in appears counted — REFUTED
- **Status:** refuted (see F-023)
- **Original claim:** reconstructed `total_feedin_mwh` exceeded the report by ~10.6 MWh/inactive
  asset, hypothesised as a report deviation.
- **Refutation:** the report is **correct** — it truncates inactive feed-in at `contract_end`.
  Under the real rule (validated+estimated, truncated at `contract_end`, F-023) all **12/12**
  inactive assets reconcile to **exactly 0.000 MWh** delta. The ~10.6 MWh "surplus" was *our*
  reconstruction summing the full month past `contract_end` — a reconstruction bug, not a
  report deviation.
- **Evidence:** per-asset trunc-delta = 0.000 for SOL-0032/0040/0059/0167/0232, WND-0011/0047/
  0175/0389/0395/0475/0565.
- **Log:**
  - 2026-07-02 — refuted. This also resolves the F-022 null-rule (post-`contract_end` hours are
    dropped, not settled), so the missing-schedule question never affects report reconciliation.

### F-016 — Active-asset feed-in surplus (validated-only hypothesis) — REFUTED
- **Status:** refuted (see F-023)
- **Original claim:** the report counts only `quality_flag='validated'` feed-in.
- **Refutation:** the real rule is **exclude `raw` only** — i.e. count `validated` +
  `estimated`. Under it, **832/835** active assets match the report to the exact milli-MWh and
  the other 3 differ by ±0.001 (pure rounding). The validated-*only* hypothesis is decisively
  wrong: it swings the active total from +159 MWh surplus to **−866 MWh deficit** (validated =
  28,616 MWh; the missing `estimated` = 876.5 MWh is real report volume). There is **no**
  active-asset feed-in deviation.
- **Evidence:** WND-0042 report 14.032 = validated 13.588 + estimated 0.444; `raw` (160 MWh
  total, 0.56%) is excluded portfolio-wide.
- **Log:**
  - 2026-07-02 — refuted and superseded by F-023 (the correct feed-in rule).

---

## D. Signals — measured, not yet root-caused

### F-017 — Gross-revenue deltas entangled with the settlement rule — RESOLVED
- **Status:** resolved (see F-024)
- **Original claim:** gross deltas were non-zero everywhere and uninterpretable until the
  settlement/null rules were pinned.
- **Resolution:** the earlier ~10% gross gap was **not** a settlement issue — it was a missing
  term. `gross_revenue_eur = energy_settlement + eeg_premium_eur` (F-024). With that term added,
  gross reconciles for **847/847** assets to ±5 cents (627 exact). Gross is **not** a deviation.
- **Log:**
  - 2026-07-02 — resolved. The settlement formula (sched×DA + (actual−sched)×ID) was correct all
    along; the report simply folds the EEG market premium into the published "gross".

---

## E. Open questions — rules to pin (mostly Phase 0)

### F-018 — Management-fee amendment time-split — RESOLVED
- **Status:** resolved (see F-029)
- **Question:** for a mid-month `amendment_effective`, is `management_fee_eur` a time-weighted
  split of the before/after terms?
- **Answer:** it **should** be (fixed rate on pre-effective feed-in + new share on post-effective
  gross), but the **report does not do this** — it applies the amended fee model to the whole
  month. That gap is DEVIATION 2 (F-029).

### F-019 — EEG-correction allocation base — RESOLVED
- **Status:** resolved (see F-014)
- **Answer:** the report allocates the −18,400 € **capacity-weighted across all wind**
  (`corr(correction, capacity) = −1.0`). The correct base is **north-wind only** — the gap is
  DEVIATION 1 (F-014).

### F-020 — `availability_pct` definition — RESOLVED
- **Status:** resolved (see F-030)
- **Question:** no obvious source field maps to `availability_pct`; derive its definition from
  feed-in vs capacity/expected, validated against report rows. Does **not** feed
  `deckungsbeitrag_eur`, so it is out of the deviation critical path; still worth pinning for a
  complete DSF.
- **Answer:** it is a **data-quality ratio**, not a capacity/uptime metric:
  `availability_pct = 100 × validated_quarters / (validated_quarters + estimated_quarters)`
  (raw excluded from **both** numerator and denominator). **847/847 exact** (F-030). It is *not*
  a deviation and hides no fourth effect.

### F-021 — Report rounding mode — RESOLVED
- **Status:** resolved (see F-028)
- **Answer:** round **half-up** at the published precision (EUR 2dp, MWh 3dp, rates 2dp) applied
  at the output boundary reproduces the report to ±1 cent across all columns and all 847 assets.
  Residuals are sub-cent per-hour accumulation, never a deviation.

### F-022 — Missing-schedule null-rule — RESOLVED
- **Status:** resolved (see F-023)
- **Answer:** moot for reconciliation. The report **drops** inactive assets' post-`contract_end`
  hours entirely (F-023 truncation), so there is no post-contract feed-in left to settle and the
  ID/DA/drop choice never affects the report. (Within-contract hours all have schedule rows.)

---

## F. Reconstruction — the report, pinned column by column

> Phase-0 tracing (deferred in the first pass) done on 2026-07-02. Every column below was pinned
> against the live report to the cent, so the deviations are provably rule-level, not artifacts.

### F-023 — Feed-in report rule: exclude `raw`, truncate inactive at `contract_end`
- **Status:** established
- **Claim:** `total_feedin_mwh` = Σ energy where `quality_flag ∈ {validated, estimated}`
  (i.e. **exclude `raw`**), and for inactive assets only dates **≤ `contract_end`** are counted.
- **Evidence:** 832/835 active assets match to the exact milli-MWh (3 off by ±0.001 rounding);
  12/12 inactive match to 0.000 after truncation. Supersedes F-015 and F-016.
- **Implication:** the feed-in backbone reconciles; it is not a deviation. The quality filter is
  single-source and applied uniformly (so not one of the three cross-source deviations).

### F-024 — `gross_revenue_eur` = energy settlement + EEG premium
- **Status:** established
- **Claim:** `gross_revenue_eur = Σ_hour[ scheduled_mwh·DA + (actual_mwh − scheduled_mwh)·ID ]
  + total_feedin_mwh · premium_eur_mwh`. The published "gross" **folds in** the EEG market
  premium (the same €-amount also reported separately as `eeg_premium_eur`).
- **Evidence:** 847/847 assets within ±0.05 € (627 exact); WND-0042 1234.90 vs report 1234.91.
  Resolves F-017; the earlier ~10% gap was the missing premium term, not a settlement error.
- **Implication:** the DB identity (F-005) still holds because premium enters DB **once**, via
  gross; the standalone `eeg_premium_eur` column is informational.

### F-025 — `eeg_premium_eur` = feed-in × premium rate (mapped technology)
- **Status:** established
- **Claim:** `eeg_premium_eur = total_feedin_mwh × premium_eur_mwh`, mapping asset `wind` →
  `wind_onshore` (8.4), `solar` → `solar` (6.2). **847/847 exact.**

### F-026 — `management_fee_eur` formulas (base includes premium)
- **Status:** established
- **Claim:** `fixed` → `feed-in × fee_fixed_eur_mwh` (586/586 exact); `revenue_share` →
  `gross_revenue_eur × fee_share_pct/100` where the base is **gross including the EEG premium**
  (258/259 exact; SOL-0007 implies 2.1001 % on gross vs 2.2635 % on energy-only). Amended
  contracts are the exception → F-029.
- **Implication:** whether the fee base *should* include the premium is defensible either way,
  but the report's rule is pinned; only the amendment handling deviates.

### F-027 — `allocated_costs_eur` = Σ source cost rows; pools use the 847-asset basis
- **Status:** established
- **Claim:** report `allocated_costs_eur` = sum of the 4 source `costs` rows per asset
  (847/847 exact). The source allocation uses `active_assets_in_pool = 847` throughout: flat
  `pool/847` (monitoring 12.50), capacity-weighted denominator = Σ capacity over all 847
  (insurance/data_fees), per-asset (grid_fees). This 847-basis is the compounding path of
  DEVIATION 3 (F-013).

### F-028 — The entire report reproduces to ±1 cent
- **Status:** established
- **Claim:** with F-023…F-027 plus half-up rounding (F-021), every report column
  (`total_feedin_mwh`, `gross_revenue_eur`, `eeg_premium_eur`, `management_fee_eur`,
  `allocated_costs_eur`) reproduces to ±1 cent for ~all 847 assets.
- **Implication:** the three deviations are **rule-level choices**, not parsing/enum/rounding
  artifacts — exactly F-014, F-029, F-013.

### F-029 — DEVIATION 2: amendment fee applied whole-month, not time-split
- **Status:** confirmed-deviation
- **Claim:** for the 2 amended contracts (both flip `fixed`→`revenue_share` effective
  2025-01-15), the report applies the **new** fee model to the **whole month**, ignoring
  `amendment_effective`. Correct is a time-split: fixed rate on pre-effective feed-in + new
  share on post-effective gross.
- **Evidence:** SOL-0214 report 96.53 = whole-month 1.2 % on total gross (correct time-split
  239.03; report −142.50). WND-0089 report 26,440.14 = whole-month 12.0 % (correct 19,278.21;
  report **+7,161.93**). Cross-refs `contracts.amendment_effective`/`amd_*` + feed-in + prices.
- **EUR impact:** net fee **+7,019.43 €** over-charged (⇒ portfolio DB understated by the same);
  dominated by WND-0089, whose amended 12.0 % share is itself an eye-catching outlier.
- **Related:** resolves F-018; one of the challenge's three hinted deviations ("fee logic lives
  in contracts").

### F-030 — `availability_pct` = validated / (validated + estimated) × 100
- **Status:** established
- **Claim:** the report's `availability_pct` is a **feed-in data-quality ratio**, not a capacity
  factor or uptime metric: for each asset, `100 × (# quarter-hours flagged `validated`) / (# flagged
  `validated` + # flagged `estimated`)`. `raw` quarter-hours are excluded from **both** sides;
  quarter-hours past `contract_end` are excluded (same truncation as F-023).
- **Evidence:** **847/847 exact** to 2 dp. Competing candidates fail: `validated/total` (incl. raw)
  0/847 (mean |Δ| 0.48); `(validated+estimated)/total` 0/847 (mean |Δ| 2.51); capacity factor and
  producing-quarter fraction are ~20–40 %, not the observed 95.79–98.00 % band.
- **Implication:** the last un-reconstructed report column is now pinned → the whole report is
  accounted for and **no fourth systematic effect is hiding** in `availability_pct`. It carries
  **no EUR impact** (does not enter `deckungsbeitrag_eur`), so it is not one of the three
  deviations; it belongs in the DSF `BUSINESS LOGIC`/`OUTPUT` for completeness. Resolves F-020.

### F-031 — DEVIATION 3 EUR impact corrected: 893.74 €, not 1,827.73 €
- **Status:** established (refines F-013)
- **Claim:** the earlier headline "1,827.73 € of cost on the 12 inactive" over-stated the impact by
  conflating three mechanically different pieces. Decomposed by the cost source's own
  `allocation_basis`:

  | pool | basis | on 12 inactive (report ÷847) | redistributes 847→835? |
  | --- | --- | --- | --- |
  | monitoring | `flat` | 150.00 € | yes (pool/835) |
  | insurance | `capacity_weighted` | 470.84 € | yes (Σcap over 835) |
  | data_fees | `capacity_weighted` | 272.90 € | yes (Σcap over 835) |
  | **subtotal redistributable** | | **893.74 €** | **yes** |
  | grid_fees | `per_asset` | 934.00 € | **no** — bespoke per-asset fee (4 distinct values, corr(fee,cap)=0.57), not a ÷count; removing an inactive asset moves nothing onto anyone |

  So the true redistributive impact of the 847-vs-835 denominator is **893.74 €** (binary
  exclusion), verified to the cent as the extra cost loaded onto the 835 active
  (north +467.99, south +180.31, east +128.10, west +117.34). Under an alternative **pro-rata by
  active-days** rule the shift is only **315.41 €** (inactive keep 578.33 € for the days they were
  active). Portfolio total `allocated_costs_eur` is **unchanged** either way (fixed pools).
- **Corrected rule chosen for the spec:** **binary ÷835.** `active_assets_in_pool` and
  `active_asset_count` are **integer counts**; their natural corrected value is 835, which is only
  expressible by binary exclusion (a fractional pro-rata count is not what the field represents),
  and the challenge frames status as binary active/inactive ("contract ended during January").
  Pro-rata is recorded as the economically-defensible alternative (315.41 €) so the spec can state
  the assumption explicitly.
- **Evidence:** `stg.costs` (basis/pool/`active_assets_in_pool`=847 per category), `stg.assets`
  (12 inactive), `stg.contracts` (capacity, `contract_end`). Recomputed in
  `.venv` DuckDB queries 2026-07-03; grid_fees distinct-value + capacity-correlation check confirms
  `per_asset` is not a divisible pool.
- **Implication:** DEVIATION 3 stands, but the DSF must quote **893.74 €** (binary), exclude
  `grid_fees` from the redistribution, and note the pro-rata alternative. Do **not** cite 1,827.73 €.

### F-032 — `db_per_mwh` is a derived column: round(published_db / published_feedin, 2)
- **Status:** established
- **Claim:** the report's `db_per_mwh` is **not** an independent computation — it equals
  `round(deckungsbeitrag_eur / total_feedin_mwh, 2)` using the report's **own already-rounded**
  published DB and feed-in. Tested three bases: raw ratio → 764/847 exact; round(db,2)/round(feedin,3)
  → 835/847; **published_db / published_feedin → 847/847 exact.** So it carries no information beyond
  the two columns it derives from and cannot host a deviation.
- **Evidence:** the three-way match test above; reconstruction from our rounded DB/feed-in matches
  835/847 with the other 12 at exactly ±0.01 (last-digit ratio rounding), gated by `recon_db_per_mwh`
  (tol 0.011) in `sql/04_checks.sql`.
- **Implication:** closes the gap the audit flagged — `db_per_mwh` (and `availability_pct`, F-030)
  are now reconstructed and gated, so **all nine** published asset columns are accounted for, not
  just the DB total. Corrects the earlier over-claim that all nine were "gated" (only DB was).

### F-033 — Independent audit pass; no hidden extraction/settlement trick; one open D1 sub-assumption
- **Status:** established
- **Claim:** an independent audit found no evidence the result is an artifact of extraction loss,
  DB truncation, staging field loss, or a partial build. Reproduced here:
  - **No hidden ID-settlement trick:** **0** in-contract feed-in hours lack a schedule row, so the
    `COALESCE(scheduled_mwh,0)` in settlement never fires within contract (no volume silently settled
    at ID). Contracts are strictly 1:1 with assets (0 multi-contract, 0 orphan).
  - **Clean rebuild reproduces:** rebuilding the warehouse from raw pages passes all gates and all
    9 per-column recon checks (only `active_count_vs_inactive` fails = DEVIATION 3, as designed).
  - **D3 corroborated:** `/data/contracts?active_on` steps 847 (01-01) → 846 (01-09) → 842 (01-15)
    → 835 (01-31) → 835 (02-01); `/assets?status=active` = 835. Month-end/default active = 835,
    supporting binary exclusion.
- **Open sub-assumption (D1):** the within-north-wind **asset-level** correction split is not
  determined by data. Current spec uses capacity over **all** north wind (incl. the 5 inactive
  north-wind assets). Excluding inactive would move **≈313.38 €** among north-wind assets **without**
  changing the region total (−18,400 €). Flagged in `DSF_SPEC.md` step 7; analogous to D3's
  binary/pro-rata choice. Neither this nor D3's exact rule is resolvable without a "correct" report.
- **Evidence:** live `active_on` probe; `recon.asset_hour` missing-schedule count = 0; clean rebuild
  of `data/warehouse.duckdb` and a scratch DB both green.
- **Process note (self-critical):** the audit fairly observed the exploration order was suboptimal —
  the first pass bulk-pulled and hunted candidates before pinning trace rules, which produced the
  since-refuted F-015/F-016 and the over-counted 1,827.73 €. The *final technical path* (raw-first,
  immutable pages, DuckDB staging, independent reconstruction, clean rebuild) is sound; the lesson
  is trace-then-bulk, already the documented Phase-0/Phase-1 plan.

---

## Changelog

- **2026-07-01** — Ledger created. F-001…F-022 logged from live probes, the full immutable pull
  (verified to the row), and the first full warehouse build. Report confirmed internally
  consistent (F-012); four candidate deviations open (F-013…F-016) to be narrowed to three;
  five business rules (F-018…F-022) to pin in Phase 0.
- **2026-07-02** — Did the deferred Phase-0 tracing. **Refuted F-015 and F-016** as
  reconstruction artifacts; **resolved F-017…F-019, F-021, F-022**; pinned every report column
  (F-023…F-028, report reproduces to ±1 cent). **Confirmed the three deviations:** F-014 (EEG
  correction all-wind vs north-only, 6,432.51 €), F-029 (amendment fee whole-month vs
  time-split, +7,019.43 €), F-013 (inactive-as-active: `active_asset_count` 847 vs 835 and
  cost pools ÷847, 1,827.73 €).
- **2026-07-03** — Pressure-tested DEVIATION 3 and pinned the last column before spec-writing.
  **F-031:** corrected DEVIATION 3's EUR impact from 1,827.73 € to **893.74 €** (binary ÷835) —
  `grid_fees` is `per_asset` (934 €, does not redistribute) and pro-rata-by-active-days would be
  only 315.41 €; portfolio total is unchanged. **F-030:** pinned `availability_pct` =
  validated/(validated+estimated)×100 (847/847 exact), resolving F-020 and confirming no fourth
  systematic effect. All nine report columns are now reconstructed; the three deviations hold.
  **Full cross-validation pass:** extraction complete (manifest 6,811 responses / 1,703 partitions,
  0 Σrows≠total_records; all 11 raw tables match F-001), all build gates pass, reconstruction max
  |db_delta| 0.01 € (0 assets > 0.02 tol), amendment cohort confirmed = exactly 2. DEVIATION 3 also
  corroborated at region granularity (`report_region.asset_count` counts inactive). Nothing
  overlooked in extraction/storage/reconstruction → cleared to write the DSF spec.
- **2026-07-03 (audit)** — Independent audit + additional checks. Confirmed clean extraction/rebuild;
  **fixed three over-statements**: (1) `db_per_mwh`/`availability_pct` were not reconstructed or gated
  — now both are (F-032, F-030), so all 9 columns are gated per-column in `sql/04_checks.sql`; (2)
  `sql/03_recon.sql` D3 table rewritten from the stale 1,827.73 € to the decomposed **893.74 €** (binary,
  grid_fees excluded); (3) `DSF_SPEC.md` intro corrected (7 independent + 2 derived columns, not "9
  gated"). Added `active_on` corroboration (835) and the D1 within-north-wind open assumption
  (±313.38 €). No hidden ID-settlement trick (0 in-contract missing-schedule hours). See F-032, F-033.
