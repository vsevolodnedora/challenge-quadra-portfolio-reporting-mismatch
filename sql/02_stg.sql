-- 02_stg.sql — the single interpretation layer.
-- Every column is extracted from the verbatim raw._record JSON with an explicit type.
-- The two highest-risk normalizations live here and NOWHERE else:
--   (1) kWh -> MWh  (÷1000, feed-in only)
--   (2) the naive-timestamp parse (no tz shift: January has no DST in Germany)
-- Text -> json_extract_string; numerics -> CAST(json_extract(...) AS DOUBLE); dates -> TRY_CAST.

CREATE SCHEMA IF NOT EXISTS stg;

-- assets: typed + inactive flag (status='inactive' == contract ended during January)
CREATE OR REPLACE TABLE stg.assets AS
SELECT json_extract_string(_record, '$.asset_id')                        AS asset_id,
       json_extract_string(_record, '$.technology')                     AS technology,
       json_extract_string(_record, '$.region')                         AS region,
       CAST(json_extract(_record, '$.installed_capacity_kw') AS DOUBLE)  AS installed_capacity_kw,
       TRY_CAST(json_extract_string(_record, '$.commissioning_date') AS DATE) AS commissioning_date,
       json_extract_string(_record, '$.status')                         AS status,
       (json_extract_string(_record, '$.status') = 'inactive')          AS is_inactive
FROM raw.assets;

-- contracts: parse dates, flatten the nested amendment (fee logic lives here)
CREATE OR REPLACE TABLE stg.contracts AS
SELECT json_extract_string(_record, '$.asset_id')                        AS asset_id,
       json_extract_string(_record, '$.contract_id')                    AS contract_id,
       json_extract_string(_record, '$.owner_name')                     AS owner_name,
       json_extract_string(_record, '$.technology')                     AS technology,
       json_extract_string(_record, '$.region')                         AS region,
       CAST(json_extract(_record, '$.installed_capacity_kw') AS DOUBLE)  AS installed_capacity_kw,
       TRY_CAST(json_extract_string(_record, '$.commissioning_date') AS DATE) AS commissioning_date,
       TRY_CAST(json_extract_string(_record, '$.contract_start') AS DATE)     AS contract_start,
       TRY_CAST(json_extract_string(_record, '$.contract_end')   AS DATE)     AS contract_end,
       json_extract_string(_record, '$.fee_model')                      AS fee_model,
       TRY_CAST(json_extract(_record, '$.fee_fixed_eur_mwh') AS DOUBLE)  AS fee_fixed_eur_mwh,
       TRY_CAST(json_extract(_record, '$.fee_share_pct')     AS DOUBLE)  AS fee_share_pct,
       json_extract_string(_record, '$.operator_id')                    AS operator_id,
       json_extract_string(_record, '$.scada_system')                   AS scada_system,
       json_extract_string(_record, '$.amendment_id')                   AS amendment_id,
       TRY_CAST(json_extract_string(_record, '$.amendment_effective') AS DATE) AS amendment_effective,
       (json_extract_string(_record, '$.amendment_id') IS NOT NULL)     AS has_amendment,
       json_extract_string(_record, '$.amendment.fee_model')            AS amd_fee_model,
       TRY_CAST(json_extract(_record, '$.amendment.fee_fixed_eur_mwh') AS DOUBLE) AS amd_fee_fixed_eur_mwh,
       TRY_CAST(json_extract(_record, '$.amendment.fee_share_pct')     AS DOUBLE) AS amd_fee_share_pct
FROM raw.contracts;

-- feed-in quarter-hour: kWh -> MWh and the naive-timestamp parse (grain = asset x quarter-hour)
CREATE OR REPLACE TABLE stg.feedin_qh AS
WITH x AS (
  SELECT json_extract_string(_record, '$.timestamp')     AS ts_s,
         json_extract_string(_record, '$.asset_id')      AS asset_id,
         json_extract(_record, '$.energy_kwh')           AS energy_j,
         json_extract_string(_record, '$.quality_flag')  AS quality_flag,
         json_extract_string(_record, '$.source_system') AS source_system
  FROM raw.feedin
)
SELECT asset_id,
       CAST(ts_s AS TIMESTAMP)                    AS ts,
       CAST(CAST(ts_s AS TIMESTAMP) AS DATE)      AS date,
       EXTRACT(hour FROM CAST(ts_s AS TIMESTAMP)) AS hour,
       CAST(energy_j AS DOUBLE) / 1000.0          AS energy_mwh,   -- the one kWh->MWh conversion
       quality_flag, source_system
