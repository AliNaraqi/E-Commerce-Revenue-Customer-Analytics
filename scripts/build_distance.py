"""Add a seller→customer distance layer, so delivery findings can be controlled.

Usage:
    python scripts/build_distance.py    # creates zip_geo and order_distance

WHY THIS EXISTS
    The headline operational finding — "Rio de Janeiro is slow for reasons other
    than distance" — was originally asserted from average delivery days alone.
    That is not evidence: RJ could simply be further from the seller base. This
    script builds the control that tests it.

WHY PYTHON AND NOT SQL
    Haversine needs sin/cos/radians. SQLite ships those only when compiled with
    SQLITE_ENABLE_MATH_FUNCTIONS, which this build (and many system builds) is
    not. Computing it in numpy keeps the project runnable on a stock SQLite; the
    output is written back as a table so the SQL layer can join to it normally.

CENTROID CHOICE
    geolocation holds ~53 points per zip prefix and a few dozen points outside
    Brazil entirely (geocoder errors landing in China and the US). The centroid
    is therefore the MEDIAN lat/lng of points inside a Brazil bounding box —
    the mean would be dragged by those outliers.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# Generous bounding box for Brazil, used only to drop geocoder errors.
BR_LAT = (-34.0, 5.3)
BR_LNG = (-74.0, -34.0)
EARTH_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Great-circle distance in km. Vectorised over numpy arrays.

    Great-circle, not road distance: it understates real freight distance,
    especially where terrain or coastline forces a detour. It is a control
    variable here, not a logistics estimate.
    """
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(a))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ROOT / "data" / "olist.db"))
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"database not found: {db}\nrun: python scripts/build_db.py")

    conn = sqlite3.connect(db)
    if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='order_base'").fetchone():
        raise SystemExit("order_base missing — run sql/00_analytical_tables.sql first")

    # ---- 1. one centroid per zip prefix -----------------------------------
    geo = pd.read_sql_query("""
        SELECT geolocation_zip_code_prefix AS zip,
               geolocation_lat AS lat, geolocation_lng AS lng
          FROM geolocation
    """, conn)
    before = len(geo)
    geo = geo[geo.lat.between(*BR_LAT) & geo.lng.between(*BR_LNG)]
    print(f"geolocation points: {before:,} -> {len(geo):,} "
          f"({before - len(geo)} outside Brazil, dropped)")

    zip_geo = (geo.groupby("zip", as_index=False)
                  .agg(lat=("lat", "median"), lng=("lng", "median"),
                       points=("lat", "size")))
    zip_geo.to_sql("zip_geo", conn, if_exists="replace", index=False)
    print(f"zip_geo: {len(zip_geo):,} zip prefixes")

    # ---- 2. seller -> customer distance per order -------------------------
    # Grain guard: an order can span several sellers, so this aggregates to
    # order level (max = the leg that gates delivery; mean kept for contrast)
    # BEFORE joining anything else.
    legs = pd.read_sql_query("""
        SELECT DISTINCT ob.order_id, i.seller_id,
               c.customer_zip_code_prefix AS cust_zip,
               s.seller_zip_code_prefix   AS sell_zip
          FROM order_base ob
          JOIN order_items i ON ob.order_id = i.order_id
          JOIN customers   c ON ob.customer_id = c.customer_id
          JOIN sellers     s ON i.seller_id = s.seller_id
    """, conn)

    z = zip_geo.set_index("zip")[["lat", "lng"]]
    legs = (legs.join(z.rename(columns={"lat": "c_lat", "lng": "c_lng"}), on="cust_zip")
                .join(z.rename(columns={"lat": "s_lat", "lng": "s_lng"}), on="sell_zip"))
    resolved = legs.dropna(subset=["c_lat", "s_lat"])
    print(f"order legs: {len(legs):,}; with both endpoints geocoded: "
          f"{len(resolved):,} ({100 * len(resolved) / len(legs):.1f}%)")

    resolved = resolved.copy()
    resolved["distance_km"] = haversine_km(
        resolved.s_lat.values, resolved.s_lng.values,
        resolved.c_lat.values, resolved.c_lng.values)

    order_distance = (resolved.groupby("order_id", as_index=False)
                              .agg(distance_km=("distance_km", "max"),
                                   mean_leg_km=("distance_km", "mean"),
                                   seller_legs=("seller_id", "nunique")))
    order_distance.to_sql("order_distance", conn, if_exists="replace", index=False)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_od_order "
                 "ON order_distance(order_id)")

    total_orders = conn.execute(
        "SELECT COUNT(*) FROM order_base WHERE is_eligible=1").fetchone()[0]
    covered = conn.execute("""
        SELECT COUNT(*) FROM order_base ob JOIN order_distance d USING(order_id)
         WHERE ob.is_eligible=1""").fetchone()[0]
    print(f"order_distance: {len(order_distance):,} orders "
          f"({100 * covered / total_orders:.1f}% of delivered orders covered)")
    print(f"distance km — median {order_distance.distance_km.median():.0f}, "
          f"p90 {order_distance.distance_km.quantile(0.9):.0f}, "
          f"max {order_distance.distance_km.max():.0f}")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
