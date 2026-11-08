-- =============================================================================
-- 03_customer_analytics.sql — repeat behaviour, cohorts and RFM segmentation
-- Requires 00_analytical_tables.sql. Basis: is_eligible = 1 (delivered orders).
--
-- CRITICAL: every customer measure keys on customer_unique_id, NOT customer_id.
-- customer_id is regenerated per order in this dataset, so keying on it reports
-- a 0% repeat rate — a classic and silent error on this source.
--
-- SNAPSHOT DATE for recency = 2018-10-17, the last purchase timestamp in the
-- data. Recency is therefore "days before end of data", not days before today.
-- =============================================================================

-- Q4: one-time vs repeat customers ------------------------------------------
SELECT 'customer_type_summary' AS block,
       customer_type,
       COUNT(*)                                            AS customers,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)  AS pct_of_customers,
       SUM(orders)                                         AS orders,
       ROUND(SUM(gmv), 2)                                  AS gmv,
       ROUND(100.0 * SUM(gmv) / SUM(SUM(gmv)) OVER (), 2)  AS pct_of_gmv,
       ROUND(SUM(gmv) / NULLIF(COUNT(*), 0), 2)            AS gmv_per_customer,
       ROUND(1.0 * SUM(orders) / NULLIF(COUNT(*), 0), 2)   AS orders_per_customer,
       ROUND(SUM(gmv) / NULLIF(SUM(orders), 0), 2)         AS aov
  FROM customer_base
 GROUP BY customer_type
 ORDER BY customers DESC;

-- Q4: distribution of orders per customer ------------------------------------
SELECT 'orders_per_customer_dist' AS block,
       orders AS orders_placed,
       COUNT(*) AS customers,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 4) AS pct_of_customers,
       ROUND(SUM(gmv), 2) AS gmv
  FROM customer_base
 GROUP BY orders
 ORDER BY orders;

-- Q4: does the second order differ from the first? ---------------------------
-- Uses a window function over each customer's order sequence.
WITH seq AS (
    SELECT customer_unique_id, order_id, gmv, order_purchase_timestamp,
           ROW_NUMBER() OVER (PARTITION BY customer_unique_id
                              ORDER BY order_purchase_timestamp) AS order_seq
      FROM order_base
     WHERE is_eligible = 1
)
SELECT 'order_sequence_behaviour' AS block,
       order_seq,
       COUNT(*) AS orders,
       ROUND(AVG(gmv), 2) AS avg_order_value
  FROM seq
 WHERE order_seq <= 5
 GROUP BY order_seq
 ORDER BY order_seq;

-- Q4: days between first and second purchase (repeat customers only) ---------
WITH seq AS (
    SELECT customer_unique_id, order_purchase_timestamp,
           ROW_NUMBER() OVER (PARTITION BY customer_unique_id
                              ORDER BY order_purchase_timestamp) AS order_seq
      FROM order_base WHERE is_eligible = 1
),
gap AS (
    SELECT a.customer_unique_id,
           julianday(b.order_purchase_timestamp) - julianday(a.order_purchase_timestamp) AS days_to_second
      FROM seq AS a
      JOIN seq AS b
        ON a.customer_unique_id = b.customer_unique_id
       AND a.order_seq = 1 AND b.order_seq = 2
)
SELECT 'repeat_latency' AS block,
       COUNT(*) AS repeat_customers,
       ROUND(AVG(days_to_second), 1) AS avg_days_to_second_order,
       ROUND(100.0 * AVG(CASE WHEN days_to_second <= 30  THEN 1 ELSE 0 END), 1) AS pct_within_30d,
       ROUND(100.0 * AVG(CASE WHEN days_to_second <= 90  THEN 1 ELSE 0 END), 1) AS pct_within_90d,
       ROUND(100.0 * AVG(CASE WHEN days_to_second <= 180 THEN 1 ELSE 0 END), 1) AS pct_within_180d
  FROM gap;

