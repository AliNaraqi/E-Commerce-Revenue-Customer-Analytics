"""Execute the SQL layer, export every result table, and render the figures.

Usage:
    python scripts/build_db.py          # once, to create data/olist.db
    python scripts/run_analysis.py      # -> outputs/tables/*.csv, outputs/figures/*.png

Every number in reports/ and README.md comes from this script. Re-running it
after a data refresh regenerates all of them, so no figure can silently drift
away from the tables it was drawn from.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

import viz_theme as t

ROOT = Path(__file__).resolve().parent.parent
TABLES = ROOT / "outputs" / "tables"
FIGURES = ROOT / "outputs" / "figures"
SQL = ROOT / "sql"

TREND_START, TREND_END = "2017-01", "2018-08"


# --------------------------------------------------------------------------- io
def connect(db: Path) -> sqlite3.Connection:
    if not db.exists():
        raise SystemExit(f"database not found: {db}\nrun: python scripts/build_db.py")
    conn = sqlite3.connect(db)
    # Rebuild the base layer from the SQL files so they stay the single source
    # of truth: a stale db from an older revision can never silently feed the
    # figures. Cheap (~4s) because it is one pass over 100k orders.
    conn.executescript((SQL / "00_analytical_tables.sql").read_text())
    # 03 both defines customer_rfm and reports on it; take only the definition
    # up to the first reporting SELECT, then query it through Q below.
    conn.executescript(
        (SQL / "03_customer_analytics.sql").read_text().split("SELECT 'rfm_segments'")[0]
    )
    return conn


def save(df: pd.DataFrame, name: str) -> pd.DataFrame:
    TABLES.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLES / f"{name}.csv", index=False)
    print(f"  table  {name}.csv  ({len(df)} rows)")
    return df


def fig_done(fig, name: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / f"{name}.png")
    plt.close(fig)
    print(f"  figure {name}.png")


# ---------------------------------------------------------------------- queries
Q = {}

Q["headline"] = """
SELECT COUNT(*) AS orders, ROUND(SUM(gmv),2) AS gmv, ROUND(SUM(freight),2) AS freight,
       SUM(item_count) AS items, ROUND(SUM(gmv)/COUNT(*),2) AS aov,
       ROUND(1.0*SUM(item_count)/COUNT(*),3) AS items_per_order,
       ROUND(100.0*SUM(freight)/SUM(gmv+freight),2) AS freight_pct_of_spend,
       COUNT(DISTINCT customer_unique_id) AS active_customers
  FROM order_base WHERE is_eligible=1"""

Q["monthly_trend"] = f"""
WITH m AS (
  SELECT order_month, COUNT(*) AS orders, ROUND(SUM(gmv),2) AS gmv,
         ROUND(SUM(gmv)/COUNT(*),2) AS aov, COUNT(DISTINCT customer_unique_id) AS active_customers
    FROM order_base WHERE is_eligible=1 AND order_month BETWEEN '{TREND_START}' AND '{TREND_END}'
   GROUP BY order_month)
SELECT *, ROUND(100.0*(gmv-LAG(gmv) OVER (ORDER BY order_month))
       /LAG(gmv) OVER (ORDER BY order_month),2) AS gmv_mom_pct
  FROM m ORDER BY order_month"""

Q["category_contribution"] = """
WITH c AS (
  SELECT category, COUNT(DISTINCT order_id) AS orders, COUNT(*) AS items,
         ROUND(SUM(price),2) AS gmv, ROUND(AVG(price),2) AS avg_item_price
    FROM item_base WHERE is_eligible=1 GROUP BY category)
