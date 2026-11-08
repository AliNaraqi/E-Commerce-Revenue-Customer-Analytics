"""Export a Power BI–ready star schema from the analytical tables.

Usage:
    python scripts/export_powerbi_model.py   # -> dashboard/model/*.csv

Power BI performs best on a star schema: narrow fact tables surrounded by
single-direction dimensions. Pointing Power BI at the nine raw Olist CSVs
instead produces a snowflake with ambiguous paths (orders -> customers and
order_items -> products -> category), where measures silently fan out. The
export below resolves that in SQL, where it can be tested, rather than in DAX.

Grain:
    fact_orders      1 row per order_id        — order-level measures
    fact_order_items 1 row per order line      — category/seller/product measures
    dim_*            1 row per key
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboard" / "model"

EXPORTS: dict[str, str] = {
    # ---- facts -----------------------------------------------------------
    "fact_orders": """
        SELECT order_id, customer_unique_id, customer_state, order_status, is_eligible,
               order_purchase_timestamp, date(order_purchase_timestamp) AS order_date,
               order_delivered_customer_date, order_estimated_delivery_date,
               gmv, freight, item_count, paid_value, max_installments, primary_payment_type,
               days_to_deliver, delivery_vs_estimate_days, is_on_time,
               r.review_score
          FROM order_base ob LEFT JOIN order_review r USING(order_id)""",
    "fact_order_items": """
        SELECT order_id, order_item_id, product_id, seller_id, category, seller_state,
               customer_unique_id, customer_state, is_eligible,
               date(order_purchase_timestamp) AS order_date,
               price, freight_value
          FROM item_base""",
    # ---- dimensions ------------------------------------------------------
    "dim_customer": """
        SELECT cb.customer_unique_id, cb.customer_state, cb.orders, cb.gmv,
               cb.first_order_ts, cb.last_order_ts, cb.customer_type,
               rf.recency_days, rf.r_score, rf.f_score, rf.m_score, rf.rfm_total, rf.segment
          FROM customer_base cb
          LEFT JOIN customer_rfm rf USING(customer_unique_id)""",
    "dim_product": """
        SELECT product_id, category, category_pt, product_weight_g, product_photos_qty
          FROM product_dim""",
    "dim_seller": "SELECT seller_id, seller_city, seller_state FROM sellers",
    # A contiguous date table is required for any time-intelligence DAX
    # (DATEADD, SAMEPERIODLASTYEAR). Power BI's auto date/time hierarchy is
    # per-column and cannot be shared across two fact tables.
    "dim_date": """
        WITH RECURSIVE d(day) AS (
            SELECT date((SELECT MIN(order_purchase_timestamp) FROM order_base))
            UNION ALL
            SELECT date(day, '+1 day') FROM d
             WHERE day < date((SELECT MAX(order_purchase_timestamp) FROM order_base))
        )
        SELECT day AS order_date,
               CAST(strftime('%Y', day) AS INTEGER) AS year,
               CAST(strftime('%m', day) AS INTEGER) AS month_number,
               strftime('%Y-%m', day) AS year_month,
               CAST(strftime('%d', day) AS INTEGER) AS day_of_month,
               CAST(strftime('%w', day) AS INTEGER) AS weekday_number,
               CASE strftime('%w', day) WHEN '0' THEN 'Sun' WHEN '1' THEN 'Mon'
                    WHEN '2' THEN 'Tue' WHEN '3' THEN 'Wed' WHEN '4' THEN 'Thu'
                    WHEN '5' THEN 'Fri' ELSE 'Sat' END AS weekday_name,
               CASE WHEN strftime('%Y-%m', day) BETWEEN '2017-01' AND '2018-08'
                    THEN 1 ELSE 0 END AS in_trend_window
          FROM d""",
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ROOT / "data" / "olist.db"))
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"database not found: {db}\nrun: python scripts/build_db.py")
    OUT.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db) as conn:
        missing = [n for n in ("order_base", "customer_rfm")
                   if not conn.execute(
                       "SELECT 1 FROM sqlite_master WHERE name=?", (n,)).fetchone()]
        if missing:
            raise SystemExit(
                f"missing table(s): {', '.join(missing)}\n"
                "run: python scripts/run_analysis.py (it builds the full SQL layer)")
        for name, sql in EXPORTS.items():
            df = pd.read_sql_query(sql, conn)
            path = OUT / f"{name}.csv"
            df.to_csv(path, index=False)
            mb = path.stat().st_size / 1_048_576
            print(f"  {name:<20} {len(df):>9,} rows  {mb:>6.1f} MB")

    print(f"\nwrote {len(EXPORTS)} files to {OUT.relative_to(ROOT)}/")
    print("next: open Power BI Desktop -> Get Data -> Folder -> select that directory,")
    print("      then follow dashboard/powerbi_build_guide.md")


if __name__ == "__main__":
    main()
