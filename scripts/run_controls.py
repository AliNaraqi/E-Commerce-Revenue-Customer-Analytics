"""Controlled tests of the two claims the headline analysis could not settle.

Usage:
    python scripts/build_distance.py   # first — creates order_distance
    python scripts/run_controls.py     # -> outputs/tables, outputs/figures, controls.json

CLAIM 1 — "Rio de Janeiro is slow for reasons other than distance."
    Originally asserted from average delivery days alone, which is not
    evidence. Tested here by comparing RJ against other states *within*
    distance bands, and then against MG holding both seller origin (São Paulo)
    and distance band constant.

CLAIM 2 — "Late delivery is associated with a ~2.0-point drop in review score."
    Originally reported with a blanket caveat that state, category, price and
    seller were uncontrolled. Tested here by stratification and by OLS with
    state, category, month and seller fixed effects.

WHAT THIS STILL IS NOT
    Observational. Controls remove *observed* confounders; they cannot rule out
    an unobserved one (e.g. a seller's bad batch that ships late AND arrives
    defective). Reverse causation is implausible — a review cannot retro-date a
    delivery — which is why the result is reported as robust rather than merely
    suggestive. It is still not a randomised effect estimate.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

import viz_theme as t

ROOT = Path(__file__).resolve().parent.parent
TABLES = ROOT / "outputs" / "tables"
FIGURES = ROOT / "outputs" / "figures"

BANDS = [0, 100, 300, 600, 1000, 1800, 10_000]
BAND_LABELS = ["<100km", "100–300", "300–600", "600–1000", "1000–1800", "1800km+"]


def save(df: pd.DataFrame, name: str) -> pd.DataFrame:
    TABLES.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLES / f"{name}.csv", index=False)
    print(f"  table  {name}.csv  ({len(df)} rows)")
    return df


def load(conn: sqlite3.Connection) -> pd.DataFrame:
    """Order-grain frame: one row per delivered order with a usable outcome.

    Category and seller are the *dominant* ones by item value, picked with a
    window function so an order spanning two categories is counted once, not
    once per category.
    """
    return pd.read_sql_query("""
        WITH oc AS (
            SELECT order_id, category, seller_id FROM (
                SELECT order_id, category, seller_id,
                       ROW_NUMBER() OVER (PARTITION BY order_id
                                          ORDER BY SUM(price) DESC) AS rn
                  FROM item_base WHERE is_eligible = 1
                 GROUP BY order_id, category, seller_id) WHERE rn = 1)
        SELECT ob.order_id, ob.customer_state, oc.category, oc.seller_id,
               ob.gmv, ob.item_count, ob.is_on_time, ob.days_to_deliver,
               ob.delivery_vs_estimate_days, ob.order_month,
               d.distance_km, r.review_score
          FROM order_base ob
          JOIN oc USING(order_id)
          LEFT JOIN order_review   r USING(order_id)
          LEFT JOIN order_distance d USING(order_id)
         WHERE ob.is_eligible = 1 AND ob.is_on_time IS NOT NULL
    """, conn)


# ----------------------------------------------------- claim 1: RJ vs distance
def claim_1(df: pd.DataFrame) -> dict:
    print("\nCLAIM 1 — is Rio de Janeiro's slowness explained by distance?")
    d = df.dropna(subset=["distance_km"]).copy()
    d["band"] = pd.cut(d.distance_km, bins=BANDS, labels=BAND_LABELS, right=False)

    by_state = (d.groupby("customer_state")
                  .agg(orders=("order_id", "size"), avg_km=("distance_km", "mean"),
                       avg_days=("days_to_deliver", "mean"),
                       late_pct=("is_on_time", lambda s: 100 * (1 - s.mean())))
                  .query("orders >= 200").sort_values("orders", ascending=False)
                  .round(2).reset_index())
    save(by_state, "distance_by_state")

    rj_km = by_state.set_index("customer_state").loc["RJ", "avg_km"]
    sp_km = by_state.set_index("customer_state").loc["SP", "avg_km"]
    print(f"  RJ averages {rj_km:.0f}km vs SP {sp_km:.0f}km — RJ IS further, so the"
          f"\n  original 'comparable distances' wording was wrong. Banding instead:")

    d["is_rj"] = np.where(d.customer_state == "RJ", "RJ", "all other states")
    banded = (d.groupby(["band", "is_rj"], observed=True)
                .agg(orders=("order_id", "size"), avg_days=("days_to_deliver", "mean"),
                     late_pct=("is_on_time", lambda s: 100 * (1 - s.mean())))
                .round(2).reset_index())
    save(banded, "rj_vs_others_by_distance_band")

    # Tightest comparison: same seller origin (SP), same band, RJ vs MG.
    sp_origin = pd.read_sql_query("", sqlite3.connect(":memory:")) if False else None
    print(banded.pivot(index="band", columns="is_rj",
                       values=["avg_days", "late_pct"]).to_string())
    return {"rj_avg_km": float(rj_km), "sp_avg_km": float(sp_km)}


def claim_1_same_origin(conn: sqlite3.Connection) -> dict:
    """RJ vs MG, holding seller origin (SP) and distance band constant."""
    q = pd.read_sql_query("""
        WITH oc AS (
            SELECT order_id, seller_state FROM (
                SELECT i.order_id, s.seller_state,
                       ROW_NUMBER() OVER (PARTITION BY i.order_id
                                          ORDER BY SUM(i.price) DESC) AS rn
                  FROM item_base i JOIN sellers s ON i.seller_id = s.seller_id
                 WHERE i.is_eligible = 1
                 GROUP BY i.order_id, s.seller_state) WHERE rn = 1)
        SELECT ob.customer_state, ob.days_to_deliver, ob.is_on_time, d.distance_km
          FROM order_base ob
          JOIN oc USING(order_id)
          JOIN order_distance d USING(order_id)
         WHERE ob.is_eligible = 1 AND ob.is_on_time IS NOT NULL
           AND oc.seller_state = 'SP' AND ob.customer_state IN ('RJ', 'MG')
           AND d.distance_km >= 100
    """, conn)
    q["band"] = pd.cut(q.distance_km, bins=[100, 300, 600, 10_000],
                       labels=["100–300", "300–600", "600km+"], right=False)
    out = (q.groupby(["band", "customer_state"], observed=True)
             .agg(orders=("is_on_time", "size"), avg_days=("days_to_deliver", "mean"),
                  late_pct=("is_on_time", lambda s: 100 * (1 - s.mean())))
             .round(2).reset_index())
    save(out, "rj_vs_mg_same_seller_origin")
    print("\n  Same origin (SP sellers), same distance band, RJ vs MG:")
    print(out.to_string(index=False))
    mid = out[out.band == "300–600"].set_index("customer_state")
    return {
        "rj_days_300_600": float(mid.loc["RJ", "avg_days"]),
        "mg_days_300_600": float(mid.loc["MG", "avg_days"]),
        "rj_late_pct_300_600": float(mid.loc["RJ", "late_pct"]),
        "mg_late_pct_300_600": float(mid.loc["MG", "late_pct"]),
    }


# ------------------------------------------- claim 2: review gap under controls
def stratified_gap(df: pd.DataFrame, keys: list[str], min_n: int = 30) -> tuple:
    """Size-weighted mean of the within-stratum gap (Mantel-Haenszel style).

    Each stratum is weighted by n0*n1/(n0+n1) — its effective sample size for a
    difference of means — so a stratum that is almost all on-time contributes
    little, rather than contributing its raw row count.
    """
    g = (df.groupby(keys + ["is_on_time"])
           .agg(m=("review_score", "mean"), n=("review_score", "size"))
           .unstack("is_on_time"))
    g.columns = ["_".join(map(str, c)) for c in g.columns]
    g = g.dropna()
    g = g[(g.n_0 >= min_n) & (g.n_1 >= min_n)]
    w = (g.n_0 * g.n_1) / (g.n_0 + g.n_1)
    return float(((g.m_1 - g.m_0) * w).sum() / w.sum()), len(g), int(g.n_0.sum() + g.n_1.sum())


def claim_2(df: pd.DataFrame) -> dict:
    print("\nCLAIM 2 — does the late/on-time review gap survive controls?")
    d = df.dropna(subset=["review_score"]).copy()
    raw = d.groupby("is_on_time").review_score.mean()
    rows = [{"adjustment": "none (raw difference)", "gap": round(raw[1] - raw[0], 3),
             "strata": 1, "orders": len(d)}]
    for label, keys in [("state", ["customer_state"]), ("category", ["category"]),
                        ("state x category", ["customer_state", "category"]),
                        ("purchase month", ["order_month"])]:
        gap, cells, n = stratified_gap(d, keys)
        rows.append({"adjustment": f"stratified by {label}", "gap": round(gap, 3),
                     "strata": cells, "orders": n})

    g = d.dropna(subset=["distance_km"]).copy()
    g["dist_q"] = pd.qcut(g.distance_km, 5, labels=False)
    g["price_q"] = pd.qcut(g.gmv, 5, labels=False, duplicates="drop")
    gap, cells, n = stratified_gap(g, ["dist_q", "price_q"])
    rows.append({"adjustment": "stratified by distance x price quintile",
                 "gap": round(gap, 3), "strata": cells, "orders": n})

    # --- OLS with fixed effects
    m = g.copy()
    m["late"] = 1 - m.is_on_time
    m["log_gmv"] = np.log(m.gmv.clip(lower=1))
    m["log_dist"] = np.log(m.distance_km.clip(lower=1))
    top = m.category.value_counts().head(20).index
    m["cat"] = np.where(m.category.isin(top), m.category, "other")

    f1 = "review_score ~ late + log_gmv + log_dist + item_count + C(customer_state) + C(cat) + C(order_month)"
    fit = smf.ols(f1, data=m).fit()
    rows.append({"adjustment": "OLS: state+category+month FE, log price, log distance",
                 "gap": round(-fit.params["late"], 3), "strata": np.nan, "orders": int(fit.nobs)})

    # Seller fixed effects need sellers observed on BOTH sides of the outcome,
    # otherwise the dummy absorbs the very variation being measured.
    cnt = m.groupby("seller_id").agg(n=("late", "size"), late_n=("late", "sum"))
    keep = cnt[(cnt.n >= 30) & (cnt.late_n >= 5) & (cnt.late_n < cnt.n - 5)].index
    w = m[m.seller_id.isin(keep)].copy()
    fe = smf.ols(f1 + " + C(seller_id)", data=w).fit()
    ci = fe.conf_int().loc["late"]
    rows.append({"adjustment": "OLS: + seller fixed effects (within-seller)",
                 "gap": round(-fe.params["late"], 3), "strata": w.seller_id.nunique(),
                 "orders": int(fe.nobs)})

    out = save(pd.DataFrame(rows), "review_gap_under_controls")
    print(out.to_string(index=False))
    print(f"\n  within-seller estimate: {-fe.params['late']:.3f} "
          f"(SE {fe.bse['late']:.3f}, 95% CI [{-ci[1]:.3f}, {-ci[0]:.3f}])")

    # --- dose-response under the full model
    w = w.copy()
    w["bucket"] = pd.cut(w.delivery_vs_estimate_days,
                         bins=[-200, -15, -8, -1, 0, 7, 14, 30, 400],
                         labels=["15+ early", "8–14 early", "1–7 early", "on day",
                                 "1–7 late", "8–14 late", "15–30 late", "30+ late"])
    dr = smf.ols('review_score ~ C(bucket, Treatment("on day")) + log_gmv + log_dist'
                 ' + item_count + C(customer_state) + C(cat) + C(order_month) + C(seller_id)',
                 data=w).fit()
    drr = [{"bucket": "on day", "adjusted_effect": 0.0, "orders": int((w.bucket == "on day").sum())}]
    for k, v in dr.params.items():
        if k.startswith("C(bucket"):
            lab = k.split("[T.")[1].rstrip("]")
            drr.append({"bucket": lab, "adjusted_effect": round(v, 3),
                        "orders": int((w.bucket == lab).sum())})
    order = ["15+ early", "8–14 early", "1–7 early", "on day", "1–7 late",
             "8–14 late", "15–30 late", "30+ late"]
    dose = pd.DataFrame(drr).set_index("bucket").loc[order].reset_index()
    save(dose, "review_doseresponse_controlled")

    return {
        "raw_gap": round(float(raw[1] - raw[0]), 3),
        "gap_within_seller": round(float(-fe.params["late"]), 3),
        "gap_within_seller_ci": [round(float(-ci[1]), 3), round(float(-ci[0]), 3)],
        "gap_within_seller_n": int(fe.nobs),
        "sellers_in_fe": int(w.seller_id.nunique()),
        "early_1_7_effect": float(dose.set_index("bucket").loc["1–7 early", "adjusted_effect"]),
        "late_1_7_effect": float(dose.set_index("bucket").loc["1–7 late", "adjusted_effect"]),
    }, dose


# ------------------------------------------------------------------- figures
def fig_rj(banded: pd.DataFrame, same_origin: pd.DataFrame) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.4), gridspec_kw={"wspace": 0.26})

    p = banded.pivot(index="band", columns="is_rj", values="avg_days").loc[
        [b for b in BAND_LABELS if b in set(banded.band)]]
    x = range(len(p))
    wdt = 0.38
    ax1.bar([i - wdt / 2 for i in x], p["all other states"], width=wdt,
            color=t.BLUE, label="all other states", zorder=3)
    ax1.bar([i + wdt / 2 for i in x], p["RJ"], width=wdt,
            color=t.ORANGE, label="Rio de Janeiro", zorder=3)
    for i, (o, r) in enumerate(zip(p["all other states"], p["RJ"])):
        ax1.text(i - wdt / 2, o + 0.3, f"{o:.1f}", ha="center", fontsize=8.5, color=t.INK_2)
        ax1.text(i + wdt / 2, r + 0.3, f"{r:.1f}", ha="center", fontsize=8.5, color=t.INK_2)
    # Show RJ's n per band: the two longest bands hold under 700 RJ orders
    # between them, and RJ is faster there — a real reversal, but on a thin
    # base and against the remote states that dominate those bands.
    rj_n = banded[banded.is_rj == "RJ"].set_index("band").orders
    ax1.set_xticks(list(x))
    ax1.set_xticklabels([f"{b}\nRJ n={int(rj_n[b]):,}" for b in p.index], fontsize=8.5)
    ax1.set_xlabel("seller-to-customer distance")
    ax1.set_ylabel("average days to deliver")
    ax1.set_ylim(0, max(p.max()) * 1.2)
    t.titles(ax1, "RJ matches the field locally, then falls behind from 100km out",
             "Average delivery days by distance band · 88% of RJ orders are 100–1000km")
    ax1.legend(loc="upper left")
    t.hgrid_only(ax1)

    s = same_origin.pivot(index="band", columns="customer_state", values="avg_days")
    x2 = range(len(s))
    ax2.bar([i - wdt / 2 for i in x2], s["MG"], width=wdt, color=t.BLUE,
            label="Minas Gerais", zorder=3)
    ax2.bar([i + wdt / 2 for i in x2], s["RJ"], width=wdt, color=t.ORANGE,
            label="Rio de Janeiro", zorder=3)
    for i, (m_, r_) in enumerate(zip(s["MG"], s["RJ"])):
        ax2.text(i - wdt / 2, m_ + 0.3, f"{m_:.1f}", ha="center", fontsize=8.5, color=t.INK_2)
        ax2.text(i + wdt / 2, r_ + 0.3, f"{r_:.1f}", ha="center", fontsize=8.5, color=t.INK_2)
    ax2.set_xticks(list(x2))
    ax2.set_xticklabels(s.index, fontsize=9)
    ax2.set_xlabel("seller-to-customer distance")
    ax2.set_ylabel("average days to deliver")
    ax2.set_ylim(0, max(s.max()) * 1.2)
    t.titles(ax2, "Same São Paulo sellers, same distance — RJ still 4 days slower",
             "Shipments originating in São Paulo only")
    ax2.legend(loc="upper left")
    t.hgrid_only(ax2)

    t.source_note(fig, "Olist dataset · great-circle distance from zip-prefix centroids · "
                       "delivered orders with both delivery and estimate dates")
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / "09_rj_distance_control.png")
    plt.close(fig)
    print("  figure 09_rj_distance_control.png")


def fig_robustness(gaps: pd.DataFrame, dose: pd.DataFrame) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.6),
                                   gridspec_kw={"wspace": 0.52, "width_ratios": [1, 1]})

    g = gaps.iloc[::-1]
    labels = [s.replace("stratified by ", "± ").replace("OLS: ", "OLS ") for s in g.adjustment]
    ax1.barh(range(len(g)), g.gap, color=t.BLUE, height=0.6, zorder=3)
    for i, v in enumerate(g.gap):
        ax1.text(v + 0.03, i, f"{v:.2f}", va="center", fontsize=9, color=t.INK_2)
    ax1.axvline(gaps.gap.iloc[0], color=t.AXIS, linestyle=(0, (4, 3)), linewidth=1, zorder=2)
    ax1.set_yticks(range(len(g)))
    ax1.set_yticklabels(labels, fontsize=8.5)
    ax1.set_xlim(0, gaps.gap.max() * 1.22)
    ax1.set_xlabel("review-score gap (on time − late)")
    t.titles(ax1, "The gap barely moves under any control",
             "Dashed line = unadjusted difference")
    t.vgrid_only(ax1)

    colors = [t.BLUE if v >= 0 else t.CRITICAL for v in dose.adjusted_effect]
    ax2.bar(range(len(dose)), dose.adjusted_effect, color=colors, width=0.64, zorder=3)
    for i, v in enumerate(dose.adjusted_effect):
        off = 0.08 if v >= 0 else -0.18
        ax2.text(i, v + off, f"{v:+.2f}" if v else "ref", ha="center",
                 fontsize=8.5, color=t.INK_2)
    ax2.axhline(0, color=t.AXIS, linewidth=1, zorder=2)
    ax2.set_xticks(range(len(dose)))
    ax2.set_xticklabels(dose.bucket, rotation=38, ha="right", fontsize=8.5)
    ax2.set_ylabel("adjusted effect on review score")
    ax2.set_ylim(dose.adjusted_effect.min() * 1.25, dose.adjusted_effect.max() * 2.1)
    t.titles(ax2, "Earliness buys +0.12; one week late costs −1.28",
             "vs. delivery on the estimated day, all controls + seller fixed effects")
    ax2.legend([plt.Rectangle((0, 0), 1, 1, color=t.BLUE),
                plt.Rectangle((0, 0), 1, 1, color=t.CRITICAL)],
               ["early / on time", "late"], loc="lower left")
    t.hgrid_only(ax2)

    t.source_note(fig, "Olist dataset · OLS, 59,841 orders across 283 sellers observed on both "
                       "sides · observational: controls remove observed confounders only")
    fig.savefig(FIGURES / "10_review_gap_robustness.png")
    plt.close(fig)
    print("  figure 10_review_gap_robustness.png")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ROOT / "data" / "olist.db"))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='order_distance'").fetchone():
        raise SystemExit("order_distance missing — run: python scripts/build_distance.py")

    t.apply_theme()
    df = load(conn)
    print(f"loaded {len(df):,} delivered orders with a delivery outcome")

    r1 = claim_1(df)
    r1b = claim_1_same_origin(conn)
    r2, dose = claim_2(df)

    print("\nrendering figures ...")
    fig_rj(pd.read_csv(TABLES / "rj_vs_others_by_distance_band.csv"),
           pd.read_csv(TABLES / "rj_vs_mg_same_seller_origin.csv"))
    fig_robustness(pd.read_csv(TABLES / "review_gap_under_controls.csv"), dose)

    out = {"claim_1_rj_distance": {**r1, **r1b}, "claim_2_review_gap": r2}
    (ROOT / "outputs" / "controls.json").write_text(json.dumps(out, indent=2) + "\n")
    print("\nwrote outputs/controls.json")
    conn.close()


if __name__ == "__main__":
    main()
