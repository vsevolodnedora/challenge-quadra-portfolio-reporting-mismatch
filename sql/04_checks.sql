-- 04_checks.sql — validation as observation, not rejection.
-- Builds recon.checks(check, severity, observed, ok, note). Two severities:
--   gate    : pipeline/load integrity — MUST hold, or the build is broken (build_warehouse exits nonzero).
--   observe : data-content expectations — recorded as candidate findings; NEVER fail the build,
--             because an anomaly here (a bad enum, a broken sum, an unexpected null) may itself
--             be one of the three report deviations.

CREATE OR REPLACE TABLE recon.checks AS
WITH
  fq   AS (SELECT count(*) AS n, count(DISTINCT (asset_id, ts)) AS d FROM stg.feedin_qh),
  ra   AS (SELECT count(*) AS n, count(DISTINCT asset_id) AS d FROM stg.report_asset),
  ph   AS (SELECT count(*) AS n,
                  count(*) FILTER (WHERE da_price_eur_mwh IS NULL OR id_price_eur_mwh IS NULL) AS missing
           FROM stg.prices_hourly),
  fpa  AS (SELECT count(*) AS offenders
           FROM (SELECT asset_id FROM stg.feedin_qh GROUP BY asset_id HAVING count(*) <> 2976)),
  dbid AS (SELECT count(*) AS viol FROM stg.report_asset
           WHERE abs(deckungsbeitrag_eur
                     - (gross_revenue_eur + eeg_correction_eur - management_fee_eur - allocated_costs_eur)) > 0.005),
  sched AS (
    SELECT count(*) AS offenders FROM (
      SELECT s.asset_id
      FROM (SELECT asset_id, count(*) AS rows FROM stg.schedule GROUP BY asset_id) s
      JOIN stg.contracts c USING (asset_id)
      WHERE s.rows <> 24 * CASE WHEN c.contract_end < DATE '2025-01-01' THEN 0
                                WHEN c.contract_end > DATE '2025-01-31' THEN 31
                                ELSE EXTRACT(day FROM c.contract_end) END)),
  inact AS (SELECT count(*) FILTER (WHERE is_inactive) AS n_inactive, count(*) AS n_assets FROM stg.assets),
  acnt AS (SELECT (SELECT active_asset_count FROM stg.report_portfolio) AS rep,
                  (SELECT n_assets - n_inactive FROM inact)             AS calc),
  reg_db AS (
    SELECT count(*) AS offenders FROM (
      SELECT rr.region
      FROM stg.report_region rr
      JOIN (SELECT a.region, ra2.deckungsbeitrag_eur AS db
            FROM stg.report_asset ra2 JOIN stg.assets a USING (asset_id)) x ON x.region = rr.region
      GROUP BY rr.region, rr.deckungsbeitrag_eur
      HAVING abs(SUM(x.db) - rr.deckungsbeitrag_eur) > 0.005)),
  pf_db AS (SELECT abs((SELECT SUM(deckungsbeitrag_eur) FROM stg.report_region)
                       - (SELECT total_db_eur FROM stg.report_portfolio)) AS diff)
