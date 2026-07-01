"""Load the immutable raw JSON into `raw.*` tables, VERBATIM and within bounded memory.

Each row is stored as its exact source JSON (`read_json_objects` does no type coercion), so
nothing is dropped, renamed, converted, or assumed at this layer. Provenance is the source
filename; the on-disk `_manifest.jsonl` holds the rest. All typing happens in `stg` (sql/02).

Memory: the heavy surfaces (feed-in ~5k files / 2.52M rows, schedule ~1.6k files / 626k rows)
are loaded in FILE BATCHES — CREATE from the first batch, then INSERT the rest — so peak memory
stays proportional to one batch, never the whole 2.52M-row explode. Loading everything in a
single `read_json_objects(...)` over the full glob is what previously OOM-killed the build.
"""
from __future__ import annotations

import logging
import pathlib

log = logging.getLogger("quadra.storage.load_raw")

RAW_TABLES = [
    "assets", "contracts", "feedin", "schedule", "prices", "eeg_premium",
    "costs", "costs_metadata", "report_asset", "report_region", "report_portfolio",
]

DEFAULT_BATCH_FILES = 150  # ~75k feed-in rows per statement at page_size=500


def _file_list_literal(files) -> str:
    # paths are plain (data/raw/<ep>/<partition>/page-NNNN.json); escape quotes defensively
    return "[" + ", ".join("'" + str(f).replace("'", "''") + "'" for f in files) + "]"


def _load_select(files) -> str:
    return f"""
        SELECT je.value AS _record, d.filename AS _src_file
        FROM read_json_objects({_file_list_literal(files)}, filename := true) AS d,
             LATERAL unnest(CAST(json_extract(d.json, '$.data') AS JSON[])) AS je(value)
    """


def load_all(con, raw_root: str, *, batch_files: int = DEFAULT_BATCH_FILES) -> list[str]:
    con.execute("CREATE SCHEMA IF NOT EXISTS raw;")
    loaded: list[str] = []
    root = pathlib.Path(raw_root)
    for table in RAW_TABLES:
        # sorted for deterministic, reproducible loads
        pages = sorted(root.glob(f"{table}/**/page-*.json"))
        if not pages:
            log.warning("no raw pages for %s under %s — skipping", table, raw_root)
            continue
        batches = [pages[i:i + batch_files] for i in range(0, len(pages), batch_files)]
        con.execute(f"CREATE OR REPLACE TABLE raw.{table} AS {_load_select(batches[0])};")
        for bi, batch in enumerate(batches[1:], start=2):
            con.execute(f"INSERT INTO raw.{table} {_load_select(batch)};")
            if bi % 10 == 0:
                log.info("  raw.%s: %d/%d batches", table, bi, len(batches))
        n = con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]
        log.info("raw.%s: %d rows from %d file(s) in %d batch(es)", table, n, len(pages), len(batches))
        loaded.append(table)
    return loaded