-- Q7: RFM SEGMENTATION -------------------------------------------------------
-- Recency = days from last order to snapshot (lower is better)
-- Frequency = distinct eligible orders
-- Monetary = total GMV
-- Scored 1..5 by quintile with NTILE. Recency is reversed so 5 is always good.
-- Frequency quintiles collapse because 97% of customers have exactly 1 order,
-- so frequency is scored on a business rule instead of NTILE — scoring a
-- degenerate distribution with NTILE would assign different scores to
-- identical customers.
DROP TABLE IF EXISTS customer_rfm;
CREATE TABLE customer_rfm AS
WITH base AS (
    SELECT customer_unique_id, customer_state, orders, gmv,
           first_order_ts, last_order_ts,
           CAST(julianday('2018-10-17') - julianday(last_order_ts) AS INTEGER) AS recency_days
      FROM customer_base
),
scored AS (
    SELECT *,
           6 - NTILE(5) OVER (ORDER BY recency_days)       AS r_score,
           CASE WHEN orders >= 4 THEN 5
                WHEN orders = 3  THEN 4
                WHEN orders = 2  THEN 3
                ELSE 1 END                                 AS f_score,
           NTILE(5) OVER (ORDER BY gmv)                    AS m_score
      FROM base
)
SELECT *,
       r_score + f_score + m_score AS rfm_total,
       CASE
           WHEN f_score >= 3 AND r_score >= 4 AND m_score >= 4 THEN 'Champions'
           WHEN f_score >= 3 AND r_score >= 3                  THEN 'Loyal'
           WHEN f_score >= 3                                   THEN 'Repeat — lapsing'
           WHEN r_score >= 4 AND m_score >= 4                  THEN 'Promising high-value'
           WHEN r_score >= 4                                   THEN 'Recent one-time'
           WHEN r_score <= 2 AND m_score >= 4                  THEN 'At risk — high value'
           WHEN r_score <= 2                                   THEN 'Dormant'
           ELSE 'Occasional'
       END AS segment
  FROM scored;

SELECT 'rfm_segments' AS block,
       segment,
       COUNT(*)                                           AS customers,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_customers,
       ROUND(SUM(gmv), 2)                                 AS gmv,
       ROUND(100.0 * SUM(gmv) / SUM(SUM(gmv)) OVER (), 2) AS pct_of_gmv,
       ROUND(SUM(gmv) / NULLIF(COUNT(*), 0), 2)           AS gmv_per_customer,
       ROUND(AVG(recency_days), 0)                        AS avg_recency_days,
       ROUND(AVG(orders), 2)                              AS avg_orders
  FROM customer_rfm
 GROUP BY segment
 ORDER BY gmv DESC;

-- Q7: value concentration — how top-down is GMV? -----------------------------
WITH ranked AS (
    SELECT gmv, NTILE(10) OVER (ORDER BY gmv DESC) AS decile FROM customer_base
)
SELECT 'customer_value_deciles' AS block,
       decile,
       COUNT(*) AS customers,
       ROUND(SUM(gmv), 2) AS gmv,
       ROUND(100.0 * SUM(gmv) / SUM(SUM(gmv)) OVER (), 2) AS pct_of_gmv,
       ROUND(100.0 * SUM(SUM(gmv)) OVER (ORDER BY decile
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
             / SUM(SUM(gmv)) OVER (), 2) AS cumulative_pct_of_gmv
  FROM ranked
 GROUP BY decile
 ORDER BY decile;

-- Q4: monthly new vs returning customers -------------------------------------
-- A customer is "new" in the month containing their first eligible order.
WITH firsts AS (
    SELECT customer_unique_id, substr(MIN(order_purchase_timestamp), 1, 7) AS cohort_month
      FROM order_base WHERE is_eligible = 1 GROUP BY customer_unique_id
)
SELECT 'new_vs_returning_monthly' AS block,
       ob.order_month,
       COUNT(DISTINCT CASE WHEN f.cohort_month = ob.order_month
                           THEN ob.customer_unique_id END) AS new_customers,
       COUNT(DISTINCT CASE WHEN f.cohort_month < ob.order_month
                           THEN ob.customer_unique_id END) AS returning_customers,
       ROUND(SUM(CASE WHEN f.cohort_month < ob.order_month THEN ob.gmv ELSE 0 END), 2) AS returning_gmv,
       ROUND(100.0 * SUM(CASE WHEN f.cohort_month < ob.order_month THEN ob.gmv ELSE 0 END)
             / NULLIF(SUM(ob.gmv), 0), 2) AS returning_gmv_pct
  FROM order_base AS ob
  JOIN firsts AS f ON ob.customer_unique_id = f.customer_unique_id
 WHERE ob.is_eligible = 1
   AND ob.order_month BETWEEN '2017-01' AND '2018-08'
 GROUP BY ob.order_month
 ORDER BY ob.order_month;