FROM x;

-- feed-in hourly: aggregate quarter-hours to the hour (join grain for schedule + prices)
CREATE OR REPLACE TABLE stg.feedin_hourly AS
SELECT asset_id, date, hour,
       SUM(energy_mwh)                          AS actual_mwh,
       COUNT(*)                                 AS n_quarters,
       BOOL_OR(quality_flag <> 'validated')     AS has_nonvalidated
FROM stg.feedin_qh
GROUP BY asset_id, date, hour;

-- feed-in monthly: report column total_feedin_mwh
CREATE OR REPLACE TABLE stg.feedin_monthly AS
SELECT asset_id, SUM(energy_mwh) AS total_feedin_mwh, COUNT(*) AS n_quarters
FROM stg.feedin_qh
GROUP BY asset_id;

-- schedule: typed (structurally incomplete — inactive assets stop on contract_end)
CREATE OR REPLACE TABLE stg.schedule AS
SELECT json_extract_string(_record, '$.asset_id')            AS asset_id,
       CAST(json_extract_string(_record, '$.date') AS DATE)  AS date,
       CAST(json_extract(_record, '$.hour') AS INTEGER)      AS hour,
       CAST(json_extract(_record, '$.scheduled_mwh') AS DOUBLE) AS scheduled_mwh
FROM raw.schedule;

-- prices: pivot DA/ID onto one row per (date,hour); id_volume is portfolio-level (not allocated)
CREATE OR REPLACE TABLE stg.prices_hourly AS
WITH p AS (
  SELECT CAST(json_extract_string(_record, '$.date') AS DATE)   AS date,
         CAST(json_extract(_record, '$.hour') AS INTEGER)       AS hour,
         json_extract_string(_record, '$.product')              AS product,
         CAST(json_extract(_record, '$.price_eur_mwh') AS DOUBLE) AS price_eur_mwh,
         CAST(json_extract(_record, '$.id_volume_mwh') AS DOUBLE) AS id_volume_mwh
  FROM raw.prices
)
SELECT date, hour,
       MAX(price_eur_mwh) FILTER (WHERE product = 'DA') AS da_price_eur_mwh,
       MAX(price_eur_mwh) FILTER (WHERE product = 'ID') AS id_price_eur_mwh,
       MAX(id_volume_mwh)                               AS id_volume_mwh
FROM p
GROUP BY 1, 2;

-- eeg premium: keep EEG technology domain (wind_onshore/solar); correction is portfolio-level
CREATE OR REPLACE TABLE stg.eeg_premium AS
SELECT json_extract_string(_record, '$.month')                          AS month,
       json_extract_string(_record, '$.technology')                     AS technology,
       CAST(json_extract(_record, '$.premium_eur_mwh') AS DOUBLE)        AS premium_eur_mwh,
       CAST(json_extract(_record, '$.correction_flag') AS BOOLEAN)       AS correction_flag,
       CAST(json_extract(_record, '$.correction_amount_eur') AS DOUBLE)  AS correction_amount_eur
FROM raw.eeg_premium;

-- costs: typed passthrough (one row per asset x cost_category)
CREATE OR REPLACE TABLE stg.costs AS
SELECT json_extract_string(_record, '$.asset_id')                       AS asset_id,
       json_extract_string(_record, '$.cost_category')                  AS cost_category,
       CAST(json_extract(_record, '$.allocated_amount_eur') AS DOUBLE)   AS allocated_amount_eur,
       json_extract_string(_record, '$.allocation_basis')               AS allocation_basis,
       CAST(json_extract(_record, '$.total_pool_eur') AS DOUBLE)         AS total_pool_eur,
       CAST(json_extract(_record, '$.active_assets_in_pool') AS INTEGER) AS active_assets_in_pool
FROM raw.costs;

