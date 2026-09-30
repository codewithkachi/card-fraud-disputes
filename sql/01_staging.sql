-- ===========================================================================
-- Project 1 / 01_staging.sql
-- Build the analytical base table.
--
-- WHAT THIS FILE IS DOING
-- Every fraud detection rule in the industry is one of two things: a threshold
-- on a single transaction, or a threshold on a transaction's RELATIONSHIP to
-- what came before it on the same card. The second kind is the interesting kind,
-- and it is built entirely with window functions. That is why this file exists
-- as SQL rather than pandas - it is the clearest possible demonstration of the
-- technique, and window functions are on nearly every analyst SQL test.
--
-- The window frame that matters:
--     OVER (PARTITION BY card_id ORDER BY transaction_datetime
--           RANGE BETWEEN INTERVAL 60 MINUTES PRECEDING AND CURRENT ROW)
-- RANGE (not ROWS) means "everything within 60 minutes of this row's timestamp",
-- which is a genuine time window rather than a fixed number of rows. Using ROWS
-- here would be a silent bug: 5 transactions in 60 minutes and 5 transactions in
-- 5 days would look identical.
-- ===========================================================================

CREATE OR REPLACE TABLE stg_transactions AS
SELECT
    t.transaction_id,
    t.card_id,
    t.customer_id,
    t.transaction_datetime,
    CAST(t.amount AS DOUBLE)                       AS amount,
    t.merchant_id,
    t.merchant_category_code,
    t.channel,
    t.country,
    m.mcc_description,
    m.risk_tier                                    AS merchant_risk_tier,
    c.segment,
    c.card_product,
    c.region,
    c.tenure_months,
    c.average_monthly_spend,
    CAST(c.age AS INTEGER)                         AS customer_age,
    -- Outcome label. LEFT JOIN then COALESCE, never an inner join: a transaction
    -- with no row in fraud_outcomes is genuine-and-unreviewed, not missing.
    COALESCE(f.is_fraud, 0)                        AS is_fraud,
    f.fraud_type,
    f.confirmed_date,
    COALESCE(f.loss_amount, 0)                     AS loss_amount,
    COALESCE(f.recovered_amount, 0)                AS recovered_amount
FROM transactions_raw       t
LEFT JOIN merchants_raw     m ON m.merchant_id = t.merchant_id
LEFT JOIN customers_raw     c ON c.customer_id = t.customer_id
LEFT JOIN fraud_outcomes_raw f ON f.transaction_id = t.transaction_id;


-- ---------------------------------------------------------------------------
-- Behavioural features. One pass, seven window functions, no self-joins.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE fct_transactions AS
SELECT
    s.*,
    EXTRACT(hour  FROM transaction_datetime)       AS txn_hour,
    EXTRACT(dow   FROM transaction_datetime)       AS txn_dow,
    DATE_TRUNC('month', transaction_datetime)      AS txn_month,

    -- Velocity: how many transactions on this card in the preceding hour
    -- (inclusive of this one).
    COUNT(*) OVER (
        PARTITION BY card_id ORDER BY transaction_datetime
        RANGE BETWEEN INTERVAL 60 MINUTES PRECEDING AND CURRENT ROW
    )                                              AS txn_count_60min,

    COUNT(*) OVER (
        PARTITION BY card_id ORDER BY transaction_datetime
        RANGE BETWEEN INTERVAL 120 MINUTES PRECEDING AND CURRENT ROW
    )                                              AS txn_count_120min,

    SUM(amount) OVER (
        PARTITION BY card_id ORDER BY transaction_datetime
        RANGE BETWEEN INTERVAL 24 HOURS PRECEDING AND CURRENT ROW
    )                                              AS amount_sum_24h,

    -- Geography: the previous country on this card, and how long ago.
    LAG(country) OVER (
        PARTITION BY card_id ORDER BY transaction_datetime
    )                                              AS prev_country,

    DATE_DIFF('minute',
        LAG(transaction_datetime) OVER (
            PARTITION BY card_id ORDER BY transaction_datetime),
        transaction_datetime)                      AS minutes_since_prev_txn,

    -- Dormancy: days since this card was last used at all.
    DATE_DIFF('day',
        LAG(transaction_datetime) OVER (
            PARTITION BY card_id ORDER BY transaction_datetime),
        transaction_datetime)                      AS days_since_prev_txn,

    -- First-time-at-this-merchant. Counting rows within the
    -- (customer, merchant) partition and testing for 1 is cheaper and clearer
    -- than a NOT EXISTS correlated subquery over 2.5m rows.
    CASE WHEN ROW_NUMBER() OVER (
            PARTITION BY customer_id, merchant_id
            ORDER BY transaction_datetime) = 1
         THEN 1 ELSE 0 END                         AS is_first_txn_at_merchant,

    -- Amount relative to what this customer normally spends. Expressed as a
    -- multiple so it is comparable across a student and a Premier customer.
    amount / NULLIF(average_monthly_spend / 30.0, 0) AS amount_vs_daily_norm
FROM stg_transactions s;


-- Fast lookups for the rule layer and the per-card time-to-detection analysis.
CREATE INDEX IF NOT EXISTS idx_fct_card ON fct_transactions (card_id, transaction_datetime);
CREATE INDEX IF NOT EXISTS idx_fct_txn  ON fct_transactions (transaction_id);