SELECT * FROM (
  SELECT 'feedin_qh_grain_unique'     AS check_name, 'gate' AS severity,
         CAST((SELECT n - d FROM fq) AS VARCHAR) AS observed, (SELECT n = d FROM fq) AS ok,
         'duplicate (asset_id,ts) in feed-in quarter-hours' AS note
  UNION ALL SELECT 'report_asset_grain_unique','gate',
         CAST((SELECT n - d FROM ra) AS VARCHAR), (SELECT n = d FROM ra),
         'duplicate asset_id in report'
  UNION ALL SELECT 'prices_hourly_count_744','gate',
         CAST((SELECT n FROM ph) AS VARCHAR), (SELECT n = 744 FROM ph),
         'expected 31*24=744 hourly price rows'
  UNION ALL SELECT 'prices_hours_complete','gate',
         CAST((SELECT missing FROM ph) AS VARCHAR), (SELECT missing = 0 FROM ph),
         'hours missing a DA or ID price'
  UNION ALL SELECT 'feedin_per_asset_2976','gate',
         CAST((SELECT offenders FROM fpa) AS VARCHAR), (SELECT offenders = 0 FROM fpa),
         'assets whose feed-in != 2976 quarter-hours'
  UNION ALL SELECT 'db_identity_violations','observe',
         CAST((SELECT viol FROM dbid) AS VARCHAR), (SELECT viol = 0 FROM dbid),
         'report rows where DB != gross+corr-fee-costs (tol 0.005)'
  UNION ALL SELECT 'region_db_sum_matches_assets','observe',
         CAST((SELECT offenders FROM reg_db) AS VARCHAR), (SELECT offenders = 0 FROM reg_db),
         'regions where report DB != sum of member-asset DB'
  UNION ALL SELECT 'portfolio_db_sum_matches_regions','observe',
         CAST(round((SELECT diff FROM pf_db), 4) AS VARCHAR), (SELECT diff <= 0.005 FROM pf_db),
         'portfolio total_db vs sum of region DB'
  UNION ALL SELECT 'schedule_vs_active_days','observe',
         CAST((SELECT offenders FROM sched) AS VARCHAR), (SELECT offenders = 0 FROM sched),
         'pulled assets where schedule rows != 24*active_days'
  UNION ALL SELECT 'inactive_assets','observe',
         CAST((SELECT n_inactive FROM inact) AS VARCHAR), (SELECT n_inactive = 12 FROM inact),
         'count of status=inactive assets (expect 12)'
  UNION ALL SELECT 'active_count_vs_inactive','observe',
         CAST((SELECT rep FROM acnt) AS VARCHAR), (SELECT rep = calc FROM acnt),
         'report active_asset_count vs (total assets - inactive)'
  UNION ALL SELECT 'enum_quality_flag','observe',
         CAST((SELECT count(*) FROM stg.feedin_qh WHERE quality_flag NOT IN ('validated','estimated','raw')) AS VARCHAR),
         (SELECT count(*) = 0 FROM stg.feedin_qh WHERE quality_flag NOT IN ('validated','estimated','raw')),
         'feed-in quality_flag outside {validated,estimated,raw}'
  UNION ALL SELECT 'enum_source_system','observe',
         CAST((SELECT count(*) FROM stg.feedin_qh WHERE source_system NOT IN ('SCADA_v3','SCADA_legacy','manual_entry')) AS VARCHAR),
         (SELECT count(*) = 0 FROM stg.feedin_qh WHERE source_system NOT IN ('SCADA_v3','SCADA_legacy','manual_entry')),
         'feed-in source_system outside its documented set'
  UNION ALL SELECT 'enum_fee_model','observe',
         CAST((SELECT count(*) FROM stg.contracts WHERE fee_model NOT IN ('fixed','revenue_share','ppa')) AS VARCHAR),
         (SELECT count(*) = 0 FROM stg.contracts WHERE fee_model NOT IN ('fixed','revenue_share','ppa')),
         'contract fee_model outside {fixed,revenue_share,ppa}'
  UNION ALL SELECT 'enum_allocation_basis','observe',
         CAST((SELECT count(*) FROM stg.costs WHERE allocation_basis NOT IN ('capacity_weighted','flat','per_asset')) AS VARCHAR),
         (SELECT count(*) = 0 FROM stg.costs WHERE allocation_basis NOT IN ('capacity_weighted','flat','per_asset')),
         'cost allocation_basis outside its documented set'
  UNION ALL SELECT 'enum_region','observe',
         CAST((SELECT count(*) FROM stg.assets WHERE region NOT IN ('north','south','east','west')) AS VARCHAR),
         (SELECT count(*) = 0 FROM stg.assets WHERE region NOT IN ('north','south','east','west')),
         'asset region outside {north,south,east,west}'
  UNION ALL SELECT 'enum_technology','observe',
         CAST((SELECT count(*) FROM stg.assets WHERE technology NOT IN ('wind','solar')) AS VARCHAR),
         (SELECT count(*) = 0 FROM stg.assets WHERE technology NOT IN ('wind','solar')),
         'asset technology outside {wind,solar}'
  UNION ALL SELECT 'null_feedin_key_qty','observe',
         CAST((SELECT count(*) FROM stg.feedin_qh WHERE asset_id IS NULL OR ts IS NULL OR energy_mwh IS NULL) AS VARCHAR),
         (SELECT count(*) = 0 FROM stg.feedin_qh WHERE asset_id IS NULL OR ts IS NULL OR energy_mwh IS NULL),
         'nulls in feed-in key/quantity'
  UNION ALL SELECT 'costs_metadata_excluded','observe',
         CAST((SELECT coalesce(max(n_assets_excluded), 0) FROM stg.costs_metadata) AS VARCHAR),
         (SELECT coalesce(max(n_assets_excluded), 0) = 0 FROM stg.costs_metadata),
         'assets_excluded list length (metadata note claims empty)'
  -- Reconstruction fidelity: recon.asset (report rules, F-023..F-028) must reproduce every
  -- published DB to the cent. A regression here means a pinned formula drifted.
  UNION ALL SELECT 'recon_reproduces_report_db','observe',
         CAST((SELECT count(*) FROM recon.asset_delta WHERE abs(db_delta) > 0.02) AS VARCHAR),
         (SELECT count(*) = 0 FROM recon.asset_delta WHERE abs(db_delta) > 0.02),
         'assets where reconstructed deckungsbeitrag != report (tol 0.02)'
) t;
