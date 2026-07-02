"""DuckDB analytical store: raw (verbatim JSON) -> stg (typed) -> recon (reconstruction).

Faithfulness contract: the `raw` layer holds each API row as its *verbatim* JSON (loaded via
read_json_objects, so `0` stays `0` and `2.769` stays `2.769` — no type coercion, no dropped
or renamed columns). ALL interpretation (kWh->MWh, timestamp/date parsing, amendment
flattening, enum reading) happens exactly once in `stg`, where it is explicit and auditable.
"""
