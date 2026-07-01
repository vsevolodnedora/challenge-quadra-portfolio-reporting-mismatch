-- 03_recon.sql — independent reconstruction of the January-2025 Deckungsbeitrag report from
-- `stg`, pinned column-by-column against the published report, then the three confirmed
-- deviations quantified. Computed at full DOUBLE precision; rounding (half-up, F-021) is applied
-- only at the comparison boundary.
--
-- All formulas below are pinned to the cent (see FINDINGS.md F-023…F-028):
--   total_feedin_mwh  = Σ energy where quality_flag IN (validated, estimated)  [exclude `raw`],
--                       counting only dates ≤ contract_end                     [truncate inactive]
--   gross_revenue_eur = Σ_hour[ sched·DA + (actual−sched)·ID ]  +  eeg_premium  [premium folded in]
--   eeg_premium_eur   = total_feedin_mwh × premium_rate (wind→wind_onshore, solar→solar)
--   management_fee    = fixed→feedin×rate ; revenue_share→gross×pct/100 (base incl. premium)
--   allocated_costs   = Σ of the 4 source cost rows
--   deckungsbeitrag   = gross + eeg_correction − management_fee − allocated_costs   (F-005)

CREATE SCHEMA IF NOT EXISTS recon;

CREATE OR REPLACE MACRO round_eur(x)  AS round(CAST(x AS DOUBLE), 2);
CREATE OR REPLACE MACRO round_mwh(x)  AS round(CAST(x AS DOUBLE), 3);
CREATE OR REPLACE MACRO round_rate(x) AS round(CAST(x AS DOUBLE), 2);

-- ---------------------------------------------------------------------------------------------
-- Report-faithful feed-in: exclude `raw`, truncate each asset at its contract_end (F-023).
-- Active assets have contract_end well beyond Jan 31, so the date filter is a no-op for them and
-- trims only the 12 inactive assets' post-contract hours.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE recon.feedin_hour AS
SELECT f.asset_id, f.date, f.hour,
       SUM(f.energy_mwh) FILTER (WHERE f.quality_flag IN ('validated','estimated')) AS actual_mwh
FROM stg.feedin_qh f
JOIN stg.contracts c USING (asset_id)
WHERE f.date <= c.contract_end
GROUP BY 1,2,3;

-- Energy settlement per asset-hour: scheduled at DA, imbalance at ID (F-024). Every in-contract
-- hour has a schedule row, so COALESCE is defensive only.
CREATE OR REPLACE TABLE recon.asset_hour AS
SELECT h.asset_id, h.date, h.hour, h.actual_mwh,
       s.scheduled_mwh, p.da_price_eur_mwh, p.id_price_eur_mwh,
       COALESCE(s.scheduled_mwh,0) * p.da_price_eur_mwh
         + (h.actual_mwh - COALESCE(s.scheduled_mwh,0)) * p.id_price_eur_mwh AS energy_eur
FROM recon.feedin_hour h
LEFT JOIN stg.schedule s      USING (asset_id, date, hour)
LEFT JOIN stg.prices_hourly p USING (date, hour);

-- ---------------------------------------------------------------------------------------------
-- Per-asset reconstruction, REPORT rules (reproduces the published report to ±1 cent, F-028).
-- eeg_correction here uses the report's (deviating) basis: −18,400 € spread capacity-weighted
-- across ALL wind. recon.asset_delta below proves this reproduces the report; the CORRECT basis
-- and the €-impact live in recon.deviation_1_eeg.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE recon.asset AS
WITH fed AS (
  SELECT asset_id, SUM(actual_mwh) AS total_feedin_mwh FROM recon.feedin_hour GROUP BY 1
),
energy AS (
  SELECT asset_id, SUM(energy_eur) AS energy_eur FROM recon.asset_hour GROUP BY 1
),
prem AS (
  SELECT f.asset_id, f.total_feedin_mwh * e.premium_eur_mwh AS eeg_premium_eur
  FROM fed f
  JOIN stg.contracts c USING (asset_id)
  JOIN stg.eeg_premium e
    ON e.technology = CASE c.technology WHEN 'wind' THEN 'wind_onshore' ELSE c.technology END
),
gross AS (
  SELECT en.asset_id, en.energy_eur + pr.eeg_premium_eur AS gross_revenue_eur
  FROM energy en JOIN prem pr USING (asset_id)
),
fee AS (  -- report rule: amended contracts get the NEW model applied to the whole month
  SELECT c.asset_id,
         CASE
           WHEN c.has_amendment            THEN g.gross_revenue_eur * c.amd_fee_share_pct / 100.0
           WHEN c.fee_model = 'fixed'         THEN f.total_feedin_mwh * c.fee_fixed_eur_mwh
           WHEN c.fee_model = 'revenue_share' THEN g.gross_revenue_eur * c.fee_share_pct / 100.0
         END AS management_fee_eur
  FROM stg.contracts c JOIN fed f USING (asset_id) JOIN gross g USING (asset_id)
),
costs AS (
  SELECT asset_id, SUM(allocated_amount_eur) AS allocated_costs_eur FROM stg.costs GROUP BY 1
),
wind_cap AS (  -- report's EEG-correction basis: capacity-weighted over all wind
  SELECT SUM(c.installed_capacity_kw) AS cap
  FROM stg.contracts c WHERE c.technology = 'wind'
),
corr AS (
  SELECT c.asset_id,
         CASE WHEN c.technology = 'wind'
              THEN (SELECT correction_amount_eur FROM stg.eeg_premium WHERE technology='wind_onshore')
                   * c.installed_capacity_kw / (SELECT cap FROM wind_cap)
              ELSE 0.0 END AS eeg_correction_eur
  FROM stg.contracts c
)
SELECT f.asset_id,
       f.total_feedin_mwh,
       g.gross_revenue_eur,
       pr.eeg_premium_eur,
       corr.eeg_correction_eur,
       fee.management_fee_eur,
       costs.allocated_costs_eur,
       g.gross_revenue_eur + corr.eeg_correction_eur
         - fee.management_fee_eur - costs.allocated_costs_eur AS deckungsbeitrag_eur