SELECT ROW_NUMBER() OVER (ORDER BY gmv DESC) AS rank, category, orders, items, gmv, avg_item_price,
       ROUND(100.0*gmv/SUM(gmv) OVER (),2) AS pct_of_gmv,
       ROUND(100.0*SUM(gmv) OVER (ORDER BY gmv DESC
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)/SUM(gmv) OVER (),2) AS cumulative_pct
  FROM c ORDER BY gmv DESC"""

Q["state_performance"] = """
SELECT ob.customer_state, COUNT(*) AS orders, ROUND(SUM(ob.gmv),2) AS gmv,
       ROUND(100.0*SUM(ob.gmv)/SUM(SUM(ob.gmv)) OVER (),2) AS pct_of_gmv,
       ROUND(SUM(ob.gmv)/COUNT(*),2) AS aov, ROUND(AVG(ob.freight),2) AS avg_freight,
       ROUND(AVG(ob.days_to_deliver),1) AS avg_days_to_deliver,
       ROUND(100.0-100.0*AVG(ob.is_on_time),2) AS late_pct,
       ROUND(AVG(r.review_score),3) AS avg_review
  FROM order_base ob LEFT JOIN order_review r ON ob.order_id=r.order_id
 WHERE ob.is_eligible=1 GROUP BY ob.customer_state ORDER BY gmv DESC"""

Q["customer_type_summary"] = """
SELECT customer_type, COUNT(*) AS customers,
       ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),2) AS pct_of_customers,
       SUM(orders) AS orders, ROUND(SUM(gmv),2) AS gmv,
       ROUND(100.0*SUM(gmv)/SUM(SUM(gmv)) OVER (),2) AS pct_of_gmv,
       ROUND(SUM(gmv)/COUNT(*),2) AS gmv_per_customer,
       ROUND(1.0*SUM(orders)/COUNT(*),2) AS orders_per_customer,
       ROUND(SUM(gmv)/SUM(orders),2) AS aov
  FROM customer_base GROUP BY customer_type ORDER BY customers DESC"""

Q["rfm_segments"] = """
SELECT segment, COUNT(*) AS customers,
       ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),2) AS pct_of_customers,
       ROUND(SUM(gmv),2) AS gmv, ROUND(100.0*SUM(gmv)/SUM(SUM(gmv)) OVER (),2) AS pct_of_gmv,
       ROUND(SUM(gmv)/COUNT(*),2) AS gmv_per_customer,
       ROUND(AVG(recency_days),0) AS avg_recency_days, ROUND(AVG(orders),2) AS avg_orders
  FROM customer_rfm GROUP BY segment ORDER BY gmv DESC"""

Q["customer_value_deciles"] = """
WITH r AS (SELECT gmv, NTILE(10) OVER (ORDER BY gmv DESC) AS decile FROM customer_base)
SELECT decile, COUNT(*) AS customers, ROUND(SUM(gmv),2) AS gmv,
       ROUND(100.0*SUM(gmv)/SUM(SUM(gmv)) OVER (),2) AS pct_of_gmv,
       ROUND(100.0*SUM(SUM(gmv)) OVER (ORDER BY decile
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)/SUM(SUM(gmv)) OVER (),2) AS cumulative_pct_of_gmv
  FROM r GROUP BY decile ORDER BY decile"""

Q["delivery_headline"] = """
SELECT COUNT(*) AS delivered_with_dates, SUM(is_on_time) AS on_time,
       COUNT(*)-SUM(is_on_time) AS late, ROUND(100.0*AVG(is_on_time),2) AS on_time_pct,
       ROUND(100.0-100.0*AVG(is_on_time),2) AS late_pct,
       ROUND(AVG(days_to_deliver),2) AS avg_days_to_deliver,
       ROUND(AVG(-delivery_vs_estimate_days),2) AS avg_days_early_vs_estimate
  FROM order_base WHERE is_eligible=1 AND is_on_time IS NOT NULL"""

Q["lateness_buckets"] = """
SELECT CASE WHEN delivery_vs_estimate_days<=-15 THEN '15+ days early'
            WHEN delivery_vs_estimate_days<=-8 THEN '8-14 days early'
            WHEN delivery_vs_estimate_days<=-1 THEN '1-7 days early'
            WHEN delivery_vs_estimate_days=0 THEN 'on estimated day'
            WHEN delivery_vs_estimate_days<=7 THEN '1-7 days late'
            WHEN delivery_vs_estimate_days<=14 THEN '8-14 days late'
            WHEN delivery_vs_estimate_days<=30 THEN '15-30 days late'
            ELSE '30+ days late' END AS bucket,
       MIN(delivery_vs_estimate_days) AS bucket_min_days, COUNT(*) AS orders,
       ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),2) AS pct_of_orders,
       ROUND(AVG(r.review_score),3) AS avg_review
  FROM order_base ob LEFT JOIN order_review r ON ob.order_id=r.order_id
 WHERE ob.is_eligible=1 AND ob.delivery_vs_estimate_days IS NOT NULL
 GROUP BY bucket ORDER BY bucket_min_days"""

Q["ontime_vs_late_reviews"] = """
SELECT CASE WHEN ob.is_on_time=1 THEN 'on time' ELSE 'late' END AS delivery_outcome,
       COUNT(*) AS orders, ROUND(AVG(r.review_score),3) AS avg_review,
       ROUND(100.0*AVG(CASE WHEN r.review_score<=2 THEN 1 ELSE 0 END),2) AS pct_1_or_2_star,
       ROUND(100.0*AVG(CASE WHEN r.review_score>=4 THEN 1 ELSE 0 END),2) AS pct_4_or_5_star
  FROM order_base ob JOIN order_review r ON ob.order_id=r.order_id
 WHERE ob.is_eligible=1 AND ob.is_on_time IS NOT NULL GROUP BY delivery_outcome"""

Q["review_score_crosstab"] = """
SELECT CAST(r.review_score AS INTEGER) AS review_score,
       SUM(CASE WHEN ob.is_on_time=1 THEN 1 ELSE 0 END) AS on_time_orders,
       SUM(CASE WHEN ob.is_on_time=0 THEN 1 ELSE 0 END) AS late_orders,
       ROUND(100.0*SUM(CASE WHEN ob.is_on_time=1 THEN 1 ELSE 0 END)
             /SUM(SUM(CASE WHEN ob.is_on_time=1 THEN 1 ELSE 0 END)) OVER (),2) AS pct_of_on_time,
       ROUND(100.0*SUM(CASE WHEN ob.is_on_time=0 THEN 1 ELSE 0 END)
             /SUM(SUM(CASE WHEN ob.is_on_time=0 THEN 1 ELSE 0 END)) OVER (),2) AS pct_of_late
  FROM order_base ob JOIN order_review r ON ob.order_id=r.order_id
 WHERE ob.is_eligible=1 AND ob.is_on_time IS NOT NULL
   AND r.review_score=CAST(r.review_score AS INTEGER)
 GROUP BY review_score ORDER BY review_score"""

Q["ontime_monthly"] = f"""
SELECT ob.order_month, COUNT(*) AS delivered_orders,
       ROUND(100.0*AVG(ob.is_on_time),2) AS on_time_pct,
       ROUND(AVG(ob.days_to_deliver),1) AS avg_days_to_deliver,
       ROUND(AVG(r.review_score),3) AS avg_review
  FROM order_base ob LEFT JOIN order_review r ON ob.order_id=r.order_id
 WHERE ob.is_eligible=1 AND ob.is_on_time IS NOT NULL
   AND ob.order_month BETWEEN '{TREND_START}' AND '{TREND_END}'
 GROUP BY ob.order_month ORDER BY ob.order_month"""

Q["delays_by_state"] = """
SELECT ob.customer_state, COUNT(*) AS delivered_orders,
       COUNT(*)-SUM(ob.is_on_time) AS late_orders,
       ROUND(100.0-100.0*AVG(ob.is_on_time),2) AS late_pct,
       ROUND(AVG(ob.days_to_deliver),1) AS avg_days_to_deliver,
       ROUND(AVG(ob.freight),2) AS avg_freight, ROUND(AVG(r.review_score),3) AS avg_review,
       ROUND(SUM(ob.gmv),2) AS gmv
  FROM order_base ob LEFT JOIN order_review r ON ob.order_id=r.order_id
 WHERE ob.is_eligible=1 AND ob.is_on_time IS NOT NULL
 GROUP BY ob.customer_state HAVING COUNT(*)>=200 ORDER BY late_orders DESC"""

Q["delays_by_category"] = """
WITH oc AS (SELECT order_id, category FROM (
      SELECT order_id, category, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY SUM(price) DESC) rn
        FROM item_base WHERE is_eligible=1 GROUP BY order_id, category) WHERE rn=1)
