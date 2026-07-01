# QUADRA Energy DSF Challenge Reference

Source: `README.md` links to `https://poc16264.quadra-energy.com/challenge`.

## Challenge Summary

NEBELGARD Renewables GmbH is a fictional portfolio manager for 847 renewable assets in Germany:

- 612 wind assets.
- 235 solar assets.
- All challenge data is synthetic and fictional.
- The reporting month is January 2025.
- The monthly `Deckungsbeitrag` report, meaning contribution-margin report, is generated per asset and aggregated to region and portfolio level.
- The report feeds investor reporting, management fee invoicing, and P&L tracking.
- The published January 2025 report is wrong.
- Exactly three deviations exist between the source data and the published report, no more and no less.
- Deviations are individually small within a portfolio with roughly EUR 2.3M monthly turnover.
- No deviation is visible from one source alone; each requires cross-referencing at least two data sources.
- The aggregation chain has about seven steps.
- A deviation early in the chain compounds through downstream aggregation.
- Comparing totals and hunting outliers is expected to find noise.
- The intended approach is to trace one asset end-to-end, then generalize.

## Required Work

1. Explore the raw data behind the report through the nine API endpoints.
2. Reverse-engineer the aggregation step by step from raw sources to report output.
3. Identify all three deviations.
4. For each deviation, provide:
   - Source.
   - EUR impact.
   - Root cause.
5. Write a DSF-format spec precise enough for an agent to reproduce the correct aggregation without asking questions.

The spec is the deliverable. Submissions are evaluated on:

- Process reconstruction.
- API thoroughness.
- Deviation identification.
- Spec precision.

Every submission is reviewed by a human. There is no time limit.

## API Access

Base URL:

```text
https://poc16264.quadra-energy.com/api/v1
```

Authentication:

- Every API call must include the personal API key in the `X-API-Key` header.
- The key is shown once after registration.
- The platform stores only a hash of the key.

Common response envelope:

