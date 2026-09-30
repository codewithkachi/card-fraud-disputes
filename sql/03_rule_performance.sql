-- ===========================================================================
-- Project 1 / 03_rule_performance.sql
-- A confusion matrix per RULE, not per model.
--
-- THE IDEA
-- Precision, recall and the confusion matrix are usually taught as things you
-- do to a classifier. A rules engine is just a classifier with hand-written
-- coefficients, so the same arithmetic applies - and applying it per rule is
-- what turns "we have eight rules" into "two of these rules are carrying the
-- other six".
--
-- THE METRIC THAT ACTUALLY DRIVES THE DECISION
-- Not precision. UNIQUE CATCHES: the fraud that ONLY this rule alerted on. A
-- rule can have respectable precision and still be worth nothing, because every
-- fraud it finds is also found by a rule you are keeping anyway. Switching that
-- rule off costs nothing and saves its whole false-positive bill. That column is
-- the one the recommendation table is built on.
-- ===========================================================================

CREATE OR REPLACE TABLE rule_performance AS
WITH
-- How many DISTINCT rules alerted on each fraudulent transaction. Needed to
-- work out which fraud would be missed entirely if a rule were removed.
fraud_rule_counts AS (
    SELECT a.transaction_id, COUNT(DISTINCT a.rule_id) AS n_rules
    FROM rule_alerts a
    WHERE a.is_fraud = 1
    GROUP BY a.transaction_id
),
totals AS (
    SELECT
        SUM(is_fraud)                                AS total_fraud_txns,
        SUM(CASE WHEN is_fraud = 1 THEN amount END)  AS total_fraud_value,
        COUNT(*)                                     AS total_txns
    FROM fct_outcomes
),
per_rule AS (
    SELECT
        a.rule_id,
        COUNT(*)                                            AS alert_volume,
        SUM(a.is_fraud)                                     AS true_positives,
        SUM(1 - a.is_fraud)                                 AS false_positives,
        SUM(CASE WHEN a.is_fraud = 1 THEN a.amount END)     AS fraud_value_alerted,
        SUM(CASE WHEN a.is_fraud = 0 THEN a.amount END)     AS genuine_value_alerted,
        -- unique catches: fraud where this was the only rule that fired
        SUM(CASE WHEN a.is_fraud = 1 AND frc.n_rules = 1 THEN 1 ELSE 0 END)
                                                            AS unique_fraud_catches,
        SUM(CASE WHEN a.is_fraud = 1 AND frc.n_rules = 1 THEN a.amount END)
                                                            AS unique_fraud_value
    FROM rule_alerts a
    LEFT JOIN fraud_rule_counts frc ON frc.transaction_id = a.transaction_id
    GROUP BY a.rule_id
)
SELECT
    r.rule_id,
    r.rule_name,
    r.typology_covered,
    r.default_action,
    r.threshold_value,
    r.threshold_unit,
    p.alert_volume,
    ROUND(p.alert_volume / 12.0, 0)                          AS alerts_per_month,
    p.true_positives,
    p.false_positives,
    -- Precision: of everything this rule stopped, how much was actually fraud.
    ROUND(100.0 * p.true_positives / p.alert_volume, 3)      AS precision_pct,
    -- Recall: of all fraud in the book, how much did this rule see.
    ROUND(100.0 * p.true_positives / t.total_fraud_txns, 2)  AS recall_pct,
    -- The number a fraud manager feels: genuine customers stopped per fraud caught.
    ROUND(1.0 * p.false_positives / NULLIF(p.true_positives, 0), 1)
                                                             AS false_positives_per_catch,
    COALESCE(p.unique_fraud_catches, 0)                      AS unique_fraud_catches,
    ROUND(COALESCE(p.unique_fraud_value, 0), 0)              AS unique_fraud_value,
    ROUND(COALESCE(p.fraud_value_alerted, 0), 0)             AS fraud_value_alerted,
    ROUND(COALESCE(p.genuine_value_alerted, 0), 0)           AS genuine_value_alerted,
    ROUND(100.0 * COALESCE(p.fraud_value_alerted,0) / t.total_fraud_value, 2)
                                                             AS value_recall_pct
FROM per_rule p
JOIN rules r ON r.rule_id = p.rule_id
CROSS JOIN totals t
ORDER BY p.alert_volume DESC;


-- ---------------------------------------------------------------------------
-- Alert duplication: how often the same transaction lights up several rules.
-- Each duplicate is a separate row in the queue and, in most banks, a separate
-- piece of investigator work.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE alert_overlap AS
SELECT
    distinct_rules_fired            AS rules_fired_on_txn,
    COUNT(*)                        AS transactions,
    SUM(is_fraud)                   AS fraudulent,
    ROUND(100.0 * SUM(is_fraud) / COUNT(*), 3) AS fraud_rate_pct
FROM fct_outcomes
WHERE alerts_fired > 0
GROUP BY distinct_rules_fired
ORDER BY distinct_rules_fired;