SELECT oc.category, COUNT(*) AS delivered_orders,
       ROUND(100.0-100.0*AVG(ob.is_on_time),2) AS late_pct,
       ROUND(AVG(ob.days_to_deliver),1) AS avg_days_to_deliver,
       ROUND(AVG(r.review_score),3) AS avg_review, ROUND(SUM(ob.gmv),2) AS gmv
  FROM order_base ob JOIN oc ON ob.order_id=oc.order_id
  LEFT JOIN order_review r ON ob.order_id=r.order_id
 WHERE ob.is_eligible=1 AND ob.is_on_time IS NOT NULL
 GROUP BY oc.category HAVING COUNT(*)>=500 ORDER BY late_pct DESC"""

Q["new_vs_returning_monthly"] = f"""
WITH f AS (SELECT customer_unique_id, substr(MIN(order_purchase_timestamp),1,7) AS cohort_month
             FROM order_base WHERE is_eligible=1 GROUP BY customer_unique_id)
SELECT ob.order_month,
       COUNT(DISTINCT CASE WHEN f.cohort_month=ob.order_month THEN ob.customer_unique_id END) AS new_customers,
       COUNT(DISTINCT CASE WHEN f.cohort_month<ob.order_month THEN ob.customer_unique_id END) AS returning_customers,
       ROUND(SUM(CASE WHEN f.cohort_month<ob.order_month THEN ob.gmv ELSE 0 END),2) AS returning_gmv,
       ROUND(100.0*SUM(CASE WHEN f.cohort_month<ob.order_month THEN ob.gmv ELSE 0 END)/SUM(ob.gmv),2) AS returning_gmv_pct
  FROM order_base ob JOIN f ON ob.customer_unique_id=f.customer_unique_id
 WHERE ob.is_eligible=1 AND ob.order_month BETWEEN '{TREND_START}' AND '{TREND_END}'
 GROUP BY ob.order_month ORDER BY ob.order_month"""

Q["repeat_latency"] = """
WITH s AS (SELECT customer_unique_id, order_purchase_timestamp,
             ROW_NUMBER() OVER (PARTITION BY customer_unique_id ORDER BY order_purchase_timestamp) rn
             FROM order_base WHERE is_eligible=1),
     g AS (SELECT julianday(b.order_purchase_timestamp)-julianday(a.order_purchase_timestamp) AS d
             FROM s a JOIN s b ON a.customer_unique_id=b.customer_unique_id AND a.rn=1 AND b.rn=2)
