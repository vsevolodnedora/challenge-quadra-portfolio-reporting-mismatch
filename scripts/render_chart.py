#!/usr/bin/env python3
"""Render the one-chart project/solution summary: docs/solution_chart.{svg,png}.

    python scripts/render_chart.py [--db data/warehouse.duckdb] [--out docs]

Every figure on the chart is computed live from the DuckDB warehouse (itself a
deterministic rebuild from the immutable raw API extracts) and hard-asserted
against the values documented in DSF_SPEC.md before anything is drawn — the
chart cannot silently drift from the deliverable.

Layout: a top band with the raw sources (9 endpoints) and the published report
side by side, the reconstructed aggregation chain (7 business-logic steps)
spanning the full width below them with the three deviations pinned at the step
where each rule breaks, and a bottom band with one published-vs-correct panel
per deviation.
"""
from __future__ import annotations

import argparse
import pathlib

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyArrow, FancyBboxPatch
from matplotlib.ticker import MaxNLocator

# ------------------------------------------------------------------------------------ palette
SLATE = "#33475B"        # primary text / boxes
SLATE_SOFT = "#5B7186"   # secondary text
FILL = "#EDF1F5"         # node fill
EDGE = "#AFBECB"         # node edge
BAND = "#F7F9FB"         # band background
RED = "#C0392B"          # deviations only
PUB = "#A9B4BF"          # published (wrong) series
COR = "#2C6E8A"          # correct series

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


def box(ax, x, y, w, h, text, *, fs=11.5, fc=FILL, ec=EDGE, tc=SLATE, bold=False, lw=1.1):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.0035,rounding_size=0.006",
                                fc=fc, ec=ec, lw=lw, mutation_aspect=0.62))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc, fontweight="bold" if bold else "normal", linespacing=1.25)


def pin(ax, fig, x, y, label):
    w = 0.024
    h = w * fig.get_figwidth() / fig.get_figheight()
    ax.add_patch(Ellipse((x, y), w, h, fc=RED, ec="white", lw=1.2, zorder=6))
    ax.text(x, y, label, ha="center", va="center", fontsize=12.5, color="white",
            fontweight="bold", zorder=7)


def harrow(ax, x0, x1, y, *, color=EDGE, lw=1.2):
    ax.add_patch(FancyArrow(x0, y, x1 - x0, 0, width=0.0001, head_width=0.009,
                            head_length=0.0065, length_includes_head=True,
                            fc=color, ec=color, lw=lw))


def varrow(ax, y0, y1, x, *, color=EDGE, lw=1.2):
    ax.add_patch(FancyArrow(x, y0, 0, y1 - y0, width=0.0001, head_width=0.0075,
                            head_length=0.011, length_includes_head=True,
                            fc=color, ec=color, lw=lw))


