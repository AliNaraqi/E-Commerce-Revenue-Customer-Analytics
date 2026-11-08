-- =============================================================================
-- 01_data_validation.sql — run before trusting any KPI
-- Each block returns a `check` label and a `status` of PASS / REVIEW so the
-- output can be scanned top-to-bottom. REVIEW means "known and handled", not
-- "broken" — the handling is stated in the comment above each block.
-- =============================================================================

-- 1. PRIMARY KEY UNIQUENESS -------------------------------------------------
-- review_id is NOT unique (789 ids reused) and order_id is not unique inside
-- order_reviews, which is why 00_analytical_tables collapses reviews to order
-- grain before joining.
SELECT '1. key uniqueness' AS section, * FROM (
    SELECT 'orders.order_id' AS key_name, COUNT(*) AS rows_, COUNT(DISTINCT order_id) AS distinct_,
           CASE WHEN COUNT(*) = COUNT(DISTINCT order_id) THEN 'PASS' ELSE 'REVIEW' END AS status
      FROM orders
    UNION ALL SELECT 'customers.customer_id', COUNT(*), COUNT(DISTINCT customer_id),
           CASE WHEN COUNT(*) = COUNT(DISTINCT customer_id) THEN 'PASS' ELSE 'REVIEW' END FROM customers
    UNION ALL SELECT 'customers.customer_unique_id', COUNT(*), COUNT(DISTINCT customer_unique_id),
           'EXPECTED DUPES (person vs order)' FROM customers
    UNION ALL SELECT 'products.product_id', COUNT(*), COUNT(DISTINCT product_id),
           CASE WHEN COUNT(*) = COUNT(DISTINCT product_id) THEN 'PASS' ELSE 'REVIEW' END FROM products
    UNION ALL SELECT 'sellers.seller_id', COUNT(*), COUNT(DISTINCT seller_id),
           CASE WHEN COUNT(*) = COUNT(DISTINCT seller_id) THEN 'PASS' ELSE 'REVIEW' END FROM sellers
    UNION ALL SELECT 'order_reviews.review_id', COUNT(*), COUNT(DISTINCT review_id),
           CASE WHEN COUNT(*) = COUNT(DISTINCT review_id) THEN 'PASS' ELSE 'REVIEW' END FROM order_reviews
    UNION ALL SELECT 'order_reviews.order_id', COUNT(*), COUNT(DISTINCT order_id),
           CASE WHEN COUNT(*) = COUNT(DISTINCT order_id) THEN 'PASS' ELSE 'REVIEW' END FROM order_reviews
);

-- 2. REFERENTIAL INTEGRITY ---------------------------------------------------
-- 775 orders have no order_items; 603 of those are status 'unavailable' and 164
-- 'canceled', so the eligibility filter removes them from GMV anyway.
SELECT '2. referential integrity' AS section, * FROM (
    SELECT 'order_items -> orders (orphans)' AS check_, COUNT(*) AS n,
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'REVIEW' END AS status
      FROM order_items i LEFT JOIN orders o ON i.order_id = o.order_id WHERE o.order_id IS NULL
    UNION ALL SELECT 'orders -> customers (orphans)', COUNT(*),
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'REVIEW' END
      FROM orders o LEFT JOIN customers c ON o.customer_id = c.customer_id WHERE c.customer_id IS NULL
    UNION ALL SELECT 'order_items -> products (orphans)', COUNT(*),
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'REVIEW' END
      FROM order_items i LEFT JOIN products p ON i.product_id = p.product_id WHERE p.product_id IS NULL
    UNION ALL SELECT 'order_items -> sellers (orphans)', COUNT(*),
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'REVIEW' END
      FROM order_items i LEFT JOIN sellers s ON i.seller_id = s.seller_id WHERE s.seller_id IS NULL
    UNION ALL SELECT 'orders with zero order_items', COUNT(*), 'REVIEW — excluded by eligibility'
      FROM orders o LEFT JOIN (SELECT DISTINCT order_id FROM order_items) i ON o.order_id = i.order_id
     WHERE i.order_id IS NULL
    UNION ALL SELECT 'eligible orders with zero items', COUNT(*),
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'REVIEW' END
      FROM order_base WHERE is_eligible = 1 AND item_count = 0
);