SELECT COUNT(*) AS repeat_customers, ROUND(AVG(d),1) AS avg_days_to_second_order,
       ROUND(100.0*AVG(CASE WHEN d<=30 THEN 1 ELSE 0 END),1) AS pct_within_30d,
       ROUND(100.0*AVG(CASE WHEN d<=90 THEN 1 ELSE 0 END),1) AS pct_within_90d,
       ROUND(100.0*AVG(CASE WHEN d<=180 THEN 1 ELSE 0 END),1) AS pct_within_180d
  FROM g"""

Q["first_order_outcome_vs_repeat"] = """
WITH fo AS (SELECT customer_unique_id, is_on_time, orders_total FROM (
      SELECT ob.customer_unique_id, ob.is_on_time,
             COUNT(*) OVER (PARTITION BY ob.customer_unique_id) AS orders_total,
             ROW_NUMBER() OVER (PARTITION BY ob.customer_unique_id
                                ORDER BY ob.order_purchase_timestamp) rn
        FROM order_base ob WHERE ob.is_eligible=1) WHERE rn=1)
SELECT CASE WHEN is_on_time=1 THEN 'first order on time' ELSE 'first order late' END AS first_experience,
       COUNT(*) AS customers, SUM(CASE WHEN orders_total>1 THEN 1 ELSE 0 END) AS came_back,
       ROUND(100.0*AVG(CASE WHEN orders_total>1 THEN 1 ELSE 0 END),2) AS repeat_rate_pct
  FROM fo WHERE is_on_time IS NOT NULL GROUP BY first_experience"""

Q["eligibility_sensitivity"] = """
SELECT 'delivered (reporting basis)' AS basis, COUNT(*) AS orders, ROUND(SUM(gmv),2) AS gmv,
       ROUND(SUM(gmv)/COUNT(*),2) AS aov FROM order_base WHERE order_status='delivered'
UNION ALL SELECT 'not canceled/unavailable', COUNT(*), ROUND(SUM(gmv),2), ROUND(SUM(gmv)/COUNT(*),2)
  FROM order_base WHERE order_status NOT IN ('canceled','unavailable') AND item_count>0
