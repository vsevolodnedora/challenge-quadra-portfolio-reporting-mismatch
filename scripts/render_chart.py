#!/usr/bin/env python3
"""Render the one-chart project/solution summary: docs/solution_chart.{svg,png}.

    python scripts/render_chart.py [--db data/warehouse.duckdb] [--out docs]

Every figure on the chart is computed live from the DuckDB warehouse (itself a
deterministic rebuild from the immutable raw API extracts) and hard-asserted
against the values documented in DSF_SPEC.md before anything is drawn — the
chart cannot silently drift from the deliverable.

Layout: a top band showing the reconstructed aggregation pipeline (9 endpoints
-> 7 business-logic steps -> published report) with the three deviations pinned
at the step where each rule breaks, and a bottom band with one published-vs-
correct panel per deviation.
"""
from __future__ import annotations

import argparse
import pathlib

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyArrow, FancyBboxPatch

# ------------------------------------------------------------------------------------ palette
SLATE = "#33475B"        # primary text / boxes
SLATE_SOFT = "#5B7186"   # secondary text
FILL = "#EDF1F5"         # node fill
EDGE = "#AFBECB"         # node edge
BAND = "#F7F9FB"         # band background
RED = "#C0392B"          # deviations only
PUB = "#A9B4BF"          # published (wrong) series
COR = "#2C6E8A"          # correct series
OKG = "#2E7D5B"          # fidelity-gate green

DOC = {  # constants documented in DSF_SPEC.md — the chart must reproduce these exactly
    "pub_db": 2_316_102.80,
    "corr_db": 2_323_122.23,
    "d1_shift": 6_432.51,
    "d1_north_pub": -11_967.49,
    "d2_net": 7_019.43,
    "d2_wnd": 7_161.93,
    "d2_sol": -142.50,
    "d3_shift": 893.74,
    "active": 835,
    "inactive": 12,
}

EXPECTED_ROWS = {
    "feedin": 2_520_672, "schedule": 626_472, "prices": 1_488, "contracts": 847,
    "assets": 847, "eeg_premium": 2, "costs": 3_388, "costs_metadata": 1,
    "report_asset": 847, "report_region": 4, "report_portfolio": 1,
}