def panel_header(fig, x, y, tag, title, sub):
    fig.text(x, y, tag, fontsize=16, color="white", fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.28", fc=RED, ec="none"))
    fig.text(x + 0.045, y, title, fontsize=18, color=SLATE, fontweight="bold", va="center_baseline")
    fig.text(x, y - 0.062, sub, fontsize=13.2, color=SLATE_SOFT, linespacing=1.5, va="center")


def style_axes(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(EDGE)
    ax.tick_params(colors=SLATE_SOFT, labelsize=13)


# ------------------------------------------------------------------------------------ chart
def render(v: dict, out_dir: pathlib.Path) -> list[pathlib.Path]:
    fig = plt.figure(figsize=(16.5, 10.1), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # -------- header
    fig.text(0.035, 0.952, "Jan-2025 Deckungsbeitrag — rebuilt to the cent, 3 deviations",
             fontsize=28.5, color=SLATE, fontweight="bold")
    fig.text(0.035, 0.918,
             "QUADRA DSF challenge · NEBELGARD portfolio, 847 assets · "
             "rebuilt from nine raw API endpoints · each deviation confirmed by a second source",
             fontsize=15.5, color=SLATE_SOFT)

    # -------- top band: sources + report side by side, chain across the full width below
    ax.add_patch(FancyBboxPatch((0.03, 0.405), 0.94, 0.497,
                                boxstyle="round,pad=0.004,rounding_size=0.008",
                                fc=BAND, ec="#E3E9EE", lw=0.8, mutation_aspect=0.62, zorder=0))
    fig.text(0.045, 0.868, "RAW SOURCES — 9 endpoints, rows ✓",
             fontsize=12.5, color=SLATE_SOFT, fontweight="bold")
    fig.text(0.835, 0.868, "PUBLISHED REPORT", fontsize=12.5, color=SLATE_SOFT, fontweight="bold")
    fig.text(0.13, 0.645, "AGGREGATION CHAIN  (reverse-engineered, per asset)",
             fontsize=12.5, color=SLATE_SOFT, fontweight="bold")

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
    ey0, eh, epitch, ew = 0.828, 0.026, 0.0325, 0.285
    for i, (name, sub) in enumerate(endpoints):
        col, row = divmod(i, 5)
        x = 0.045 + col * 0.307
        y = ey0 - row * epitch
        box(ax, x, y, ew, eh, "", fc="white")
        ax.text(x + 0.007, y + eh / 2, name, ha="left", va="center", fontsize=11,
                color=SLATE, fontweight="bold", family="monospace")
        ax.text(x + ew - 0.006, y + eh / 2, sub, ha="right", va="center", fontsize=10,
                color=SLATE_SOFT)

    steps = [
        ("1 Feed-in", "kWh→MWh · no raw\ncut at contract end"),
        ("2 Settlement", "sched×DA\n+ (act−sched)×ID"),
        ("3 EEG premium", "MWh × rate\n(8.40 / 6.20)"),
        ("4 Gross rev.", "settlement\n+ premium"),
        ("5 Mgmt fee", "fixed / rev-share\namendments!"),
        ("6 Alloc. costs", "4 pools ÷\nactive assets"),
        ("7 Corr + DB", "corr alloc ·\nDB identity"),
    ]
    sx0, sw, sgap, sy, sh = 0.045, 0.1177, 0.016, 0.505, 0.115
    centers = []
    for i, (t1, t2) in enumerate(steps):
        x = sx0 + i * (sw + sgap)
        centers.append(x + sw / 2)
        box(ax, x, sy, sw, sh, "", fc="white", ec=SLATE, lw=1.6)
        ax.text(x + sw / 2, sy + sh - 0.023, t1, ha="center", va="center",
                fontsize=13.5, color=SLATE, fontweight="bold")
        ax.text(x + sw / 2, sy + 0.036, t2, ha="center", va="center",
                fontsize=10.5, color=SLATE_SOFT, linespacing=1.35)
        if i:
            harrow(ax, x - sgap + 0.002, x - 0.002, sy + sh / 2, color=SLATE_SOFT)

    # sources ↓ into the chain; last chain step ↑ into the report
    varrow(ax, 0.692, 0.628, 0.085, color=SLATE_SOFT)
    varrow(ax, 0.624, 0.646, centers[6], color=SLATE_SOFT)

    # deviation pins on the breaking steps (5 -> D2, 6 -> D3, 7 -> D1)
    for idx, (tag, note) in {4: ("D2", "new model on\nwhole month"),
                             5: ("D3", "÷847, inactive\nas active"),
                             6: ("D1", "all wind, not\nnorth-only")}.items():
        pin(ax, fig, centers[idx], sy - 0.028, tag)
        ax.text(centers[idx], sy - 0.068, note, ha="center", va="center",
                fontsize=11, color=RED, linespacing=1.25, fontweight="bold")

    # report stack
    rx, rw = 0.835, 0.13
    box(ax, rx, 0.798, rw, 0.048, "847 asset rows", fs=12, fc="white")
    box(ax, rx, 0.742, rw, 0.048, "4 region rows", fs=12, fc="white")
    box(ax, rx, 0.652, rw, 0.082, f"portfolio\nDB {eur(v['pub_db'])} €\nactive count 847 ✗",
        fs=11.4, fc="white", ec=SLATE, lw=1.6)

    # -------- bottom band: three panels
    panel_header(fig, 0.055, 0.352, "D1", "EEG correction → north only",
                 f"{eur(v['d1_shift'])} € shifted off north · net 0 € at portfolio\n"
                 "note: “ausschließlich Windanlagen Region Nord”")
    p1 = fig.add_axes([0.072, 0.08, 0.233, 0.17])
    regions = ["north", "east", "south", "west"]
    pub1 = [v["d1_regions"][r] for r in regions]
    cor1 = [-18_400.0 if r == "north" else 0.0 for r in regions]
    xs = range(len(regions))
    p1.bar([x - 0.2 for x in xs], pub1, width=0.38, color=PUB, label="published (wrong)")
    p1.bar([x + 0.2 for x in xs], cor1, width=0.38, color=COR, label="correct")
    for x, val in zip(xs, pub1):
        p1.text(x - 0.2, val - 700, eur(val, 0), ha="center", va="top", fontsize=12, color=SLATE_SOFT)
    p1.text(0 + 0.2, cor1[0] - 700, eur(cor1[0], 0), ha="center", va="top", fontsize=13,
            color=COR, fontweight="bold")
    p1.set_xticks(list(xs), [r + " wind" if x == 0 else r for x, r in enumerate(regions)])
    p1.set_ylim(-22_300, 800)
    p1.axhline(0, color=EDGE, lw=0.8)
    p1.set_ylabel("EEG correction (€)", fontsize=14, color=SLATE_SOFT)
    p1.yaxis.set_major_locator(MaxNLocator(4))
    p1.legend(fontsize=13, frameon=False, loc="lower right")
    style_axes(p1)

    panel_header(fig, 0.395, 0.352, "D2", "Amended fee → split at Jan 15",
                 f"portfolio DB understated {eur(v['d2_net'])} €\n"
                 f"WND-0089 +{eur(v['d2_wnd'])} over · SOL-0214 −{eur(abs(v['d2_sol']))} under")
    p2 = fig.add_axes([0.400, 0.08, 0.245, 0.17])
    base = 2_310_000
    p2.bar([0], [v["pub_db"] - base], bottom=base, width=0.58, color=PUB)
    p2.bar([1], [v["d2_net"]], bottom=v["pub_db"], width=0.58, color=COR, alpha=0.55)
    p2.bar([2], [v["corr_db"] - base], bottom=base, width=0.58, color=COR)
    p2.plot([-0.29, 1.29], [v["pub_db"]] * 2, color=SLATE_SOFT, lw=0.8, ls=":")
    p2.plot([0.71, 2.29], [v["corr_db"]] * 2, color=SLATE_SOFT, lw=0.8, ls=":")
    p2.text(0, v["pub_db"] + 400, eur(v["pub_db"]), ha="center", fontsize=12.5, color=SLATE)
    p2.text(1, v["corr_db"] + 400, f"+{eur(v['d2_net'])}", ha="center", fontsize=12.5,
            color=COR, fontweight="bold")
    p2.text(2, v["corr_db"] + 400, eur(v["corr_db"]), ha="center", fontsize=12.5,
            color=COR, fontweight="bold")
    p2.set_xticks([0, 1, 2], ["published DB", "fee over-charge\nreversed", "correct DB"])
    p2.set_ylim(base, 2_326_500)
    p2.set_ylabel("portfolio DB (€)", fontsize=14, color=SLATE_SOFT)
    p2.yaxis.set_major_formatter(lambda x, _: f"{x / 1e6:.3f} M")
    p2.yaxis.set_major_locator(MaxNLocator(4))
    style_axes(p2)

    panel_header(fig, 0.72, 0.352, "D3", "12 inactive as active",
                 f"847 → {v['active']} · {eur(v['d3_shift'])} € moves to the active\n"
                 f"grid fees ({eur(v['d3_grid'])} €, per-asset) correctly stay")
    p3 = fig.add_axes([0.72, 0.08, 0.245, 0.17])
    cats = list(v["d3_cats"])
    pub3 = [v["d3_cats"][c] for c in cats]
    cor3 = [0.0, 0.0, 0.0, v["d3_grid"]]
    xs3 = range(len(cats))
    p3.bar([x - 0.2 for x in xs3], pub3, width=0.38, color=PUB, label="on 12 inactive (published)")
    p3.bar([x + 0.2 for x in xs3], cor3, width=0.38, color=COR, label="on 12 inactive (correct)")
    for x, val in zip(xs3, pub3):
        p3.text(x - 0.2, val + 25, eur(val, 0), ha="center", fontsize=12, color=SLATE_SOFT)
    p3.text(3 + 0.27, cor3[3] + 25, eur(cor3[3], 0), ha="center", fontsize=13,
            color=COR, fontweight="bold")
    p3.set_xticks(list(xs3), cats)
    p3.tick_params(axis="x", labelsize=12)
    p3.set_ylim(0, 1_250)
    p3.set_ylabel("cost on inactive (€)", fontsize=14, color=SLATE_SOFT)
    p3.yaxis.set_major_locator(MaxNLocator(4))
    p3.legend(fontsize=12, frameon=False, loc="upper left")
    style_axes(p3)

    # -------- footer
    fig.text(0.035, 0.013,
             "All figures computed live from the DuckDB warehouse and asserted against DSF_SPEC.md "
             "before rendering · synthetic challenge data (QUADRA / NEBELGARD, fictional)",
             fontsize=11, color=SLATE_SOFT)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext, kw in (("svg", {}), ("png", {"dpi": 190})):
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