UNION ALL SELECT 'all statuses', COUNT(*), ROUND(SUM(gmv),2), ROUND(SUM(gmv)/COUNT(*),2)
  FROM order_base WHERE item_count>0"""

Q["payment_mix"] = """
SELECT primary_payment_type, COUNT(*) AS orders,
       ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),2) AS pct_of_orders,
       ROUND(SUM(gmv),2) AS gmv, ROUND(SUM(gmv)/COUNT(*),2) AS aov,
       ROUND(AVG(max_installments),2) AS avg_installments
  FROM order_base WHERE is_eligible=1 AND primary_payment_type IS NOT NULL
 GROUP BY primary_payment_type ORDER BY gmv DESC"""


# ---------------------------------------------------------------------- figures
def fig_monthly(trend: pd.DataFrame) -> None:
    """GMV and order volume as stacked panels — never a dual axis."""
    x = range(len(trend))
    labels = [m if m.endswith(("-01", "-04", "-07", "-10")) else "" for m in trend.order_month]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [1.3, 1], "hspace": 0.28})

    ax1.plot(x, trend.gmv, color=t.BLUE, zorder=3)
    ax1.fill_between(x, trend.gmv, color=t.BLUE, alpha=0.08, zorder=2)
    peak = trend.gmv.idxmax()
    ax1.scatter([peak], [trend.gmv[peak]], color=t.BLUE, s=46, zorder=4,
                edgecolor=t.SURFACE, linewidth=2)
    ax1.annotate(f"peak {t.brl(trend.gmv[peak])}\n{trend.order_month[peak]}",
                 xy=(peak, trend.gmv[peak]), xytext=(-14, -56), textcoords="offset points",
                 fontsize=9, color=t.INK_2, ha="right")
    t.titles(ax1, "GMV grew ~8x over 20 months, then plateaued through 2018", "Monthly merchandise value, delivered orders (freight excluded)")
    ax1.set_ylabel("GMV")
    ax1.set_ylim(0, trend.gmv.max() * 1.22)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: t.brl(v)))
    t.hgrid_only(ax1)

    ax2.bar(x, trend.orders, color=t.BLUE, width=0.62, zorder=3)
    t.titles(ax2, "Delivered orders per month", size=11)
    ax2.set_ylabel("orders")
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v/1000:.0f}k" if v else "0"))
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(labels)
    t.hgrid_only(ax2)

    t.source_note(fig, "Olist Brazilian e-commerce dataset · delivered orders · "
                       f"{TREND_START} to {TREND_END} (partial months excluded)")
    fig_done(fig, "01_monthly_gmv_orders")


def fig_categories(cat: pd.DataFrame) -> None:
    """Top 15 by GMV; the rest folded into one 'all other' bar."""
    top = cat.head(15).copy()
    other_gmv = cat.gmv[15:].sum()
    other_pct = cat.pct_of_gmv[15:].sum()
    rows = list(zip(top.category.str.replace("_", " "), top.gmv, top.pct_of_gmv))
    rows.append((f"all other ({len(cat) - 15} categories)", other_gmv, other_pct))
    rows.reverse()
    names, gmv, pct = zip(*rows)

    fig, ax = plt.subplots(figsize=(9.5, 7.2))
    colors = [t.MUTED if n.startswith("all other") else t.BLUE for n in names]
    ax.barh(range(len(names)), gmv, color=colors, height=0.68, zorder=3)
    for i, (g, p) in enumerate(zip(gmv, pct)):
        ax.text(g + max(gmv) * 0.012, i, f"{t.brl(g)}  ·  {p:.1f}%",
                va="center", fontsize=8.5, color=t.INK_2)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names)
    ax.set_xlim(0, max(gmv) * 1.26)
    ax.set_xlabel("GMV")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: t.brl(v)))
    top5 = cat.pct_of_gmv.head(5).sum()
    t.titles(ax, f"The top 5 of {len(cat)} categories carry {top5:.0f}% of GMV",
             "Merchandise value by product category, delivered orders")
    t.vgrid_only(ax)
    t.source_note(fig, "Olist dataset · item-level GMV, freight excluded · "
                       "'unknown' = products with no category label in source")
    fig_done(fig, "02_category_contribution")


def fig_states(state: pd.DataFrame) -> None:
    top = state.head(12).iloc[::-1]
    fig, ax = plt.subplots(figsize=(9, 5.6))
    ax.barh(range(len(top)), top.gmv, color=t.BLUE, height=0.66, zorder=3)
    for i, (g, p, a) in enumerate(zip(top.gmv, top.pct_of_gmv, top.aov)):
        ax.text(g + top.gmv.max() * 0.012, i, f"{t.brl(g)}  ·  {p:.1f}%  ·  AOV {a:.0f}",
                va="center", fontsize=8.5, color=t.INK_2)
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(top.customer_state)
    ax.set_xlim(0, top.gmv.max() * 1.34)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: t.brl(v)))
    ax.set_xlabel("GMV")
    sp = state.loc[state.customer_state == "SP", "pct_of_gmv"].iloc[0]
    t.titles(ax, f"São Paulo alone is {sp:.0f}% of GMV; the top 3 states are "
                 f"{state.pct_of_gmv.head(3).sum():.0f}%",
             "GMV by customer state, delivered orders · top 12 of 27")
    t.vgrid_only(ax)
    t.source_note(fig, "Olist dataset · AOV in Brazilian reais")
    fig_done(fig, "03_state_performance")


def fig_delivery_reviews(buckets: pd.DataFrame, crosstab: pd.DataFrame,
                         summary: pd.DataFrame) -> None:
    """Two panels: review score by lateness, and the score distribution split."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.4),
                                   gridspec_kw={"wspace": 0.28})

    b = buckets.copy()
    late_mask = b.bucket_min_days > 0
    colors = [t.CRITICAL if m else t.BLUE for m in late_mask]
    ax1.bar(range(len(b)), b.avg_review, color=colors, width=0.66, zorder=3)
    for i, v in enumerate(b.avg_review):
        ax1.text(i, v + 0.08, f"{v:.2f}", ha="center", fontsize=8.5, color=t.INK_2)
    ax1.set_xticks(range(len(b)))
    # Base size goes in the tick label, not inside the bar: rotated grey text on
    # a saturated fill fails contrast and is the first thing to become unreadable.
    ax1.set_xticklabels(
        [f"{lab.replace(' days', 'd').replace('on estimated day', 'on time')}\nn={n:,}"
         for lab, n in zip(b.bucket, b.orders)],
        rotation=38, ha="right", fontsize=8.5)
    ax1.set_ylim(0, 5)
    ax1.set_yticks([1, 2, 3, 4, 5])
    ax1.set_ylabel("average review score")
    t.titles(ax1, "Satisfaction falls off a cliff the moment an order is late",
             "Mean review score by delivery timing vs. promised date")
    # Identity rides on the swatch, not on coloured label text.
    ax1.legend([plt.Rectangle((0, 0), 1, 1, color=t.BLUE),
                plt.Rectangle((0, 0), 1, 1, color=t.CRITICAL)],
               ["delivered on or before estimate", "delivered late"],
               loc="upper right")
    t.hgrid_only(ax1)

    w = 0.38
    xs = range(len(crosstab))
    ax2.bar([i - w / 2 for i in xs], crosstab.pct_of_on_time, width=w,
            color=t.BLUE, label="delivered on time", zorder=3)
    ax2.bar([i + w / 2 for i in xs], crosstab.pct_of_late, width=w,
            color=t.ORANGE, label="delivered late", zorder=3)
    for i, (a, bb) in enumerate(zip(crosstab.pct_of_on_time, crosstab.pct_of_late)):
        ax2.text(i - w / 2, a + 1, f"{a:.0f}%", ha="center", fontsize=8.5, color=t.INK_2)
        ax2.text(i + w / 2, bb + 1, f"{bb:.0f}%", ha="center", fontsize=8.5, color=t.INK_2)
    ax2.set_xticks(list(xs))
    # No star glyph — the default sans fallbacks here don't carry U+2605.
    ax2.set_xticklabels([str(s) for s in crosstab.review_score])
    ax2.set_xlabel("review score (stars)")
    ax2.set_ylim(0, 72)
    ax2.set_ylabel("share of orders in group")
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    late_1 = crosstab.loc[crosstab.review_score == 1, "pct_of_late"].iloc[0]
    t.titles(ax2, f"{late_1:.0f}% of late orders are rated 1 star",
             "Review-score distribution within each delivery outcome")
    ax2.legend(loc="upper center", ncol=2)
    t.hgrid_only(ax2)

    avg_ot = summary.loc[summary.delivery_outcome == "on time", "avg_review"].iloc[0]
    avg_l = summary.loc[summary.delivery_outcome == "late", "avg_review"].iloc[0]
    t.source_note(fig, f"Olist dataset · delivered orders with both delivery and estimate dates · "
                       f"mean score {avg_ot:.2f} on time vs {avg_l:.2f} late · "
                       "association only, not a causal estimate")
    fig_done(fig, "04_delivery_vs_satisfaction")


