-- ===========================================================================
-- Project 1 / 04_patterns.sql
-- Where the fraud is, who is being inconvenienced, and how long detection takes.
--
-- One rule runs through all of it: NEVER a raw count. Fraud counts follow spend
-- volume, so the biggest merchant category will always show the most fraud and
-- tell you nothing. Every measure here is a RATE - fraud value as basis points
-- of the spend in that same cell - so a small category with a bad problem is
-- visible next to a huge category with a normal one.
-- ===========================================================================

-- --- fraud rate by merchant category ---------------------------------------
CREATE OR REPLACE TABLE fraud_by_mcc AS
SELECT
    merchant_category_code                                   AS mcc,
    ANY_VALUE(mcc_description)                               AS mcc_description,
    ANY_VALUE(merchant_risk_tier)                            AS risk_tier,
    COUNT(*)                                                 AS txns,
    ROUND(SUM(amount), 0)                                    AS total_value,
    SUM(is_fraud)                                            AS fraud_txns,
    ROUND(SUM(CASE WHEN is_fraud = 1 THEN amount END), 0)    AS fraud_value,
    ROUND(10000.0 * SUM(CASE WHEN is_fraud = 1 THEN amount ELSE 0 END)
          / NULLIF(SUM(amount), 0), 1)                       AS fraud_bps,
    ROUND(100.0 * SUM(CASE WHEN is_fraud = 0 AND is_declined = 1 THEN 1 ELSE 0 END)
          / NULLIF(SUM(CASE WHEN is_fraud = 0 THEN 1 ELSE 0 END), 0), 2)
                                                             AS genuine_decline_rate_pct
FROM fct_outcomes
GROUP BY merchant_category_code
HAVING COUNT(*) > 500
ORDER BY fraud_bps DESC;


-- --- hour x channel: is fraud nocturnal, and is genuine spend? --------------
CREATE OR REPLACE TABLE fraud_by_hour_channel AS
SELECT
    txn_hour,
    channel,
    COUNT(*)                                                 AS txns,
    SUM(is_fraud)                                            AS fraud_txns,
    ROUND(10000.0 * SUM(CASE WHEN is_fraud = 1 THEN amount ELSE 0 END)
          / NULLIF(SUM(amount), 0), 1)                       AS fraud_bps
FROM fct_outcomes
GROUP BY txn_hour, channel
ORDER BY txn_hour, channel;


-- --- amount bands ----------------------------------------------------------
CREATE OR REPLACE TABLE fraud_by_amount_band AS
SELECT
    CASE
      WHEN amount <   10 THEN 'a. under 10'
      WHEN amount <   50 THEN 'b. 10-50'
      WHEN amount <  100 THEN 'c. 50-100'
      WHEN amount <  250 THEN 'd. 100-250'
      WHEN amount <  500 THEN 'e. 250-500'
      WHEN amount < 1000 THEN 'f. 500-1k'
      ELSE                    'g. 1k+'
    END                                                      AS amount_band,
    COUNT(*)                                                 AS txns,
    SUM(is_fraud)                                            AS fraud_txns,
    ROUND(100.0 * SUM(is_fraud) / COUNT(*), 3)               AS fraud_rate_pct,
    ROUND(10000.0 * SUM(CASE WHEN is_fraud = 1 THEN amount ELSE 0 END)
          / NULLIF(SUM(amount), 0), 1)                       AS fraud_bps,
    ROUND(100.0 * SUM(CASE WHEN is_fraud = 0 AND is_declined = 1 THEN 1 ELSE 0 END)
          / NULLIF(SUM(CASE WHEN is_fraud = 0 THEN 1 ELSE 0 END), 0), 2)
                                                             AS genuine_decline_rate_pct
FROM fct_outcomes
GROUP BY 1 ORDER BY 1;


