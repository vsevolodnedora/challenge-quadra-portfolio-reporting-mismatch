"""Storage-layer tests: build the warehouse from the on-disk raw store and assert the
pipeline gates hold, faithful verbatim load is preserved, and the verified structural facts
survive normalization. Skips cleanly if no raw extract is present.
"""
from __future__ import annotations

import pathlib

import pytest

from quadra.storage import db, load_raw

REPO = pathlib.Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw"
SQL = REPO / "sql"


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    if not (RAW / "feedin").exists():
        pytest.skip("no raw extract present (run scripts/extract.py --phase trace first)")
    wh = tmp_path_factory.mktemp("wh")
    conn = db.connect(str(wh / "warehouse.duckdb"))
    # data/raw may now be the full 2.52M-row extract; bound memory so the test can't OOM the box.
    db.configure(conn, memory_limit="6GB", threads=4, temp_dir=str(wh / ".tmp"))
    load_raw.load_all(conn, str(RAW))
    for f in ["02_stg.sql", "03_recon.sql", "04_checks.sql"]:
        db.run_sql_file(conn, SQL / f)
    return conn


def _checks(conn):
    return {r[0]: {"severity": r[1], "observed": r[2], "ok": r[3], "note": r[4]}
            for r in conn.execute("SELECT check_name, severity, observed, ok, note FROM recon.checks").fetchall()}


def test_all_gate_checks_pass(con):
    failed = [name for name, c in _checks(con).items() if c["severity"] == "gate" and not c["ok"]]
    assert not failed, f"gate checks failed: {failed}"


def test_feedin_grain_is_complete_multiple_of_2976(con):
    n = con.execute("SELECT count(*) FROM stg.feedin_qh").fetchone()[0]
    assert n > 0 and n % 2976 == 0, f"feed-in rows {n} not a whole number of 2976-row assets"


def test_report_db_identity_holds(con):
    # deckungsbeitrag == gross + eeg_correction - management_fee - allocated_costs (confirmed live)
    viol = con.execute(
        "SELECT count(*) FROM stg.report_asset "
        "WHERE abs(deckungsbeitrag_eur - (gross_revenue_eur + eeg_correction_eur "
        "      - management_fee_eur - allocated_costs_eur)) > 0.005"
    ).fetchone()[0]
    assert viol == 0


def test_raw_is_verbatim_json(con):
    # SOL-0032's first quarter-hour is the integer 0 in the source; it must NOT have been
    # coerced to 0.0 at the raw layer (read_json_objects keeps literals byte-exact).
    lit = con.execute(
        "SELECT json_extract(_record, '$.energy_kwh') FROM raw.feedin "
        "WHERE json_extract_string(_record,'$.asset_id')='SOL-0032' "
        "  AND json_extract_string(_record,'$.timestamp')='2025-01-01T00:00:00'"
    ).fetchone()[0]
    assert str(lit) == "0", f"expected verbatim integer 0, got {lit!r}"


def test_schedule_inactive_boundary(con):
    # SOL-0032 contract ends 2025-01-26 -> schedule covers 26 days x 24h = 624 rows (feed-in is full).
    rows = con.execute("SELECT count(*) FROM stg.schedule WHERE asset_id='SOL-0032'").fetchone()[0]
    if rows == 0:
        pytest.skip("SOL-0032 not in this extract")
    assert rows == 624


def test_recon_reproduces_report(con):
    # The pinned reconstruction (report rules, F-023..F-028) must reproduce every published
    # deckungsbeitrag to the cent; residuals are sub-cent rounding only.
    worst = con.execute("SELECT max(abs(db_delta)) FROM recon.asset_delta").fetchone()[0]
    assert worst is not None and worst <= 0.02, f"recon DB drifted from report by {worst}"


def test_three_deviations_quantified(con):
    # Deviation 1 (EEG correction): 6,432.51 EUR shifted off north wind.
    north = con.execute(
        "SELECT misallocated_eur FROM recon.deviation_1_eeg WHERE region='north'"
    ).fetchone()[0]
    assert abs(north - 6432.51) < 0.01
    # Deviation 2 (amendment fee): WND-0089 over-charged by 7,161.93 EUR under whole-month rule.
    oc = con.execute(
        "SELECT overcharge_eur FROM recon.deviation_2_fee WHERE asset_id='WND-0089'"
    ).fetchone()[0]
    assert abs(oc - 7161.93) < 0.01
    # Deviation 3 (inactive-as-active): report counts 847 active, truth is 835.
    row = con.execute(
        "SELECT report_active_count, correct_active_count FROM recon.deviation_3_inactive"
    ).fetchone()
    assert row == (847, 835)


def test_kwh_to_mwh_applied_once(con):
    # A known WND-0042 t0 value 2.769 kWh -> 0.002769 MWh.
    mwh = con.execute(
        "SELECT energy_mwh FROM stg.feedin_qh WHERE asset_id='WND-0042' "
        "AND ts = TIMESTAMP '2025-01-01 00:00:00'"
    ).fetchone()
    if mwh is None:
        pytest.skip("WND-0042 not in this extract")
    assert abs(mwh[0] - 0.002769) < 1e-12