def fig_ontime_trend(ot: pd.DataFrame) -> None:
    """On-time rate and review score as separate panels, shared x."""
    x = range(len(ot))
    labels = [m if m.endswith(("-01", "-04", "-07", "-10")) else "" for m in ot.order_month]
    worst = ot.on_time_pct.idxmin()

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.6), sharex=True,
                                   gridspec_kw={"hspace": 0.3})

    ax1.plot(x, ot.on_time_pct, color=t.BLUE, zorder=3)
    ax1.scatter([worst], [ot.on_time_pct[worst]], color=t.CRITICAL, s=52, zorder=4,
                edgecolor=t.SURFACE, linewidth=2)
    ax1.annotate(f"{ot.order_month[worst]}: {ot.on_time_pct[worst]:.0f}% on time",
                 xy=(worst, ot.on_time_pct[worst]), xytext=(8, -4),
                 textcoords="offset points", fontsize=9, color=t.CRITICAL, weight="600")
    ax1.axhline(95, color=t.AXIS, linestyle=(0, (4, 3)), linewidth=1, zorder=2)
    ax1.annotate("95%", xy=(len(ot) - 1, 95), xytext=(6, -3), textcoords="offset points",
                 fontsize=8, color=t.MUTED)
    ax1.set_ylim(78, 101)
    ax1.set_ylabel("on-time rate")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    t.titles(ax1, f"Reliability swings {ot.on_time_pct.max() - ot.on_time_pct.min():.0f} "
                  f"points: troughs at 2017-11 and Feb–Mar 2018",
             "Share of delivered orders arriving on or before the promised date")
    t.hgrid_only(ax1)

    ax2.plot(x, ot.avg_review, color=t.ORANGE, zorder=3)
    ax2.scatter([worst], [ot.avg_review[worst]], color=t.ORANGE, s=46, zorder=4,
                edgecolor=t.SURFACE, linewidth=2)
    ax2.set_ylim(3.6, 4.5)
    ax2.set_ylabel("avg review score")
    t.titles(ax2, "Average review score moves with it", size=11)
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(labels)
    t.hgrid_only(ax2)

    t.source_note(fig, "Olist dataset · delivered orders · separate panels, not a dual axis — "
                       "the two measures share a timeline, not a scale")
    fig_done(fig, "05_ontime_rate_trend")


