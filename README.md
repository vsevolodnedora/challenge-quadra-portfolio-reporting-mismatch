# challenge-quadra-portfolio-reporting-mismatch
Hiring challenge from QUADRA energy to identify portfolio reporting mismatch

Link to the challenge: https://poc16264.quadra-energy.com/challenge

## Local Credentials

API credentials are stored locally in `.env`.

Expected variables (obtained from challenge webpage):

- `QUADRA_API_KEY`: sent to the API as the `X-API-Key` header.
- `QUADRA_ACCESS_CODE`: used for the challenge web login/submission flow.

Do not commit or paste credential values into documentation, logs, or submissions.

## Python Setup

Use Python 3.12 with a local virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Core project technology:

- DuckDB for hostless local analytical storage.
- Polars for fast in-memory/lazy tabular transformations where useful.
- pandas and pyarrow for tabular import/export.
- requests and python-dotenv for authenticated API extraction.
- pytest for validation tests.

## Design Principles

Optimize for correctness, auditability, and speed; avoid platform-level abstraction.

- Components: separate API extraction, raw storage, normalization, reconciliation, validation, and reporting.
- Storage: preserve immutable raw API extracts; persist normalized/derived analytical tables in DuckDB.
- Compute: use Polars for fast typed transformations where useful; avoid obscuring lineage by mixing engines unnecessarily.
- Types: use Pydantic/typed schemas for config, endpoint params, response validation, and domain records.
- Validation: fail on missing credentials, incomplete pagination, schema drift, bad enums, duplicate keys, unit/date errors, or unexpected nulls.
- Logic: keep formulas explicit and testable, especially unit conversion, hourly aggregation, joins, contract amendments, allocation denominators, and rounding.
- Reproducibility: final outputs must rebuild from raw data via scripts/queries; notebooks are exploratory only.
- Precision: calculate at full precision; round only at report comparison/output boundaries.
- Scope: add abstractions only when they reduce duplication, isolate a real boundary, or improve tests.
- Assumptions: document inferred business rules with the source fields/report comparisons that justify them.
