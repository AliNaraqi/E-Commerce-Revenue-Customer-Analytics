-- =============================================================================
-- 00_analytical_tables.sql — canonical grain + KPI eligibility rules
-- Engine: SQLite (see PORTING notes). Run FIRST; every later script reads these
-- tables, so one definition change propagates everywhere.
-- =============================================================================
--
-- WHY TABLES AND NOT VIEWS
--   These were views first. Because SQLite re-evaluates a view on every
--   reference, each of the ~20 downstream queries re-ran the full item and
--   payment roll-ups — the analysis took minutes. Materialising once and
--   indexing turns the whole run into seconds. The definitions below are still
--   the single source of truth; re-run this file after any data refresh.
--
-- GRAIN CONTRACT
--   order_base   : 1 row per order_id              (order header + money rolled up)
--   item_base    : 1 row per order_id+order_item_id (never re-aggregate money
--                  here after joining to order_base — that is the fan-out trap)
--   customer_base: 1 row per customer_unique_id
--   order_review : 1 row per order_id
--
-- ELIGIBILITY RULE (stored as `is_eligible = 1`)
--   order_status = 'delivered'. Rationale: 97.0% of all orders, the only status
--   with complete money + delivery timestamps, and the only basis on which
--   delivery and review KPIs are defined at all. Sensitivity against looser
--   bases is reported in 02_sales_kpis.sql — the spread is under 2.8% of GMV.
--
-- MONEY DEFINITIONS
--   gmv     = SUM(order_items.price)         -- merchandise value, freight excluded
--   freight = SUM(order_items.freight_value) -- always reported separately
--   GMV IS NOT REVENUE. Olist's commission take, product cost and marketing
--   spend are absent from this dataset.
--
-- PORTING TO POSTGRESQL
--   substr(ts,1,7)              -> to_char(ts, 'YYYY-MM')
--   julianday(a) - julianday(b) -> EXTRACT(EPOCH FROM (a - b)) / 86400.0
--   date(x)                     -> x::date
--   CREATE TABLE ... AS SELECT and every window function are unchanged.
-- =============================================================================

-- Idempotent: safe to re-run at any time.
DROP TABLE IF EXISTS customer_rfm;   -- defined in 03, dropped here so the
                                     -- base layer always rebuilds cleanly
DROP TABLE IF EXISTS product_dim;
DROP TABLE IF EXISTS order_review;
DROP TABLE IF EXISTS order_base;
DROP TABLE IF EXISTS item_base;
DROP TABLE IF EXISTS customer_base;

-- Product dimension with English category names. Two live categories are absent
-- from the vendor translation file and are patched here; genuinely unlabelled
-- products become 'unknown' rather than NULL so they survive a GROUP BY.
CREATE TABLE product_dim AS
SELECT
    p.product_id,
    p.product_category_name AS category_pt,
    COALESCE(
        t.product_category_name_english,
        CASE p.product_category_name
            WHEN 'portateis_cozinha_e_preparadores_de_alimentos'
                THEN 'small_kitchen_appliances_and_food_preparers'
            WHEN 'pc_gamer' THEN 'pc_gamer'
        END,
        'unknown'
    ) AS category,
    p.product_weight_g,
    p.product_photos_qty
FROM products AS p
LEFT JOIN product_category_translation AS t
    ON p.product_category_name = t.product_category_name;

-- Review score collapsed to order grain. review_id is not unique (789 ids are
-- reused) and 547 orders carry more than one review row, so joining
-- order_reviews directly to orders would multiply those orders. Averaging keeps
-- the measure comparable to every other order-level KPI.
CREATE TABLE order_review AS
SELECT
    order_id,
    AVG(review_score)         AS review_score,
    COUNT(*)                  AS review_rows,
    MIN(review_creation_date) AS first_review_date
FROM order_reviews
GROUP BY order_id;

