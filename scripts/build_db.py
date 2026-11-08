"""Load the Olist CSV extract into a SQLite database with typed columns.

Usage:
    python scripts/build_db.py [--raw-dir archive] [--db data/olist.db]

The raw CSVs are read with pandas, date columns are normalised to ISO-8601
text (SQLite's preferred datetime storage) and numeric columns are cast, so
downstream SQL can rely on types instead of defensive casting.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

# table name -> (csv file, date columns, numeric columns)
TABLES: dict[str, tuple[str, list[str], list[str]]] = {
    "orders": (
        "olist_orders_dataset.csv",
        [
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
        [],
    ),
    "order_items": (
        "olist_order_items_dataset.csv",
        ["shipping_limit_date"],
        ["order_item_id", "price", "freight_value"],
    ),
    "order_payments": (
        "olist_order_payments_dataset.csv",
        [],
        ["payment_sequential", "payment_installments", "payment_value"],
    ),
    "order_reviews": (
        "olist_order_reviews_dataset.csv",
        ["review_creation_date", "review_answer_timestamp"],
        ["review_score"],
    ),
    "customers": (
        "olist_customers_dataset.csv",
        [],
        ["customer_zip_code_prefix"],
    ),
    "products": (
        "olist_products_dataset.csv",
        [],
        [
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        ],
    ),
    "sellers": (
        "olist_sellers_dataset.csv",
        [],
        ["seller_zip_code_prefix"],
    ),
    "geolocation": (
        "olist_geolocation_dataset.csv",
        [],
        ["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng"],
    ),
    "product_category_translation": (
        "product_category_name_translation.csv",
        [],
        [],
    ),
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_orders_customer ON orders(customer_id)",
    "CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(order_status)",
    "CREATE INDEX IF NOT EXISTS ix_items_order ON order_items(order_id)",
    "CREATE INDEX IF NOT EXISTS ix_items_product ON order_items(product_id)",
    "CREATE INDEX IF NOT EXISTS ix_items_seller ON order_items(seller_id)",
    "CREATE INDEX IF NOT EXISTS ix_payments_order ON order_payments(order_id)",
    "CREATE INDEX IF NOT EXISTS ix_reviews_order ON order_reviews(order_id)",
    "CREATE INDEX IF NOT EXISTS ix_customers_unique ON customers(customer_unique_id)",
    "CREATE INDEX IF NOT EXISTS ix_geo_zip ON geolocation(geolocation_zip_code_prefix)",
]


def load_table(path: Path, date_cols: list[str], num_cols: list[str]) -> pd.DataFrame:
    # utf-8-sig strips the BOM present in the category-translation file.
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=True)
    df.columns = [c.strip() for c in df.columns]
    for col in date_cols:
        parsed = pd.to_datetime(df[col], errors="coerce")
        df[col] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S")
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in df.columns:
        if col not in date_cols and col not in num_cols:
            df[col] = df[col].str.strip()
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="archive", help="directory holding the Olist CSVs")
    parser.add_argument("--db", default="data/olist.db", help="SQLite database to (re)create")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    if not raw_dir.exists():
        raise SystemExit(f"raw directory not found: {raw_dir}")

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(db_path) as conn:
        for table, (csv_name, date_cols, num_cols) in TABLES.items():
            csv_path = raw_dir / csv_name
            if not csv_path.exists():
                raise SystemExit(f"missing expected file: {csv_path}")
            df = load_table(csv_path, date_cols, num_cols)
            df.to_sql(table, conn, if_exists="replace", index=False)
            print(f"{table:<30} {len(df):>9,} rows  x {len(df.columns)} cols")
        for stmt in INDEXES:
            conn.execute(stmt)
        conn.execute("ANALYZE")

    size_mb = db_path.stat().st_size / 1_048_576
    print(f"\nwrote {db_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