FROM fed f
JOIN gross  g    USING (asset_id)
JOIN prem   pr   USING (asset_id)
JOIN corr        USING (asset_id)
JOIN fee         USING (asset_id)
JOIN costs       USING (asset_id);

-- Delta vs the published report — the reproduction proof (all columns ≈ 0 → F-028).
CREATE OR REPLACE TABLE recon.asset_delta AS
SELECT a.asset_id,
       round_mwh(a.total_feedin_mwh)   - r.total_feedin_mwh   AS feedin_delta,
       round_eur(a.gross_revenue_eur)  - r.gross_revenue_eur  AS gross_delta,
       round_eur(a.eeg_premium_eur)    - r.eeg_premium_eur    AS premium_delta,
       round_eur(a.eeg_correction_eur) - r.eeg_correction_eur AS correction_delta,
       round_eur(a.management_fee_eur) - r.management_fee_eur AS fee_delta,
       round_eur(a.allocated_costs_eur)- r.allocated_costs_eur AS costs_delta,
       round_eur(a.deckungsbeitrag_eur)- r.deckungsbeitrag_eur AS db_delta
FROM recon.asset a JOIN stg.report_asset r USING (asset_id);

-- ---------------------------------------------------------------------------------------------
-- DEVIATION 1 (F-014) — EEG correction should hit north wind ONLY (costs/metadata note), not all
-- wind. report_corr − correct_corr, by region.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE recon.deviation_1_eeg AS
WITH wind AS (
  SELECT a.asset_id, a.region, c.installed_capacity_kw AS cap, r.eeg_correction_eur AS report_corr
  FROM stg.report_asset r JOIN stg.assets a USING (asset_id) JOIN stg.contracts c USING (asset_id)
  WHERE a.technology = 'wind'
),
north_cap AS (SELECT SUM(cap) AS s FROM wind WHERE region = 'north')
SELECT region,
       round_eur(SUM(report_corr))                                              AS report_correction,
       round_eur(SUM(CASE WHEN region='north'
                          THEN (SELECT correction_amount_eur FROM stg.eeg_premium WHERE technology='wind_onshore')
                               * cap / (SELECT s FROM north_cap)
                          ELSE 0.0 END))                                        AS correct_correction,
       round_eur(SUM(report_corr) - SUM(CASE WHEN region='north'
                          THEN (SELECT correction_amount_eur FROM stg.eeg_premium WHERE technology='wind_onshore')
                               * cap / (SELECT s FROM north_cap) ELSE 0.0 END)) AS misallocated_eur
FROM wind GROUP BY region ORDER BY region;

-- ---------------------------------------------------------------------------------------------
-- DEVIATION 2 (F-029) — amended contracts: report applies the new fee model to the whole month;
-- correct is a time-split at amendment_effective (fixed rate before, new share on gross after).
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE recon.deviation_2_fee AS
WITH ah AS (  -- per asset-hour gross for the amended assets
  SELECT h.asset_id, h.date, h.actual_mwh,
         h.energy_eur + h.actual_mwh
           * (SELECT premium_eur_mwh FROM stg.eeg_premium e
              JOIN stg.contracts cc ON cc.asset_id = h.asset_id
              WHERE e.technology = CASE cc.technology WHEN 'wind' THEN 'wind_onshore' ELSE cc.technology END)
           AS gross_h
  FROM recon.asset_hour h
  WHERE h.asset_id IN (SELECT asset_id FROM stg.contracts WHERE has_amendment)
),
split AS (
  SELECT ah.asset_id, c.amendment_effective AS eff, c.fee_fixed_eur_mwh AS fixed_rate,
         c.amd_fee_share_pct AS new_pct,
         SUM(ah.actual_mwh) FILTER (WHERE ah.date <  c.amendment_effective) AS fed_before,
         SUM(ah.gross_h)    FILTER (WHERE ah.date >= c.amendment_effective) AS gross_after
  FROM ah JOIN stg.contracts c USING (asset_id)
  GROUP BY 1,2,3,4
)
SELECT s.asset_id, s.eff, s.fixed_rate, s.new_pct,
       round_eur(s.fed_before * s.fixed_rate + s.gross_after * s.new_pct/100.0) AS fee_correct,
       r.management_fee_eur                                                     AS fee_report,
       round_eur(r.management_fee_eur - (s.fed_before * s.fixed_rate + s.gross_after * s.new_pct/100.0))
                                                                               AS overcharge_eur
FROM split s JOIN stg.report_asset r USING (asset_id) ORDER BY asset_id;

-- ---------------------------------------------------------------------------------------------
-- DEVIATION 3 (F-013) — 12 assets ended contracts in Jan (status=inactive) yet the report treats
-- 847 as active: active_asset_count=847 (vs 835) and every cost pool divides by 847, so the 12
-- inactive absorb cost that should fall on the active 835.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE recon.deviation_3_inactive AS
SELECT (SELECT active_asset_count FROM stg.report_portfolio)                  AS report_active_count,
       (SELECT COUNT(*) FROM stg.assets WHERE status='active')               AS correct_active_count,
       (SELECT COUNT(*) FROM stg.assets WHERE status='inactive')             AS inactive_count,
       round_eur((SELECT SUM(cc.allocated_amount_eur)
                  FROM stg.costs cc JOIN stg.assets a USING (asset_id)
                  WHERE a.status='inactive'))                                AS costs_on_inactive_eur;
