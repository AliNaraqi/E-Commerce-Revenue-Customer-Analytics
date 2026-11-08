-- =============================================================================
-- 02_sales_kpis.sql — sales, GMV, AOV, category and geography
-- Requires 00_analytical_tables.sql. Basis: is_eligible = 1 (delivered orders).
-- GMV = SUM(order_items.price); freight reported separately; GMV ≠ revenue.
-- =============================================================================

-- Q: headline KPIs for the full observed period ------------------------------
SELECT 'headline' AS block,
       COUNT(*)                                   AS orders,
       ROUND(SUM(gmv), 2)                         AS gmv,
       ROUND(SUM(freight), 2)                     AS freight,
       SUM(item_count)                            AS items,
       ROUND(SUM(gmv) / NULLIF(COUNT(*), 0), 2)   AS aov,
       ROUND(1.0 * SUM(item_count) / NULLIF(COUNT(*), 0), 3) AS items_per_order,
       ROUND(100.0 * SUM(freight) / NULLIF(SUM(gmv + freight), 0), 2) AS freight_pct_of_spend,
       COUNT(DISTINCT customer_unique_id)         AS active_customers
  FROM order_base
 WHERE is_eligible = 1;

-- Q: how much does the eligibility rule move the numbers? --------------------
-- Guards the choice made in 00_analytical_tables: the spread is small enough
-- that the headline story does not depend on it.
SELECT 'eligibility_sensitivity' AS block, basis, orders, gmv, aov,
       ROUND(100.0 * (gmv - FIRST_VALUE(gmv) OVER (ORDER BY orders))
             / FIRST_VALUE(gmv) OVER (ORDER BY orders), 2) AS gmv_pct_vs_delivered
  FROM (
    SELECT 'delivered (reporting basis)' AS basis, COUNT(*) AS orders,
           ROUND(SUM(gmv), 2) AS gmv, ROUND(SUM(gmv) / COUNT(*), 2) AS aov
      FROM order_base WHERE order_status = 'delivered'
    UNION ALL
    SELECT 'not canceled/unavailable', COUNT(*), ROUND(SUM(gmv), 2), ROUND(SUM(gmv) / COUNT(*), 2)
      FROM order_base WHERE order_status NOT IN ('canceled', 'unavailable') AND item_count > 0
    UNION ALL
    SELECT 'all statuses', COUNT(*), ROUND(SUM(gmv), 2), ROUND(SUM(gmv) / COUNT(*), 2)
      FROM order_base WHERE item_count > 0
  )
 ORDER BY orders;

-- Q1/Q3: monthly orders, GMV, AOV and month-over-month growth ----------------
-- Window restricted to full-coverage months (see validation check 7).
WITH monthly AS (
    SELECT order_month,
           COUNT(*)                                 AS orders,
           ROUND(SUM(gmv), 2)                       AS gmv,
           ROUND(SUM(gmv) / NULLIF(COUNT(*), 0), 2) AS aov,
           COUNT(DISTINCT customer_unique_id)       AS active_customers
      FROM order_base
     WHERE is_eligible = 1
       AND order_month BETWEEN '2017-01' AND '2018-08'
     GROUP BY order_month
)
SELECT 'monthly_trend' AS block, order_month, orders, gmv, aov, active_customers,
       LAG(gmv) OVER (ORDER BY order_month) AS prev_gmv,
       ROUND(100.0 * (gmv - LAG(gmv) OVER (ORDER BY order_month))
             / NULLIF(LAG(gmv) OVER (ORDER BY order_month), 0), 2) AS gmv_mom_pct
  FROM monthly
 ORDER BY order_month;

-- Q2: category contribution, with a running share to expose concentration ----
WITH cat AS (
    SELECT category,
           COUNT(DISTINCT order_id) AS orders,
           COUNT(*)                 AS items,
           ROUND(SUM(price), 2)     AS gmv,
           ROUND(AVG(price), 2)     AS avg_item_price
      FROM item_base
     WHERE is_eligible = 1
     GROUP BY category
)
SELECT 'category_contribution' AS block,
       ROW_NUMBER() OVER (ORDER BY gmv DESC) AS rank_,
       category, orders, items, gmv, avg_item_price,
       ROUND(100.0 * gmv / SUM(gmv) OVER (), 2) AS pct_of_gmv,
       ROUND(100.0 * SUM(gmv) OVER (ORDER BY gmv DESC
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) / SUM(gmv) OVER (), 2) AS cumulative_pct
  FROM cat
 ORDER BY gmv DESC;

-- Q1: geography — state-level GMV, AOV and delivery/satisfaction context -----
SELECT 'state_performance' AS block,
       ob.customer_state,
       COUNT(*)                                       AS orders,
       ROUND(SUM(ob.gmv), 2)                          AS gmv,
       ROUND(100.0 * SUM(ob.gmv) / SUM(SUM(ob.gmv)) OVER (), 2) AS pct_of_gmv,
       ROUND(SUM(ob.gmv) / NULLIF(COUNT(*), 0), 2)    AS aov,
       ROUND(AVG(ob.freight), 2)                      AS avg_freight,
       ROUND(AVG(ob.days_to_deliver), 1)              AS avg_days_to_deliver,
       ROUND(100.0 * AVG(ob.is_on_time), 2)           AS on_time_pct,
       ROUND(AVG(r.review_score), 3)                  AS avg_review
  FROM order_base AS ob
  LEFT JOIN order_review AS r ON ob.order_id = r.order_id
 WHERE ob.is_eligible = 1
 GROUP BY ob.customer_state
 ORDER BY gmv DESC;

-- Q2: top products -----------------------------------------------------------
SELECT 'top_products' AS block,
       ROW_NUMBER() OVER (ORDER BY SUM(ib.price) DESC) AS rank_,
       ib.product_id, ib.category,
       COUNT(*) AS units, ROUND(SUM(ib.price), 2) AS gmv, ROUND(AVG(ib.price), 2) AS avg_price,
       ROUND(AVG(r.review_score), 2) AS avg_review
  FROM item_base AS ib
  LEFT JOIN order_review AS r ON ib.order_id = r.order_id
 WHERE ib.is_eligible = 1
 GROUP BY ib.product_id, ib.category
 ORDER BY gmv DESC
 LIMIT 20;

-- Q2: seller concentration ---------------------------------------------------
WITH s AS (
    SELECT seller_id, seller_state, COUNT(DISTINCT order_id) AS orders,
           ROUND(SUM(price), 2) AS gmv
      FROM item_base WHERE is_eligible = 1 GROUP BY seller_id, seller_state
)
SELECT 'seller_concentration' AS block,
       COUNT(*) AS sellers,
       ROUND(100.0 * SUM(CASE WHEN rnk <= 10  THEN gmv ELSE 0 END) / SUM(gmv), 2) AS top10_gmv_pct,
       ROUND(100.0 * SUM(CASE WHEN rnk <= 100 THEN gmv ELSE 0 END) / SUM(gmv), 2) AS top100_gmv_pct
  FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY gmv DESC) AS rnk FROM s);

-- Q: payment mix -------------------------------------------------------------
SELECT 'payment_mix' AS block,
       primary_payment_type,
       COUNT(*) AS orders,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_orders,
       ROUND(SUM(gmv), 2) AS gmv,
       ROUND(SUM(gmv) / NULLIF(COUNT(*), 0), 2) AS aov,
       ROUND(AVG(max_installments), 2) AS avg_installments
  FROM order_base
 WHERE is_eligible = 1 AND primary_payment_type IS NOT NULL
 GROUP BY primary_payment_type
 ORDER BY gmv DESC;