```json
{
  "data": [],
  "meta": {
    "page": 1,
    "page_size": 100,
    "total_records": 0,
    "has_next_page": false,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

Common API behavior:

- Rate limit: 120 requests per minute per API key.
- IP rate limit: 240 requests per minute per IP.
- Rate limit headers on every response:
  - `X-RateLimit-Remaining`
  - `X-RateLimit-Reset`
- Data window: January 2025.
- Date ranges outside January 2025 return HTTP 200 with an empty `data` array.
- Validation errors return HTTP 400 and name the invalid parameter.
- Missing or unknown API key returns HTTP 401.

## Endpoint Reference

### `GET /data/feedin`

Quarter-hourly feed-in actuals per asset for January 2025. Approximately 2.5 million rows.

Important unit note:

- `energy_kwh` is in kWh.
- Every other source works in MWh.

Parameters:

| Parameter | Required | Values/default |
| --- | --- | --- |
| `date_from` | yes | ISO date `YYYY-MM-DD` |
| `date_to` | yes | ISO date `YYYY-MM-DD` |
| `asset_id` | no | Single asset, e.g. `WND-0042` |
| `asset_ids` | no | Comma-separated list of asset IDs |
| `quality_filter` | no | `all` or `validated_only`; default `all` |
| `page` | no | Integer >= 1; default `1` |
| `page_size` | no | 1-500; default `100` |

Rows contain:

- `asset_id`
- `timestamp`: naive string, no timezone designator, exactly as exported by the source system.
- `energy_kwh`
- `quality_flag`: `validated`, `estimated`, or `raw`.
- `source_system`: `SCADA_v3`, `SCADA_legacy`, or `manual_entry`.

Example:

```bash
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "https://poc16264.quadra-energy.com/api/v1/data/feedin?asset_id=WND-0042&date_from=2025-01-07&date_to=2025-01-07"
```

Truncated sample response:

```json
{
  "data": [
    {
      "asset_id": "WND-0042",
      "timestamp": "2025-01-07T00:00:00",
      "energy_kwh": 412.375,
      "quality_flag": "validated",
      "source_system": "SCADA_v3"
    },
    {
      "asset_id": "WND-0042",
      "timestamp": "2025-01-07T00:15:00",
      "energy_kwh": 398.250,
      "quality_flag": "validated",
      "source_system": "SCADA_v3"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 100,
    "total_records": 96,
    "has_next_page": false,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

### `GET /data/prices`

Hourly market prices for January 2025:

- Day-ahead clearing price.
- Volume-weighted intraday average per hour.
- 1,488 rows total.
- `id_volume_mwh` is the portfolio-level intraday volume for that hour, not per asset.

Parameters:

| Parameter | Required | Values/default |
| --- | --- | --- |
| `date_from` | yes | ISO date `YYYY-MM-DD` |
| `date_to` | yes | ISO date `YYYY-MM-DD` |
| `product` | no | `DA`, `ID`, or `all`; default `all` |

Rows contain:

- `date`
- `hour`: 0-23.
- `product`
- `price_eur_mwh`
- `id_volume_mwh`

Example:

```bash
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "https://poc16264.quadra-energy.com/api/v1/data/prices?date_from=2025-01-07&date_to=2025-01-07&product=DA"
```

Truncated sample response:

```json
{
  "data": [
    {
      "date": "2025-01-07",
      "hour": 0,
      "product": "DA",
      "price_eur_mwh": 71.84,
      "id_volume_mwh": 142.503
    },
    {
      "date": "2025-01-07",
      "hour": 0,
      "product": "ID",
      "price_eur_mwh": 76.02,
      "id_volume_mwh": 142.503
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 100,
    "total_records": 48,
    "has_next_page": false,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

### `GET /data/schedule`

Day-ahead schedule per asset and hour. This is the volume committed to the grid operator the day before.

Business note:

- Actual feed-in minus schedule is the intraday delta.

Parameters:

| Parameter | Required | Values/default |
| --- | --- | --- |
| `date_from` | yes | ISO date `YYYY-MM-DD` |
| `date_to` | yes | ISO date `YYYY-MM-DD` |
| `asset_id` | no | Single asset |
| `page` | no | Integer >= 1; default `1` |
| `page_size` | no | 1-500; default `100` |

Rows contain:

- `asset_id`
- `date`
- `hour`: 0-23.
- `scheduled_mwh`

Example:

```bash
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "https://poc16264.quadra-energy.com/api/v1/data/schedule?asset_id=WND-0042&date_from=2025-01-07&date_to=2025-01-07"
```

Truncated sample response:

```json
{
  "data": [
    {
      "asset_id": "WND-0042",
      "date": "2025-01-07",
      "hour": 0,
      "scheduled_mwh": 1.625
    },
    {
      "asset_id": "WND-0042",
      "date": "2025-01-07",
      "hour": 1,
      "scheduled_mwh": 1.580
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 100,
    "total_records": 24,
    "has_next_page": false,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

### `GET /data/contracts`

Contract master data for all 847 assets:

- Owner.
- Region.
- Capacity.
- Contract window.
- Fee model.
- Amendment history.

The challenge page explicitly says to read the field list closely because the fee logic lives here.

Parameters:

| Parameter | Required | Values/default |
| --- | --- | --- |
| `asset_id` | no | Single asset |
| `technology` | no | `wind` or `solar` |
| `region` | no | `north`, `south`, `east`, or `west` |
| `include_amendments` | no | `true` or `false`; default `true` |
| `active_on` | no | ISO date `YYYY-MM-DD`; only contracts active on that date |
| `page` | no | Integer >= 1; default `1` |
| `page_size` | no | 1-200; default `50` |

Rows contain:

- `asset_id`
- `contract_id`
- `owner_name`
- `technology`
- `region`
- `installed_capacity_kw`
- `commissioning_date`
- `contract_start`
- `contract_end`
- `fee_model`: `fixed`, `revenue_share`, or `ppa`.
- `fee_fixed_eur_mwh`
- `fee_share_pct`
- `operator_id`
- `scada_system`: `SCADA_v3` or `SCADA_legacy`.
- `amendment_id`
- `amendment_effective`

Example:

```bash
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "https://poc16264.quadra-energy.com/api/v1/data/contracts?asset_id=WND-0042"
```

Truncated sample response:

```json
{
  "data": [
    {
      "asset_id": "WND-0042",
      "contract_id": "CTR-2019-0042",
      "owner_name": "Windkraft Huegelland GmbH & Co. KG",
      "technology": "wind",
      "region": "west",
      "installed_capacity_kw": 3200.0,
      "commissioning_date": "2019-06-14",
      "contract_start": "2022-01-01",
      "contract_end": "2027-12-31",
      "fee_model": "fixed",
      "fee_fixed_eur_mwh": 4.20,
      "fee_share_pct": null,
      "operator_id": "OP-014",
      "scada_system": "SCADA_v3",
      "amendment_id": null,
      "amendment_effective": null
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total_records": 847,
    "has_next_page": true,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

### `GET /data/eeg_premium`

EEG market premium, `Marktpraemie`, per technology and month from the monthly grid-operator statement, including any retroactive correction.

Parameters:

| Parameter | Required | Values/default |
| --- | --- | --- |
| `month` | yes | `YYYY-MM` |
| `technology` | no | `wind_onshore` or `solar` |

Rows contain:

- `month`
- `technology`
- `premium_eur_mwh`
- `correction_flag`: `true` if a retroactive correction applies.
- `correction_amount_eur`: portfolio-level total, not per asset.

Example:

```bash
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "https://poc16264.quadra-energy.com/api/v1/data/eeg_premium?month=2025-01"
```

Truncated sample response:

```json
{
  "data": [
    {
      "month": "2025-01",
      "technology": "solar",
      "premium_eur_mwh": 6.20,
      "correction_flag": false,
      "correction_amount_eur": 0.00
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total_records": 2,
    "has_next_page": false,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

### `GET /data/costs`

Monthly cost allocations from the finance team. One row per asset and cost category, with allocation basis and the pool it was divided from.

Parameters:

| Parameter | Required | Values/default |
| --- | --- | --- |
| `month` | yes | `YYYY-MM` |
| `asset_id` | no | Single asset |
| `cost_category` | no | `monitoring`, `data_fees`, `insurance`, or `grid_fees` |
| `page` | no | Integer >= 1; default `1` |
| `page_size` | no | 1-200; default `50` |

Rows contain:

- `asset_id`
- `cost_category`
- `allocated_amount_eur`
- `allocation_basis`: `capacity_weighted`, `flat`, or `per_asset`.
- `total_pool_eur`
- `active_assets_in_pool`

Example:

```bash
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "https://poc16264.quadra-energy.com/api/v1/data/costs?month=2025-01&asset_id=SOL-0007"
```

Truncated sample response:

```json
{
  "data": [
    {
      "asset_id": "SOL-0007",
      "cost_category": "monitoring",
      "allocated_amount_eur": 12.50,
      "allocation_basis": "flat",
      "total_pool_eur": 10587.50,
      "active_assets_in_pool": 847
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total_records": 4,
    "has_next_page": false,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

### `GET /data/costs/metadata`

Metadata accompanying the monthly cost allocation:

- Allocation date.
- Explicitly excluded assets.
- Free-text note from the finance team.

Parameters:

| Parameter | Required | Values/default |
| --- | --- | --- |
| `month` | yes | `YYYY-MM` |

Rows contain:

- `allocation_date`
- `note`: free text.
- `assets_excluded`

Example:

```bash
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "https://poc16264.quadra-energy.com/api/v1/data/costs/metadata?month=2025-01"
```

Truncated sample response:

```json
{
  "data": [
    {
      "allocation_date": "2025-01-31",
      "note": "...",
      "assets_excluded": []
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total_records": 1,
    "has_next_page": false,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

### `GET /report/monthly`

Published January 2025 `Deckungsbeitrag` report. This is the output of the aggregation to reconstruct and it contains the deviations.

Available granularities:

- Asset.
- Region.
- Portfolio.

Parameters:

| Parameter | Required | Values/default |
| --- | --- | --- |
| `month` | yes | `YYYY-MM` |
| `granularity` | yes | `asset`, `region`, or `portfolio` |
| `region` | no | Filters assets when `granularity=asset` |
| `page` | no | Integer >= 1; default `1`; used when `granularity=asset` |
| `page_size` | no | 1-100; default `25` |

Asset rows contain:

- `asset_id`
- `gross_revenue_eur`
- `eeg_premium_eur`
- `eeg_correction_eur`
- `management_fee_eur`
- `allocated_costs_eur`
- `deckungsbeitrag_eur`
- `db_per_mwh`
- `total_feedin_mwh`
- `availability_pct`

Portfolio row additionally contains:

- `total_gross_revenue_eur`
- `total_db_eur`
- `db_margin_pct`
- `active_asset_count`

Example:

```bash
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "https://poc16264.quadra-energy.com/api/v1/report/monthly?month=2025-01&granularity=portfolio"
```

Truncated sample response:

```json
{
  "data": [
    {
      "asset_id": "WND-0042",
      "gross_revenue_eur": 28714.55,
      "eeg_premium_eur": 2406.32,
      "eeg_correction_eur": -41.87,
      "management_fee_eur": 1203.27,
      "allocated_costs_eur": 173.20,
      "deckungsbeitrag_eur": 27296.21,
      "db_per_mwh": 95.27,
      "total_feedin_mwh": 286.512,
      "availability_pct": 97.12
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 25,
    "total_records": 847,
    "has_next_page": true,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

### `GET /assets`

Slim portfolio list for overview before using contract master data.

Important status note:

- `status=inactive` marks assets whose contract ended during January.

Parameters:

| Parameter | Required | Values/default |
| --- | --- | --- |
| `technology` | no | `wind` or `solar` |
| `region` | no | `north`, `south`, `east`, or `west` |
| `status` | no | `active`, `inactive`, or `all`; default `active` |
| `page` | no | Integer >= 1; default `1` |
| `page_size` | no | 1-200; default `50` |

Rows contain:

- `asset_id`
- `technology`
- `region`
- `installed_capacity_kw`
- `commissioning_date`
- `status`

Example:

```bash
curl -H "X-API-Key: <YOUR_API_KEY>" \
  "https://poc16264.quadra-energy.com/api/v1/assets?status=all&page=1&page_size=50"
```

Truncated sample response:

```json
{
  "data": [
    {
      "asset_id": "SOL-0007",
      "technology": "solar",
      "region": "south",
      "installed_capacity_kw": 1450.0,
      "commissioning_date": "2021-03-22",
      "status": "active"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total_records": 847,
    "has_next_page": true,
    "generated_at": "2025-02-03T08:14:22Z"
  }
}
```

## DSF Spec Template

Submit the spec in exactly seven fields, no more and no less:

```text
INTENT
What business outcome does this process achieve?
One sentence. Start with a verb.

TRIGGER
When does this process run?
Be precise: day, time, timezone, conditions.

DATA INPUTS
List every API call you would make.
For each: endpoint, all parameters with exact values, and why those values.
Do not write "get the feedin data" - write the exact call.

BUSINESS LOGIC
Describe the aggregation step by step.
Use concrete numbers for thresholds.
Use if/then for conditional logic.
Do not skip steps - if you had to explain a step, it belongs here.

GUARDRAILS
What should the agent NOT do?
When should it stop and ask a human?
What data quality checks must pass before proceeding?

OUTPUT
What is delivered? To whom? In what format?
What does a correct output look like at portfolio level?

SUCCESS CRITERIA
How do we know it worked?
Preferred format: Given [condition] / When [process runs] / Then [expected result]
Give at least three criteria.
```

Challenge advice:

- `DATA INPUTS` can determine success or failure: name every endpoint, every parameter, every value, and why.
- Defaults are also choices and should be explicit.
- `BUSINESS LOGIC` is where deviations live or die.
- Any aggregation step that is glossed over is a step the agent may get wrong.

## Submission Process

1. Register to receive an access code and personal API key. The key is shown exactly once.
2. Work the problem with an agent against the API.
3. Log in with the access code and open the Submit page.
4. Paste the seven-field spec and the agent output.
5. Tick the deviations found.
6. Up to three submissions are allowed; the best submission counts.

Relevant site routes:

- Challenge: `https://poc16264.quadra-energy.com/challenge`
- Register: `https://poc16264.quadra-energy.com/register`
- Login: `https://poc16264.quadra-energy.com/login`
- Submit: `https://poc16264.quadra-energy.com/submit`

## Legal Notes

- The challenge is a voluntary skills demonstration.
- Participation does not create an employment relationship.
- Participation is not remunerated.
- All challenge data is synthetic and fictitious.
- Assets, operators, prices, and measurements are generated for this challenge and do not describe any real installation or company.
- Submissions are used solely for evaluation in this hiring process.
- Submissions are never used as training data.
- Submissions are never used as production work.
- QUADRA energy may end the challenge at any time.
- There is no legal claim to evaluation or feedback: `Es besteht kein Rechtsanspruch auf Bewertung oder Feedback`.

## Quick API Checklist

Use this checklist when reconstructing the report:

- Fetch complete asset inventory with `GET /assets?status=all` and paginate.
- Fetch contract master data with `GET /data/contracts`, explicitly considering `include_amendments` and `active_on`.
- Fetch feed-in actuals from `GET /data/feedin` for January 2025, remembering kWh-to-MWh conversion.
- Fetch day-ahead schedules from `GET /data/schedule` for January 2025.
- Fetch hourly DA and ID prices from `GET /data/prices` for January 2025.
- Fetch EEG premium data from `GET /data/eeg_premium?month=2025-01`.
- Fetch allocated costs from `GET /data/costs?month=2025-01`.
- Fetch cost allocation metadata from `GET /data/costs/metadata?month=2025-01`.
- Fetch published report rows from `GET /report/monthly?month=2025-01` at asset, region, and portfolio granularity.
- Cross-reference at least two sources for each suspected deviation.