-- One row per order. Money is pre-aggregated from order_items so that any later
-- join to customers, dates or reviews cannot multiply it.
CREATE TABLE order_base AS
WITH item_rollup AS (
    SELECT
        order_id,
        SUM(price)                 AS gmv,
        SUM(freight_value)         AS freight,
        COUNT(*)                   AS item_count,
        COUNT(DISTINCT product_id) AS distinct_products,
        COUNT(DISTINCT seller_id)  AS distinct_sellers
    FROM order_items
    GROUP BY order_id
),
payment_ranked AS (
    -- The payment row carrying the largest share of the order, picked with a
    -- window function rather than a correlated subquery (which re-scanned
    -- order_payments once per order).
    SELECT order_id, payment_type,
           ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY payment_value DESC) AS rn
    FROM order_payments
),
payment_rollup AS (
    SELECT
        p.order_id,
        SUM(p.payment_value)        AS paid_value,
        MAX(p.payment_installments) AS max_installments,
        MAX(pr.payment_type)        AS primary_payment_type
    FROM order_payments AS p
    LEFT JOIN payment_ranked AS pr
        ON p.order_id = pr.order_id AND pr.rn = 1
    GROUP BY p.order_id
)
SELECT
    o.order_id,
    o.customer_id,
    c.customer_unique_id,
    c.customer_state,
    c.customer_city,
    o.order_status,
    CASE WHEN o.order_status = 'delivered' THEN 1 ELSE 0 END AS is_eligible,
    o.order_purchase_timestamp,
    substr(o.order_purchase_timestamp, 1, 7) AS order_month,
    substr(o.order_purchase_timestamp, 1, 4) AS order_year,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    COALESCE(i.gmv, 0)        AS gmv,
    COALESCE(i.freight, 0)    AS freight,
    COALESCE(i.item_count, 0) AS item_count,
    i.distinct_products,
    i.distinct_sellers,
    pm.paid_value,
    pm.max_installments,
    pm.primary_payment_type,
    -- Delivery measures are NULL unless the delivery date exists, so rates and
    -- averages exclude those orders instead of scoring them as late.
    CASE WHEN o.order_delivered_customer_date IS NOT NULL
         THEN julianday(o.order_delivered_customer_date)
            - julianday(o.order_purchase_timestamp) END AS days_to_deliver,
    CASE WHEN o.order_delivered_customer_date IS NOT NULL
         THEN julianday(date(o.order_delivered_customer_date))
            - julianday(date(o.order_estimated_delivery_date)) END AS delivery_vs_estimate_days,
    CASE WHEN o.order_delivered_customer_date IS NULL THEN NULL
         WHEN date(o.order_delivered_customer_date)
              <= date(o.order_estimated_delivery_date) THEN 1
         ELSE 0 END AS is_on_time
FROM orders AS o
JOIN customers AS c ON o.customer_id = c.customer_id
LEFT JOIN item_rollup    AS i  ON o.order_id = i.order_id
LEFT JOIN payment_rollup AS pm ON o.order_id = pm.order_id;

CREATE UNIQUE INDEX ix_ob_order ON order_base(order_id);
CREATE INDEX ix_ob_customer ON order_base(customer_unique_id);
CREATE INDEX ix_ob_month ON order_base(order_month);
CREATE INDEX ix_ob_eligible ON order_base(is_eligible);
CREATE UNIQUE INDEX ix_orv_order ON order_review(order_id);

-- One row per order line. Use this for category, product and seller analysis.
CREATE TABLE item_base AS
SELECT
    i.order_id,
    i.order_item_id,
    i.product_id,
    i.seller_id,
    d.category,
    s.seller_state,
    i.price,
    i.freight_value,
    ob.customer_unique_id,
    ob.customer_state,
    ob.order_status,
    ob.is_eligible,
    ob.order_month,
    ob.order_purchase_timestamp
FROM order_items AS i
JOIN order_base AS ob ON i.order_id = ob.order_id
LEFT JOIN product_dim AS d ON i.product_id = d.product_id
LEFT JOIN sellers     AS s ON i.seller_id = s.seller_id;

CREATE INDEX ix_ib_order ON item_base(order_id);
CREATE INDEX ix_ib_category ON item_base(category);
CREATE INDEX ix_ib_seller ON item_base(seller_id);

-- One row per real customer. customer_id is regenerated per order in this
-- dataset; customer_unique_id is the person. Keying customer metrics on
-- customer_id reports a 0% repeat rate — a silent, classic error on this source.
CREATE TABLE customer_base AS
SELECT
    ob.customer_unique_id,
    MIN(ob.customer_state)           AS customer_state,
    COUNT(DISTINCT ob.order_id)      AS orders,
    SUM(ob.gmv)                      AS gmv,
    SUM(ob.item_count)               AS items,
    MIN(ob.order_purchase_timestamp) AS first_order_ts,
    MAX(ob.order_purchase_timestamp) AS last_order_ts,
    CASE WHEN COUNT(DISTINCT ob.order_id) > 1 THEN 'repeat' ELSE 'one-time' END AS customer_type
FROM order_base AS ob
WHERE ob.is_eligible = 1
GROUP BY ob.customer_unique_id;

CREATE UNIQUE INDEX ix_cb_customer ON customer_base(customer_unique_id);

ANALYZE;
