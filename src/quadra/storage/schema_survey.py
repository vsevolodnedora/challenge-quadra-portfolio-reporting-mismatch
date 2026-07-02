"""Raw schema-drift survey: fingerprint every raw.* table by its rows' top-level key-sets.

The raw layer stores each source row as verbatim JSON (`_record`), and `stg` (sql/02) reads
named paths with `json_extract*` — so a row with a missing / renamed / extra key silently
becomes NULL downstream. This survey makes that visible instead: for each endpoint it records
the distinct top-level key shapes, the modal (most common) shape, and how many rows deviate
from it. It NEVER fails the build — a deviating shape may itself be a report deviation; it is a
recorded observation, computed over whatever raw is on disk (trace or the full pull).
"""
from __future__ import annotations

import json
import logging

from . import load_raw

log = logging.getLogger("quadra.storage.schema_survey")


def _existing_raw_tables(con) -> list[str]:
    present = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='raw'").fetchall()}
    return [t for t in load_raw.RAW_TABLES if t in present]


def survey(con) -> list[dict]:
    """Build recon.schema_survey and return one dict per endpoint that has >1 key shape."""
    con.execute("CREATE SCHEMA IF NOT EXISTS recon;")
    con.execute("""
        CREATE OR REPLACE TABLE recon.schema_survey (
            endpoint         VARCHAR,
            n_rows           BIGINT,
            n_shapes         BIGINT,
            modal_keys       VARCHAR,
            n_off_modal      BIGINT,
            off_modal_shapes VARCHAR
        );
    """)

    findings: list[dict] = []
    for table in _existing_raw_tables(con):
        # Canonical shape = the sorted set of top-level JSON keys of each row.
        shapes = con.execute(f"""
            WITH ks AS (
                SELECT array_to_string(list_sort(json_keys(_record)), ',') AS keyset
                FROM raw.{table}
            )
            SELECT keyset, count(*) AS n FROM ks GROUP BY keyset ORDER BY n DESC, keyset
        """).fetchall()
        if not shapes:
            continue
        n_rows = sum(n for _, n in shapes)
        modal_keys, modal_n = shapes[0]
        off = [(ks, n) for ks, n in shapes[1:]]
        n_off_modal = sum(n for _, n in off)
        off_txt = json.dumps({ks: n for ks, n in off}) if off else ""

        con.execute(
            "INSERT INTO recon.schema_survey VALUES (?,?,?,?,?,?)",
            [table, n_rows, len(shapes), modal_keys, n_off_modal, off_txt],
        )
        if len(shapes) > 1:
            finding = {
                "check": "schema_shape_drift", "severity": "observe", "endpoint": table,
                "observed": f"{len(shapes)} shapes, {n_off_modal}/{n_rows} rows off modal",
                "modal_keys": modal_keys, "off_modal_shapes": off_txt,
            }
            findings.append(finding)
            log.warning("schema drift in raw.%s: %d shapes; off-modal rows=%d; deviations=%s",
                        table, len(shapes), n_off_modal, off_txt)
    return findings