def fig_state_delay_scatter(ds: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(9, 6))
    sizes = (ds.delivered_orders / ds.delivered_orders.max() * 900) + 40
    ax.scatter(ds.late_pct, ds.avg_review, s=sizes, color=t.BLUE, alpha=0.5,
               edgecolor=t.SURFACE, linewidth=1.6, zorder=3)
    label_these = set(ds.nlargest(5, "late_pct").customer_state) | \
                  set(ds.nlargest(4, "delivered_orders").customer_state)
    for _, r in ds.iterrows():
        if r.customer_state in label_these:
            ax.annotate(r.customer_state, (r.late_pct, r.avg_review),
                        xytext=(0, 11), textcoords="offset points", ha="center",
                        fontsize=9, color=t.INK, weight="600")
    ax.set_xlabel("late-delivery rate")
    ax.set_ylabel("average review score")
    # Headroom so the largest bubble and its label are not clipped.
    lo, hi = ds.avg_review.min(), ds.avg_review.max()
    ax.set_ylim(lo - (hi - lo) * 0.12, hi + (hi - lo) * 0.22)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    t.titles(ax, "States with more late deliveries review lower, with wide spread",
             "Each bubble is a state; bubble area is delivered order volume")
    ax.grid(True)
    t.source_note(fig, "Olist dataset · states with 200+ delivered orders · "
                       "descriptive association; freight distance and product mix not controlled")
    fig_done(fig, "06_state_delay_vs_review")


def fig_customer_value(dec: pd.DataFrame, rfm: pd.DataFrame) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.6),
                                   gridspec_kw={"wspace": 0.3, "width_ratios": [1, 1.15]})

    ramp = [t.SEQ[-1 - i] for i in range(len(dec))]
    ax1.bar(dec.decile, dec.pct_of_gmv, color=ramp, width=0.68, zorder=3)
    for d, p in zip(dec.decile, dec.pct_of_gmv):
        ax1.text(d, p + 0.8, f"{p:.0f}%", ha="center", fontsize=8.5, color=t.INK_2)
    ax1.set_xticks(dec.decile)
    ax1.set_xlabel("customer decile by spend (1 = highest)")
    ax1.set_ylabel("share of GMV")
    ax1.set_ylim(0, dec.pct_of_gmv.max() * 1.18)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    t.titles(ax1, f"The top 10% of customers generate {dec.pct_of_gmv.iloc[0]:.0f}% of GMV",
             "GMV share by customer spend decile")
    t.hgrid_only(ax1)

    one_time_share = rfm.loc[rfm.segment.isin(
        ["Promising high-value", "At risk — high value"]), "pct_of_gmv"].sum()
    r = rfm.sort_values("pct_of_gmv").copy()
    names = r.segment.tolist()
    # Grouped, not overlaid: two bars sharing a baseline are comparable by
    # length; one bar drawn on top of another reads as a part-of-whole.
    h = 0.36
    ys = range(len(r))
    ax2.barh([i + h / 2 for i in ys], r.pct_of_gmv, color=t.BLUE, height=h, zorder=3)
    ax2.barh([i - h / 2 for i in ys], r.pct_of_customers, color=t.ORANGE, height=h, zorder=3)
    for i, (g, c) in enumerate(zip(r.pct_of_gmv, r.pct_of_customers)):
        ax2.text(g + 0.5, i + h / 2, f"{g:.0f}%", va="center", fontsize=8.5, color=t.INK_2)
        ax2.text(c + 0.5, i - h / 2, f"{c:.0f}%", va="center", fontsize=8.5, color=t.INK_2)
    ax2.set_yticks(list(ys))
    ax2.set_yticklabels(names, fontsize=9)
    ax2.set_xlim(0, max(r.pct_of_gmv.max(), r.pct_of_customers.max()) * 1.18)
    ax2.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    t.titles(ax2, f"Two one-time segments hold {one_time_share:.0f}% of GMV",
             "RFM segment: share of GMV vs. share of customers")
    ax2.legend([plt.Rectangle((0, 0), 1, 1, color=t.BLUE),
                plt.Rectangle((0, 0), 1, 1, color=t.ORANGE)],
               ["share of GMV", "share of customers"], loc="lower right")
    t.vgrid_only(ax2)

    t.source_note(fig, "Olist dataset · delivered orders · customer = customer_unique_id · "
                       "recency measured to 2018-10-17, the last purchase in the data")
    fig_done(fig, "07_customer_value_and_segments")