# ------------------------------------------------------------------------------------ data
def load_values(db_path: str) -> dict:
    con = duckdb.connect(db_path, read_only=True)
    v: dict = {}

    for t, exp in EXPECTED_ROWS.items():
        n = con.sql(f"SELECT COUNT(*) FROM raw.{t}").fetchone()[0]
        assert n == exp, f"raw.{t}: {n} rows, expected {exp}"
        v[f"rows_{t}"] = n

    v["pub_db"], v["active_count_pub"] = con.sql(
        "SELECT total_db_eur, active_asset_count FROM stg.report_portfolio").fetchone()
    v["inactive"] = con.sql(
        "SELECT COUNT(*) FROM stg.assets WHERE status='inactive'").fetchone()[0]
    v["active"] = 847 - v["inactive"]

    # D1 — published region corrections vs correct (north only)
    v["d1_regions"] = dict(con.sql(
        "SELECT region, eeg_correction_eur FROM stg.report_region").fetchall())
    corr_total = round(sum(v["d1_regions"].values()), 2)
    assert corr_total == -18_400.00, corr_total
    v["d1_north_pub"] = round(v["d1_regions"]["north"], 2)
    v["d1_shift"] = round(v["d1_north_pub"] - (-18_400.00), 2)  # magnitude shifted off north

    # D2 — correct time-split fee vs published (live recomputation)
    d2 = con.sql("""
        WITH fk AS (
          SELECT f.asset_id, f.date, f.hour, SUM(f.energy_mwh) AS mwh
          FROM stg.feedin_qh f
          WHERE f.asset_id IN ('WND-0089','SOL-0214')
            AND f.quality_flag IN ('validated','estimated')
          GROUP BY 1,2,3),
        hourly AS (
          SELECT fk.asset_id, fk.date, fk.mwh,
                 COALESCE(s.scheduled_mwh,0)*p.da_price_eur_mwh
                   + (fk.mwh-COALESCE(s.scheduled_mwh,0))*p.id_price_eur_mwh AS energy_eur
          FROM fk
          LEFT JOIN stg.schedule s USING (asset_id, date, hour)
          JOIN stg.prices_hourly p ON p.date=fk.date AND p.hour=fk.hour),
        agg AS (
          SELECT h.asset_id,
            SUM(h.mwh)        FILTER (h.date <  DATE '2025-01-15') AS mwh_pre,
            SUM(h.mwh)        FILTER (h.date >= DATE '2025-01-15') AS mwh_post,
            SUM(h.energy_eur) FILTER (h.date >= DATE '2025-01-15') AS eur_post
          FROM hourly h GROUP BY 1)
        SELECT a.asset_id,
               r.management_fee_eur AS published,
               ROUND(a.mwh_pre*c.fee_fixed_eur_mwh
                 + (a.eur_post + a.mwh_post*(CASE WHEN c.technology='wind' THEN 8.4 ELSE 6.2 END))
                   * c.amd_fee_share_pct/100, 2) AS correct
        FROM agg a JOIN stg.contracts c USING (asset_id)
        JOIN stg.report_asset r USING (asset_id)""").fetchall()
    v["d2"] = {a: (pub, cor) for a, pub, cor in d2}
    v["d2_wnd"] = round(v["d2"]["WND-0089"][0] - v["d2"]["WND-0089"][1], 2)
    v["d2_sol"] = round(v["d2"]["SOL-0214"][0] - v["d2"]["SOL-0214"][1], 2)
    v["d2_net"] = round(v["d2_wnd"] + v["d2_sol"], 2)
    v["corr_db"] = round(v["pub_db"] + v["d2_net"], 2)

    # D3 — pooled cost sitting on the 12 inactive, full precision by category
    mon, ins, dat, grd = con.sql("""
        WITH cap AS (
          SELECT SUM(installed_capacity_kw) AS c_all,
                 SUM(installed_capacity_kw) FILTER (is_inactive) AS c_inact
          FROM stg.assets)
        SELECT ROUND(12*10587.5/847, 2),
               ROUND(31400.0*c_inact/c_all, 2),
               ROUND(18200.0*c_inact/c_all, 2),
               (SELECT ROUND(SUM(c.allocated_amount_eur), 2)
                FROM stg.costs c JOIN stg.assets a USING (asset_id)
                WHERE a.is_inactive AND c.cost_category='grid_fees')
        FROM cap""").fetchone()
    v["d3_cats"] = {"monitoring\n(flat)": mon, "insurance\n(cap-wt)": ins,
                    "data fees\n(cap-wt)": dat, "grid fees\n(per-asset)": grd}
    v["d3_shift"] = round(mon + ins + dat, 2)
    v["d3_grid"] = grd

    # fidelity gate — reconstruction deltas vs the published report
    v["max_db_delta"] = con.sql(
        "SELECT MAX(ABS(db_delta)) FROM recon.asset_delta").fetchone()[0]
    n_bad = con.sql(
        "SELECT COUNT(*) FROM recon.asset_delta WHERE ABS(db_delta) > 0.02").fetchone()[0]
    assert n_bad == 0, f"{n_bad} assets exceed the DB reproduction tolerance"

    # hard accuracy gate: live values must equal the documented deliverable
    for key, doc in DOC.items():
        got = v[key]
        assert abs(got - doc) <= 0.005, f"{key}: live {got} != documented {doc}"
    con.close()
    return v


# ------------------------------------------------------------------------------------ helpers
def eur(x: float, dp: int = 2) -> str:
    return f"{x:,.{dp}f}"


def box(ax, x, y, w, h, text, *, fs=8.0, fc=FILL, ec=EDGE, tc=SLATE, bold=False, lw=0.9):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.0035,rounding_size=0.006",
                                fc=fc, ec=ec, lw=lw, mutation_aspect=0.62))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc, fontweight="bold" if bold else "normal", linespacing=1.25)


def pin(ax, fig, x, y, label):
    w = 0.0155
    h = w * fig.get_figwidth() / fig.get_figheight()
    ax.add_patch(Ellipse((x, y), w, h, fc=RED, ec="white", lw=1.1, zorder=6))
    ax.text(x, y, label, ha="center", va="center", fontsize=7.6, color="white",
            fontweight="bold", zorder=7)


