# Data

The raw Olist CSVs are **not committed** — they are ~126 MB, and the build
reproduces everything from them.

## Getting the data

1. Download the [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
   (Kaggle account required; licensed CC BY-NC-SA 4.0).
2. Unzip the nine CSVs into `archive/` at the repo root (or any directory — pass
   it with `--raw-dir`).
3. Build the database:

   ```bash
   python scripts/build_db.py --raw-dir archive --db data/olist.db
   ```

## Expected files

| File | Rows |
| --- | --- |
| `olist_orders_dataset.csv` | 99,441 |
| `olist_order_items_dataset.csv` | 112,650 |
| `olist_order_payments_dataset.csv` | 103,886 |
| `olist_order_reviews_dataset.csv` | 99,224 |
| `olist_customers_dataset.csv` | 99,441 |
| `olist_products_dataset.csv` | 32,951 |
| `olist_sellers_dataset.csv` | 3,095 |
| `olist_geolocation_dataset.csv` | 1,000,163 |
| `product_category_name_translation.csv` | 71 |

`build_db.py` prints its row counts; they should match this table exactly. A
mismatch means a truncated download.

## Generated, not committed

- `data/olist.db` — ~150 MB SQLite database
- `dashboard/model/*.csv` — ~53 MB Power BI star-schema export

Both are listed in `.gitignore`. `outputs/` **is** committed: the tables and
figures are small and are the published result of the analysis.