def fig_new_vs_returning(nvr: pd.DataFrame) -> None:
    x = range(len(nvr))
    labels = [m if m.endswith(("-01", "-04", "-07", "-10")) else "" for m in nvr.order_month]
    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.bar(x, nvr.new_customers, color=t.BLUE, width=0.62, label="new customers", zorder=3)
    # 2px surface gap so the thin returning segment reads as its own mark.
    ax.bar(x, nvr.returning_customers, bottom=nvr.new_customers, color=t.ORANGE,
           width=0.62, label="returning customers", zorder=3,
           edgecolor=t.SURFACE, linewidth=1.0)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("customers")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v/1000:.0f}k" if v else "0"))
    last = nvr.returning_gmv_pct.iloc[-1]
    t.titles(ax, f"Growth is almost entirely new-customer acquisition "
                 f"— returning buyers were {last:.1f}% of GMV in {nvr.order_month.iloc[-1]}", "Monthly active customers split by whether they had ordered before")
    ax.legend(loc="upper left")
    t.hgrid_only(ax)
    t.source_note(fig, "Olist dataset · delivered orders · the returning bar is visible but "
                       "never exceeds 3% of monthly customers")
    fig_done(fig, "08_new_vs_returning")


# ------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ROOT / "data" / "olist.db"))
    args = ap.parse_args()

    t.apply_theme()
    conn = connect(Path(args.db))

    print("exporting tables ...")
    out = {name: save(pd.read_sql_query(sql, conn), name) for name, sql in Q.items()}

    print("rendering figures ...")
    fig_monthly(out["monthly_trend"])
    fig_categories(out["category_contribution"])
    fig_states(out["state_performance"])
    fig_delivery_reviews(out["lateness_buckets"], out["review_score_crosstab"],
                         out["ontime_vs_late_reviews"])
    fig_ontime_trend(out["ontime_monthly"])
    fig_state_delay_scatter(out["delays_by_state"])
    fig_customer_value(out["customer_value_deciles"], out["rfm_segments"])
    fig_new_vs_returning(out["new_vs_returning_monthly"])

    # Headline numbers as JSON so the report and README can be checked against
    # one machine-written file instead of hand-copied figures.
    h = out["headline"].iloc[0].to_dict()
    d = out["delivery_headline"].iloc[0].to_dict()
    kpis = {
        "reporting_basis": "delivered orders",
        "trend_window": f"{TREND_START}..{TREND_END}",
        "orders": int(h["orders"]),
        "gmv_brl": float(h["gmv"]),
        "freight_brl": float(h["freight"]),
        "aov_brl": float(h["aov"]),
        "items_per_order": float(h["items_per_order"]),
        "freight_pct_of_spend": float(h["freight_pct_of_spend"]),
        "active_customers": int(h["active_customers"]),
        "repeat_purchase_rate_pct": float(
            out["customer_type_summary"].set_index("customer_type").loc["repeat", "pct_of_customers"]),
        "on_time_pct": float(d["on_time_pct"]),
        "late_orders": int(d["late"]),
        "avg_days_to_deliver": float(d["avg_days_to_deliver"]),
        "avg_review_on_time": float(
            out["ontime_vs_late_reviews"].set_index("delivery_outcome").loc["on time", "avg_review"]),
        "avg_review_late": float(
            out["ontime_vs_late_reviews"].set_index("delivery_outcome").loc["late", "avg_review"]),
        "top5_category_gmv_pct": float(out["category_contribution"].pct_of_gmv.head(5).sum().round(2)),
        "top_decile_gmv_pct": float(out["customer_value_deciles"].pct_of_gmv.iloc[0]),
    }
    (ROOT / "outputs" / "kpis.json").write_text(json.dumps(kpis, indent=2) + "\n")
    print("\nheadline KPIs -> outputs/kpis.json")
    for k, v in kpis.items():
        print(f"  {k:<28} {v}")
    conn.close()


if __name__ == "__main__":
    main()