-- --- who bears the false positives? ----------------------------------------
-- The equity question. If one segment is being declined at three times the rate
-- of another for the same underlying fraud risk, that is a finding on its own.
CREATE OR REPLACE TABLE false_positives_by_segment AS
SELECT
    segment,
    channel,
    COUNT(*) FILTER (WHERE is_fraud = 0)                     AS genuine_txns,
    COUNT(*) FILTER (WHERE is_fraud = 0 AND is_declined = 1) AS genuine_declined,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_fraud = 0 AND is_declined = 1)
          / NULLIF(COUNT(*) FILTER (WHERE is_fraud = 0), 0), 2)
                                                             AS false_decline_rate_pct,
    ROUND(SUM(amount) FILTER (WHERE is_fraud = 0 AND is_declined = 1), 0)
                                                             AS genuine_value_declined,
    COUNT(*) FILTER (WHERE is_fraud = 1 AND is_declined = 1) AS fraud_caught,
    ROUND(1.0 * COUNT(*) FILTER (WHERE is_fraud = 0 AND is_declined = 1)
          / NULLIF(COUNT(*) FILTER (WHERE is_fraud = 1 AND is_declined = 1), 0), 1)
                                                             AS fp_per_catch
FROM fct_outcomes
GROUP BY segment, channel
ORDER BY segment, channel;


-- --- time to detection, and the loss that accumulates inside that window ----
-- Per compromised CARD, not per transaction: the operational question is "how
-- long does a compromised card keep working", and the answer is the gap between
-- its first fraudulent transaction and the first one the engine stopped.
CREATE OR REPLACE TABLE time_to_detection AS
WITH card_fraud AS (
    SELECT
        card_id,
        MIN(transaction_datetime)                                    AS first_fraud_at,
        MIN(CASE WHEN is_declined = 1 THEN transaction_datetime END) AS first_stopped_at,
        COUNT(*)                                                     AS fraud_txns_on_card,
        SUM(amount)                                                  AS fraud_value_on_card
    FROM fct_outcomes
    WHERE is_fraud = 1
    GROUP BY card_id
),
losses AS (
    SELECT
        cf.card_id,
        cf.first_fraud_at,
        cf.first_stopped_at,
        cf.fraud_txns_on_card,
        cf.fraud_value_on_card,
        DATE_DIFF('minute', cf.first_fraud_at, cf.first_stopped_at) AS minutes_to_detection,
        -- Value that got through BEFORE the first stop. This is the number that
        -- converts "detection is slow" into money.
        SUM(CASE WHEN t.is_declined = 0
                  AND (cf.first_stopped_at IS NULL
                       OR t.transaction_datetime < cf.first_stopped_at)
                 THEN t.amount ELSE 0 END)                          AS loss_before_detection
    FROM card_fraud cf
    JOIN fct_outcomes t
      ON t.card_id = cf.card_id AND t.is_fraud = 1
    GROUP BY 1,2,3,4,5,6
)
SELECT
    *,
    CASE
      WHEN first_stopped_at IS NULL         THEN 'never detected'
      WHEN minutes_to_detection <=  0       THEN 'a. stopped on first txn'
      WHEN minutes_to_detection <= 60       THEN 'b. within 1 hour'
      WHEN minutes_to_detection <= 360      THEN 'c. 1-6 hours'
      WHEN minutes_to_detection <= 1440     THEN 'd. 6-24 hours'
      ELSE                                       'e. over 24 hours'
    END                                                              AS detection_band
FROM losses;


-- --- disputes ---------------------------------------------------------------
CREATE OR REPLACE TABLE dispute_summary AS
SELECT
    CASE WHEN is_first_party_misuse = 1 THEN 'First-party misuse'
         ELSE 'Third-party fraud' END                        AS dispute_class,
    COUNT(*)                                                 AS cases,
    ROUND(AVG(investigation_hours), 2)                       AS avg_investigation_hours,
    ROUND(SUM(investigation_hours), 0)                       AS total_investigation_hours,
    ROUND(AVG(handling_cost), 2)                             AS avg_handling_cost,
    ROUND(SUM(handling_cost + scheme_fee), 0)                AS total_operational_cost,
    ROUND(SUM(write_off_amount), 0)                          AS total_write_off,
    ROUND(SUM(handling_cost + scheme_fee + write_off_amount), 0) AS total_cost
FROM disputes
GROUP BY 1 ORDER BY 1;