CREATE OR REPLACE TABLE stg.costs_metadata AS
SELECT TRY_CAST(json_extract_string(_record, '$.allocation_date') AS DATE) AS allocation_date,
       json_extract_string(_record, '$.note')            AS note,
       json_extract(_record, '$.assets_excluded')        AS assets_excluded,   -- JSON list, kept verbatim
       json_array_length(json_extract(_record, '$.assets_excluded')) AS n_assets_excluded
FROM raw.costs_metadata;

-- report (the target being reconstructed) — typed for reconciliation + internal checks
CREATE OR REPLACE TABLE stg.report_asset AS
SELECT json_extract_string(_record, '$.asset_id')                       AS asset_id,
       CAST(json_extract(_record, '$.gross_revenue_eur')   AS DOUBLE)    AS gross_revenue_eur,
       CAST(json_extract(_record, '$.eeg_premium_eur')     AS DOUBLE)    AS eeg_premium_eur,
       CAST(json_extract(_record, '$.eeg_correction_eur')  AS DOUBLE)    AS eeg_correction_eur,
       CAST(json_extract(_record, '$.management_fee_eur')  AS DOUBLE)    AS management_fee_eur,
       CAST(json_extract(_record, '$.allocated_costs_eur') AS DOUBLE)    AS allocated_costs_eur,
       CAST(json_extract(_record, '$.deckungsbeitrag_eur') AS DOUBLE)    AS deckungsbeitrag_eur,
       CAST(json_extract(_record, '$.db_per_mwh')          AS DOUBLE)    AS db_per_mwh,
       CAST(json_extract(_record, '$.total_feedin_mwh')    AS DOUBLE)    AS total_feedin_mwh,
       CAST(json_extract(_record, '$.availability_pct')    AS DOUBLE)    AS availability_pct
FROM raw.report_asset;

CREATE OR REPLACE TABLE stg.report_region AS
SELECT json_extract_string(_record, '$.region')                         AS region,
       CAST(json_extract(_record, '$.gross_revenue_eur')   AS DOUBLE)    AS gross_revenue_eur,
       CAST(json_extract(_record, '$.eeg_premium_eur')     AS DOUBLE)    AS eeg_premium_eur,
       CAST(json_extract(_record, '$.eeg_correction_eur')  AS DOUBLE)    AS eeg_correction_eur,
       CAST(json_extract(_record, '$.management_fee_eur')  AS DOUBLE)    AS management_fee_eur,
       CAST(json_extract(_record, '$.allocated_costs_eur') AS DOUBLE)    AS allocated_costs_eur,
       CAST(json_extract(_record, '$.deckungsbeitrag_eur') AS DOUBLE)    AS deckungsbeitrag_eur,
       CAST(json_extract(_record, '$.total_feedin_mwh')    AS DOUBLE)    AS total_feedin_mwh,
       CAST(json_extract(_record, '$.asset_count')         AS INTEGER)   AS asset_count
FROM raw.report_region;

CREATE OR REPLACE TABLE stg.report_portfolio AS
SELECT CAST(json_extract(_record, '$.total_gross_revenue_eur')   AS DOUBLE)  AS total_gross_revenue_eur,
       CAST(json_extract(_record, '$.total_eeg_premium_eur')     AS DOUBLE)  AS total_eeg_premium_eur,
       CAST(json_extract(_record, '$.total_eeg_correction_eur')  AS DOUBLE)  AS total_eeg_correction_eur,
       CAST(json_extract(_record, '$.total_management_fee_eur')  AS DOUBLE)  AS total_management_fee_eur,
       CAST(json_extract(_record, '$.total_allocated_costs_eur') AS DOUBLE)  AS total_allocated_costs_eur,
       CAST(json_extract(_record, '$.total_db_eur')              AS DOUBLE)  AS total_db_eur,
       CAST(json_extract(_record, '$.total_feedin_mwh')          AS DOUBLE)  AS total_feedin_mwh,
       CAST(json_extract(_record, '$.db_margin_pct')             AS DOUBLE)  AS db_margin_pct,
       CAST(json_extract(_record, '$.active_asset_count')        AS INTEGER) AS active_asset_count
FROM raw.report_portfolio;
