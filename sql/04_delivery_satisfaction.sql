-- =============================================================================
-- 04_delivery_satisfaction.sql — delivery reliability and its association with
-- review scores. Requires 00_analytical_tables.sql.
--
-- SCOPE: delivered orders that have BOTH a customer-delivery date and an
-- estimated-delivery date. 8 delivered orders lack the former and are excluded
-- rather than counted as late (order_base sets their measures to NULL).
--
-- CAUSAL CAUTION: everything here is an ASSOCIATION. Late deliveries and low
-- reviews co-occur, but the dataset has no experiment, no randomisation and no
-- controls for product, seller, price or customer mix. Read as "late orders are
-- reviewed worse", never "lateness causes N stars of damage".
-- =============================================================================

-- Q5: headline delivery reliability ------------------------------------------
SELECT 'delivery_headline' AS block,
       COUNT(*)                                        AS delivered_with_dates,
       SUM(is_on_time)                                 AS on_time,
       COUNT(*) - SUM(is_on_time)                      AS late,
       ROUND(100.0 * AVG(is_on_time), 2)               AS on_time_pct,
       ROUND(100.0 - 100.0 * AVG(is_on_time), 2)       AS late_pct,
       ROUND(AVG(days_to_deliver), 2)                  AS avg_days_to_deliver,
       ROUND(AVG(-delivery_vs_estimate_days), 2)       AS avg_days_early_vs_estimate
  FROM order_base
 WHERE is_eligible = 1 AND is_on_time IS NOT NULL;

-- Q5: how late is late? ------------------------------------------------------
SELECT 'lateness_buckets' AS block,
       CASE
           WHEN delivery_vs_estimate_days <= -15 THEN '15+ days early'
           WHEN delivery_vs_estimate_days <=  -8 THEN '8-14 days early'
           WHEN delivery_vs_estimate_days <=  -1 THEN '1-7 days early'
           WHEN delivery_vs_estimate_days  =   0 THEN 'on the estimated day'
           WHEN delivery_vs_estimate_days <=   7 THEN '1-7 days late'
           WHEN delivery_vs_estimate_days <=  14 THEN '8-14 days late'
           WHEN delivery_vs_estimate_days <=  30 THEN '15-30 days late'
           ELSE '30+ days late'
       END AS bucket,
       MIN(delivery_vs_estimate_days) AS bucket_min_days,
       COUNT(*) AS orders,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_orders,
       ROUND(AVG(r.review_score), 3) AS avg_review
  FROM order_base AS ob
  LEFT JOIN order_review AS r ON ob.order_id = r.order_id
 WHERE ob.is_eligible = 1 AND ob.delivery_vs_estimate_days IS NOT NULL
 GROUP BY bucket
 ORDER BY bucket_min_days;

-- Q6: on-time vs late — review distribution ----------------------------------
SELECT 'ontime_vs_late_reviews' AS block,
       CASE WHEN ob.is_on_time = 1 THEN 'on time' ELSE 'late' END AS delivery_outcome,
       COUNT(*)                                            AS orders,
       ROUND(AVG(r.review_score), 3)                       AS avg_review,
       ROUND(100.0 * AVG(CASE WHEN r.review_score <= 2 THEN 1 ELSE 0 END), 2) AS pct_1_or_2_star,
       ROUND(100.0 * AVG(CASE WHEN r.review_score >= 4 THEN 1 ELSE 0 END), 2) AS pct_4_or_5_star
  FROM order_base AS ob
  JOIN order_review AS r ON ob.order_id = r.order_id
 WHERE ob.is_eligible = 1 AND ob.is_on_time IS NOT NULL
 GROUP BY delivery_outcome
 ORDER BY delivery_outcome;

-- Q6: full review-score crosstab ---------------------------------------------
SELECT 'review_score_crosstab' AS block,
       CAST(r.review_score AS INTEGER) AS review_score,
       SUM(CASE WHEN ob.is_on_time = 1 THEN 1 ELSE 0 END) AS on_time_orders,
       SUM(CASE WHEN ob.is_on_time = 0 THEN 1 ELSE 0 END) AS late_orders,
       ROUND(100.0 * SUM(CASE WHEN ob.is_on_time = 1 THEN 1 ELSE 0 END)
             / SUM(SUM(CASE WHEN ob.is_on_time = 1 THEN 1 ELSE 0 END)) OVER (), 2) AS pct_of_on_time,
       ROUND(100.0 * SUM(CASE WHEN ob.is_on_time = 0 THEN 1 ELSE 0 END)
             / SUM(SUM(CASE WHEN ob.is_on_time = 0 THEN 1 ELSE 0 END)) OVER (), 2) AS pct_of_late
  FROM order_base AS ob
  JOIN order_review AS r ON ob.order_id = r.order_id
 WHERE ob.is_eligible = 1 AND ob.is_on_time IS NOT NULL
   AND r.review_score = CAST(r.review_score AS INTEGER)   -- drop the 547 averaged ties
 GROUP BY review_score
 ORDER BY review_score;