-- 3. MISSINGNESS ON DELIVERY TIMESTAMPS --------------------------------------
-- 8 delivered orders lack a customer-delivery date. order_base sets their
-- delivery measures to NULL so they drop out of rates rather than count as late.
SELECT '3. delivery timestamp completeness' AS section, * FROM (
    SELECT order_status AS status_,
           COUNT(*) AS orders_,
           SUM(CASE WHEN order_delivered_customer_date IS NULL THEN 1 ELSE 0 END) AS missing_delivery_date,
           SUM(CASE WHEN order_approved_at IS NULL THEN 1 ELSE 0 END) AS missing_approved_at
      FROM orders GROUP BY order_status ORDER BY orders_ DESC
);

-- 4. MONEY RECONCILIATION ----------------------------------------------------
-- Independent cross-check: payments should ≈ item price + freight. A tight gap
-- proves the item-level join grain is right. Observed gap: R$2,870 / 0.02%.
SELECT '4. money reconciliation' AS section, * FROM (
    WITH it AS (SELECT order_id, SUM(price) AS p, SUM(freight_value) AS f FROM order_items GROUP BY order_id),
         pm AS (SELECT order_id, SUM(payment_value) AS pv FROM order_payments GROUP BY order_id)
    SELECT ROUND(SUM(it.p), 2) AS items_price,
           ROUND(SUM(it.f), 2) AS freight,
           ROUND(SUM(pm.pv), 2) AS payments,
           ROUND(SUM(pm.pv) - SUM(it.p) - SUM(it.f), 2) AS gap,
           ROUND(100.0 * (SUM(pm.pv) - SUM(it.p) - SUM(it.f)) / SUM(pm.pv), 4) AS gap_pct,
           CASE WHEN ABS(SUM(pm.pv) - SUM(it.p) - SUM(it.f)) / SUM(pm.pv) < 0.01
                THEN 'PASS' ELSE 'REVIEW' END AS status
      FROM it JOIN pm ON it.order_id = pm.order_id
);

-- 5. VALUE RANGES ------------------------------------------------------------
SELECT '5. value ranges' AS section, * FROM (
    SELECT 'item price' AS measure, MIN(price) AS min_, MAX(price) AS max_,
           SUM(CASE WHEN price <= 0 THEN 1 ELSE 0 END) AS non_positive,
           CASE WHEN SUM(CASE WHEN price <= 0 THEN 1 ELSE 0 END) = 0 THEN 'PASS' ELSE 'REVIEW' END AS status
      FROM order_items
    UNION ALL SELECT 'freight value', MIN(freight_value), MAX(freight_value),
           SUM(CASE WHEN freight_value < 0 THEN 1 ELSE 0 END),
           CASE WHEN SUM(CASE WHEN freight_value < 0 THEN 1 ELSE 0 END) = 0 THEN 'PASS' ELSE 'REVIEW' END
      FROM order_items
    UNION ALL SELECT 'review score', MIN(review_score), MAX(review_score),
           SUM(CASE WHEN review_score NOT BETWEEN 1 AND 5 THEN 1 ELSE 0 END),
           CASE WHEN SUM(CASE WHEN review_score NOT BETWEEN 1 AND 5 THEN 1 ELSE 0 END) = 0 THEN 'PASS' ELSE 'REVIEW' END
      FROM order_reviews
);

-- 6. DATE LOGIC --------------------------------------------------------------
SELECT '6. date logic' AS section, * FROM (
    SELECT 'delivered before purchase' AS check_, COUNT(*) AS n,
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'REVIEW' END AS status
      FROM orders WHERE order_delivered_customer_date < order_purchase_timestamp
    UNION ALL SELECT 'approved before purchase', COUNT(*),
           CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'REVIEW' END
      FROM orders WHERE order_approved_at < order_purchase_timestamp
);

-- 7. PERIOD COVERAGE ---------------------------------------------------------
-- Drives the reporting window. 2016-09, 2016-10 and 2016-12 are a pilot-scale
-- trickle and 2018-09/10 are truncated mid-collection, so month-over-month
-- trends are reported on 2017-01..2018-08 only.
SELECT '7. period coverage' AS section, * FROM (
    SELECT order_month, COUNT(*) AS orders_,
           CASE WHEN COUNT(*) < 500 THEN 'SPARSE — excluded from trend' ELSE 'in trend window' END AS status
      FROM order_base GROUP BY order_month ORDER BY order_month
);

-- 8. CATEGORY TRANSLATION COVERAGE ------------------------------------------
SELECT '8. category coverage' AS section, * FROM (
    SELECT category, COUNT(*) AS products_
      FROM product_dim WHERE category = 'unknown' GROUP BY category
);