def harrow(ax, x0, x1, y, *, color=EDGE, lw=1.2):
    ax.add_patch(FancyArrow(x0, y, x1 - x0, 0, width=0.0001, head_width=0.0075,
                            head_length=0.0055, length_includes_head=True,
                            fc=color, ec=color, lw=lw))


def panel_header(fig, x, y, tag, title, sub):
    fig.text(x, y, tag, fontsize=9.5, color="white", fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.28", fc=RED, ec="none"))
    fig.text(x + 0.030, y, title, fontsize=9.8, color=SLATE, fontweight="bold", va="center_baseline")
    fig.text(x, y - 0.048, sub, fontsize=8.2, color=SLATE_SOFT, linespacing=1.45, va="center")


def style_axes(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(EDGE)
    ax.tick_params(colors=SLATE_SOFT, labelsize=7.6)


# ------------------------------------------------------------------------------------ chart
def render(v: dict, out_dir: pathlib.Path) -> list[pathlib.Path]:
    fig = plt.figure(figsize=(15, 9.2), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # -------- header
    fig.text(0.035, 0.962, "NEBELGARD Jan-2025 Deckungsbeitrag — reconstruction and the three deviations",
             fontsize=16.5, color=SLATE, fontweight="bold")
    fig.text(0.035, 0.936,
             "QUADRA DSF challenge · published report reproduced to the cent from nine raw API sources · "
             "exactly three rule-level deviations, each cross-referencing ≥ 2 sources",
             fontsize=10, color=SLATE_SOFT)

    # -------- top band: pipeline
    ax.add_patch(FancyBboxPatch((0.03, 0.555), 0.94, 0.355,
                                boxstyle="round,pad=0.004,rounding_size=0.008",
                                fc=BAND, ec="#E3E9EE", lw=0.8, mutation_aspect=0.62, zorder=0))
    fig.text(0.045, 0.878, "RAW SOURCES — 9 endpoints, rows = meta.total_records ✓",
             fontsize=7.8, color=SLATE_SOFT, fontweight="bold")
    fig.text(0.40, 0.878, "AGGREGATION CHAIN  (reverse-engineered, per asset)",
             fontsize=7.8, color=SLATE_SOFT, fontweight="bold")
    fig.text(0.845, 0.878, "PUBLISHED REPORT", fontsize=7.8, color=SLATE_SOFT, fontweight="bold")

    endpoints = [
        ("/data/feedin", f"{v['rows_feedin']:,} qh rows (kWh)"),
        ("/data/schedule", f"{v['rows_schedule']:,} rows"),
        ("/data/prices", f"{v['rows_prices']:,} rows (DA+ID)"),
        ("/data/contracts", f"{v['rows_contracts']} rows · 2 amendments"),
        ("/assets", f"{v['rows_assets']} rows · {v['inactive']} inactive"),
        ("/data/eeg_premium", f"{v['rows_eeg_premium']} rows · corr −18,400 €"),
        ("/data/costs", f"{v['rows_costs']:,} rows · 4 pools"),
        ("/data/costs/metadata", "1 row · scope note"),
        ("/report/monthly", "847 + 4 + 1 rows"),
    ]
    ey0, eh, egap = 0.842, 0.0225, 0.0048
    for i, (name, sub) in enumerate(endpoints):
        y = ey0 - i * (eh + egap)
        box(ax, 0.045, y, 0.20, eh, "", fc="white")
        ax.text(0.052, y + eh / 2, name, ha="left", va="center", fontsize=7.7,
                color=SLATE, fontweight="bold", family="monospace")
        ax.text(0.238, y + eh / 2, sub, ha="right", va="center", fontsize=6.9, color=SLATE_SOFT)

    steps = [
        ("1  Feed-in", "kWh→MWh · excl. raw\ntrunc. at contract end"),
        ("2  Settlement", "sched×DA\n+ (act−sched)×ID"),
        ("3  EEG premium", "MWh × rate\n(8.40 / 6.20)"),
        ("4  Gross revenue", "settlement\n+ premium"),
        ("5  Mgmt fee", "fixed / rev-share\namendments!"),
        ("6  Alloc. costs", "4 pools ÷\nactive assets"),
        ("7  EEG corr + DB", "corr alloc ·\nDB identity"),
    ]
    sx0, sw, sgap, sy, sh = 0.285, 0.0655, 0.0095, 0.715, 0.085
    centers = []
    for i, (t1, t2) in enumerate(steps):
        x = sx0 + i * (sw + sgap)
        centers.append(x + sw / 2)
        box(ax, x, sy, sw, sh, "", fc="white", ec=SLATE, lw=1.0)
        ax.text(x + sw / 2, sy + sh - 0.017, t1, ha="center", va="center",
                fontsize=8.0, color=SLATE, fontweight="bold")
        ax.text(x + sw / 2, sy + 0.028, t2, ha="center", va="center",
                fontsize=6.7, color=SLATE_SOFT, linespacing=1.3)
        if i:
            harrow(ax, x - sgap + 0.001, x - 0.001, sy + sh / 2, color=SLATE_SOFT)

    # sources -> chain, chain -> report
    harrow(ax, 0.252, sx0 - 0.004, sy + sh / 2, color=SLATE_SOFT, lw=1.4)
    harrow(ax, sx0 + 7 * sw + 6 * sgap + 0.004, 0.845, sy + sh / 2, color=SLATE_SOFT, lw=1.4)

    # deviation pins on the breaking steps (5 -> D2, 6 -> D3, 7 -> D1)
    for idx, (tag, note) in {4: ("D2", "new model applied\nto whole month"),
                             5: ("D3", "÷ 847 — inactive\ncounted as active"),
                             6: ("D1", "spread over all wind,\nnot north-only")}.items():
        pin(ax, fig, centers[idx], sy - 0.020, tag)
        ax.text(centers[idx], sy - 0.048, note, ha="center", va="center",
                fontsize=6.6, color=RED, linespacing=1.25)

    # report stack
    rx, rw = 0.850, 0.115
    box(ax, rx, 0.783, rw, 0.045, "847 asset rows", fs=7.8, fc="white")
    box(ax, rx, 0.730, rw, 0.045, "4 region rows", fs=7.8, fc="white")
    box(ax, rx, 0.648, rw, 0.074, f"portfolio\nDB {eur(v['pub_db'])} €\nactive count 847 ✗",
        fs=7.4, fc="white", ec=SLATE, lw=1.0)

    # fidelity gate strip
    fig.text(0.045, 0.575,
             "✓ FIDELITY GATE   the clean rebuild reproduces every published column for all 847 assets "
             f"(max |Δ Deckungsbeitrag| = {v['max_db_delta']:.2f} €) — the three deviations are rule-level, "
             "not parsing or rounding artifacts",
             fontsize=8.6, color=OKG, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#D8E5DD", lw=0.8))

    # -------- bottom band: three panels
    panel_header(fig, 0.055, 0.475, "D1", "EEG correction: all wind  →  north wind only",
                 f"{eur(v['d1_shift'])} € shifted off north · net 0 € at portfolio\n"
                 "note: “ausschließlich Windanlagen Region Nord”")
    p1 = fig.add_axes([0.055, 0.095, 0.25, 0.315])
    regions = ["north", "east", "south", "west"]
    pub1 = [v["d1_regions"][r] for r in regions]
    cor1 = [-18_400.0 if r == "north" else 0.0 for r in regions]
    xs = range(len(regions))
    p1.bar([x - 0.19 for x in xs], pub1, width=0.36, color=PUB, label="published (wrong)")
    p1.bar([x + 0.19 for x in xs], cor1, width=0.36, color=COR, label="correct")
    for x, val in zip(xs, pub1):
        p1.text(x - 0.19, val - 600, eur(val, 0), ha="center", va="top", fontsize=6.8, color=SLATE_SOFT)
    p1.text(0 + 0.19, cor1[0] - 600, eur(cor1[0], 0), ha="center", va="top", fontsize=6.8,
            color=COR, fontweight="bold")
    p1.set_xticks(list(xs), [r + " wind" for r in regions])
    p1.set_ylim(-21_500, 800)
    p1.axhline(0, color=EDGE, lw=0.8)
    p1.set_ylabel("EEG correction (€)", fontsize=7.8, color=SLATE_SOFT)
    p1.legend(fontsize=7.2, frameon=False, loc="lower right")
    style_axes(p1)

    panel_header(fig, 0.395, 0.475, "D2", "Amended fee: whole month  →  time-split at Jan 15",
                 f"portfolio DB understated {eur(v['d2_net'])} €\n"
                 f"WND-0089 +{eur(v['d2_wnd'])} over · SOL-0214 −{eur(abs(v['d2_sol']))} under")
    p2 = fig.add_axes([0.395, 0.095, 0.25, 0.315])
    base = 2_310_000
    p2.bar([0], [v["pub_db"] - base], bottom=base, width=0.55, color=PUB)
    p2.bar([1], [v["d2_net"]], bottom=v["pub_db"], width=0.55, color=COR, alpha=0.55)
    p2.bar([2], [v["corr_db"] - base], bottom=base, width=0.55, color=COR)
    p2.plot([-0.28, 1.28], [v["pub_db"]] * 2, color=SLATE_SOFT, lw=0.7, ls=":")
    p2.plot([0.72, 2.28], [v["corr_db"]] * 2, color=SLATE_SOFT, lw=0.7, ls=":")
    p2.text(0, v["pub_db"] + 350, eur(v["pub_db"]), ha="center", fontsize=7.4, color=SLATE)
    p2.text(1, v["corr_db"] + 350, f"+{eur(v['d2_net'])}", ha="center", fontsize=7.4,
            color=COR, fontweight="bold")
    p2.text(2, v["corr_db"] + 350, eur(v["corr_db"]), ha="center", fontsize=7.4,
            color=COR, fontweight="bold")
    p2.set_xticks([0, 1, 2], ["published DB", "fee over-charge\nreversed", "correct DB"])
    p2.set_ylim(base, 2_326_500)
    p2.set_ylabel("portfolio Deckungsbeitrag (€)", fontsize=7.8, color=SLATE_SOFT)
    p2.yaxis.set_major_formatter(lambda x, _: f"{x / 1e6:.3f} M")
    style_axes(p2)

    panel_header(fig, 0.72, 0.475, "D3", "Inactive counted as active: 847  →  835",
                 f"{eur(v['d3_shift'])} € pooled cost moves to the {v['active']} active\n"
                 f"grid fees ({eur(v['d3_grid'])} €, per-asset) correctly stay")
    p3 = fig.add_axes([0.72, 0.095, 0.245, 0.315])
    cats = list(v["d3_cats"])
    pub3 = [v["d3_cats"][c] for c in cats]
    cor3 = [0.0, 0.0, 0.0, v["d3_grid"]]
    xs3 = range(len(cats))
    p3.bar([x - 0.19 for x in xs3], pub3, width=0.36, color=PUB, label="on 12 inactive (published)")
    p3.bar([x + 0.19 for x in xs3], cor3, width=0.36, color=COR, label="on 12 inactive (correct)")
    for x, val in zip(xs3, pub3):
        p3.text(x - 0.19, val + 18, eur(val, 0), ha="center", fontsize=6.8, color=SLATE_SOFT)
    p3.text(3 + 0.19, cor3[3] + 18, eur(cor3[3], 0), ha="center", fontsize=6.8,
            color=COR, fontweight="bold")
    p3.set_xticks(list(xs3), cats)
    p3.tick_params(axis="x", labelsize=6.9)
    p3.set_ylim(0, 1_150)
    p3.set_ylabel("cost carried by inactive (€)", fontsize=7.8, color=SLATE_SOFT)
    p3.legend(fontsize=7.0, frameon=False, loc="upper left")
    style_axes(p3)

    # -------- footer
    fig.text(0.035, 0.022,
             "All figures computed live from data/warehouse.duckdb (deterministic rebuild from immutable raw "
             "API extracts) and asserted against DSF_SPEC.md before rendering · scripts/render_chart.py · "
             "synthetic challenge data (QUADRA / NEBELGARD, fictional)",
             fontsize=7.6, color=SLATE_SOFT)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext, kw in (("svg", {}), ("png", {"dpi": 170})):
        p = out_dir / f"solution_chart.{ext}"
        fig.savefig(p, facecolor="white", **kw)
        paths.append(p)
    plt.close(fig)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/warehouse.duckdb")
    ap.add_argument("--out", default="docs")
    args = ap.parse_args()
    v = load_values(args.db)
    for p in render(v, pathlib.Path(args.out)):
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