-- Q5: monthly on-time rate — is reliability drifting? ------------------------
SELECT 'ontime_monthly' AS block,
       ob.order_month,
       COUNT(*) AS delivered_orders,
       ROUND(100.0 * AVG(ob.is_on_time), 2) AS on_time_pct,
       ROUND(AVG(ob.days_to_deliver), 1) AS avg_days_to_deliver,
       ROUND(AVG(r.review_score), 3) AS avg_review
  FROM order_base AS ob
  LEFT JOIN order_review AS r ON ob.order_id = r.order_id
 WHERE ob.is_eligible = 1 AND ob.is_on_time IS NOT NULL
   AND ob.order_month BETWEEN '2017-01' AND '2018-08'
 GROUP BY ob.order_month
 ORDER BY ob.order_month;

-- Q5/Q6: where are delays concentrated? --------------------------------------
-- Ordered by late_orders so the ranking reflects absolute customer impact,
-- not a high percentage on a tiny base. min 200 orders keeps rates stable.
SELECT 'delays_by_state' AS block,
       ob.customer_state,
       COUNT(*) AS delivered_orders,
       COUNT(*) - SUM(ob.is_on_time) AS late_orders,
       ROUND(100.0 - 100.0 * AVG(ob.is_on_time), 2) AS late_pct,
       ROUND(AVG(ob.days_to_deliver), 1) AS avg_days_to_deliver,
       ROUND(AVG(ob.freight), 2) AS avg_freight,
       ROUND(AVG(r.review_score), 3) AS avg_review
  FROM order_base AS ob
  LEFT JOIN order_review AS r ON ob.order_id = r.order_id
 WHERE ob.is_eligible = 1 AND ob.is_on_time IS NOT NULL
 GROUP BY ob.customer_state
HAVING COUNT(*) >= 200
 ORDER BY late_orders DESC;

-- Q5: delays by category -----------------------------------------------------
WITH order_cat AS (
    -- Assign each order a single category: the one holding the most item value.
    -- Prevents an order from being counted once per category it touches.
    SELECT order_id, category FROM (
        SELECT order_id, category,
               ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY SUM(price) DESC) AS rn
          FROM item_base WHERE is_eligible = 1 GROUP BY order_id, category
    ) WHERE rn = 1
)
SELECT 'delays_by_category' AS block,
       oc.category,
       COUNT(*) AS delivered_orders,
       ROUND(100.0 - 100.0 * AVG(ob.is_on_time), 2) AS late_pct,
       ROUND(AVG(ob.days_to_deliver), 1) AS avg_days_to_deliver,
       ROUND(AVG(r.review_score), 3) AS avg_review,
       ROUND(SUM(ob.gmv), 2) AS gmv
  FROM order_base AS ob
  JOIN order_cat AS oc ON ob.order_id = oc.order_id
  LEFT JOIN order_review AS r ON ob.order_id = r.order_id
 WHERE ob.is_eligible = 1 AND ob.is_on_time IS NOT NULL
 GROUP BY oc.category
HAVING COUNT(*) >= 500
 ORDER BY late_pct DESC;

-- Q6: does a bad delivery experience suppress repeat purchase? ---------------
-- Descriptive only: a customer's FIRST order outcome vs whether they ever
-- ordered again. Confounded by state, category and time-in-window.
WITH first_order AS (
    SELECT customer_unique_id, order_id, is_on_time, orders_total FROM (
        SELECT ob.customer_unique_id, ob.order_id, ob.is_on_time,
               COUNT(*) OVER (PARTITION BY ob.customer_unique_id) AS orders_total,
               ROW_NUMBER() OVER (PARTITION BY ob.customer_unique_id
                                  ORDER BY ob.order_purchase_timestamp) AS rn
          FROM order_base AS ob WHERE ob.is_eligible = 1
    ) WHERE rn = 1
)
SELECT 'first_order_outcome_vs_repeat' AS block,
       CASE WHEN is_on_time = 1 THEN 'first order on time' ELSE 'first order late' END AS first_experience,
       COUNT(*) AS customers,
       SUM(CASE WHEN orders_total > 1 THEN 1 ELSE 0 END) AS came_back,
       ROUND(100.0 * AVG(CASE WHEN orders_total > 1 THEN 1 ELSE 0 END), 2) AS repeat_rate_pct
  FROM first_order
 WHERE is_on_time IS NOT NULL
 GROUP BY first_experience
 ORDER BY first_experience;
