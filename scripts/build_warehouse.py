#!/usr/bin/env python3
"""CLI: build the DuckDB warehouse from the immutable raw store, deterministically.

    python scripts/build_warehouse.py --db data/warehouse.duckdb --raw data/raw

Runs: load raw (verbatim JSON) -> sql/02_stg.sql -> sql/03_recon.sql -> sql/04_checks.sql.
Prints recon.checks. Exits nonzero ONLY if a `gate` check fails (pipeline/load integrity);
`observe` anomalies are printed and written to <db-dir>/warehouse_findings.json as candidate
deviations, but never fail the build.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from quadra.storage import db, load_raw, schema_survey  # noqa: E402

SQL_FILES = ["02_stg.sql", "03_recon.sql", "04_checks.sql"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the QUADRA DuckDB warehouse")
    ap.add_argument("--db", default="data/warehouse.duckdb")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--sql-dir", default="sql")
    # memory safety: the full feed-in load is 2.52M rows; bound DuckDB so it spills, not OOMs.
    ap.add_argument("--memory-limit", default="8GB", help="DuckDB memory_limit (default 8GB)")
    ap.add_argument("--threads", type=int, default=4, help="DuckDB threads (default 4)")
    ap.add_argument("--temp-dir", default=None, help="DuckDB spill dir (default <db-dir>/.tmp)")
    ap.add_argument("--batch-files", type=int, default=150,
                    help="raw-load file batch size (bounds peak memory)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    log = logging.getLogger("quadra.build")

    # Deterministic rebuild: start from a clean database file.
    dbp = pathlib.Path(args.db)
    dbp.parent.mkdir(parents=True, exist_ok=True)
    if dbp.exists():
        dbp.unlink()

    con = db.connect(args.db)
    temp_dir = args.temp_dir or str(dbp.parent / ".tmp")
    db.configure(con, memory_limit=args.memory_limit, threads=args.threads, temp_dir=temp_dir)
    log.info("duckdb config: memory_limit=%s threads=%d temp_dir=%s", args.memory_limit,
             args.threads, temp_dir)
    loaded = load_raw.load_all(con, args.raw, batch_files=args.batch_files)
    log.info("loaded raw tables: %s", ", ".join(loaded))
    for f in SQL_FILES:
        db.run_sql_file(con, pathlib.Path(args.sql_dir) / f)

    # Raw schema-drift survey over whatever raw is on disk (recorded, never fatal).
    schema_findings = schema_survey.survey(con)
    survey_rows = con.execute(
        "SELECT endpoint, n_rows, n_shapes, n_off_modal FROM recon.schema_survey ORDER BY endpoint"
    ).fetchall()
    print("\n=== raw schema survey (recon.schema_survey) ===")
    print(f"{'endpoint':18} {'rows':>10} {'shapes':>7} {'off_modal':>10}")
    for endpoint, n_rows, n_shapes, n_off in survey_rows:
        flag = "" if n_shapes == 1 else "  <- DRIFT"
        print(f"{endpoint:18} {n_rows:>10} {n_shapes:>7} {n_off:>10}{flag}")

    checks = con.execute(
        "SELECT check_name, severity, observed, ok, note FROM recon.checks ORDER BY severity, check_name"
    ).fetchall()

    print("\n=== recon.checks ===")
    print(f"{'check':32} {'sev':8} {'ok':5} {'observed':10} note")
    gate_failures, observe_findings = [], []
    for check, severity, observed, ok, note in checks:
        flag = "OK" if ok else "FAIL"
        print(f"{check:32} {severity:8} {flag:5} {str(observed):10} {note}")
        if not ok:
            (gate_failures if severity == "gate" else observe_findings).append(
                {"check": check, "severity": severity, "observed": observed, "note": note})

    observe_findings.extend(schema_findings)  # schema drift is a recorded observation too
    findings_path = dbp.parent / "warehouse_findings.json"
    findings_path.write_text(json.dumps(observe_findings, indent=2))
    print(f"\n{len(observe_findings)} observe finding(s) -> {findings_path}")
    if observe_findings:
        print("candidate-deviation findings (non-fatal):")
        for f in observe_findings:
            print("  -", json.dumps(f))

    if gate_failures:
        log.error("%d GATE check(s) failed — the build is broken:", len(gate_failures))
        for f in gate_failures:
            log.error("  %s", json.dumps(f))
        return 1
    log.info("all gate checks passed; warehouse built at %s", args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
