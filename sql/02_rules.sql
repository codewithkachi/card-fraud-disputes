-- ===========================================================================
-- Project 1 / 02_rules.sql
-- The detection rule set, and the alert layer it produces.
--
-- WHERE THESE RULES COME FROM
-- Each rule below maps to a published card-fraud typology. They are not
-- invented, and they are deliberately NOT derived from how the synthetic fraud
-- was generated - see the note at the top of src/01_generate_data.py. Sources
-- for the typologies: UK Finance Annual Fraud Report (card-not-present,
-- counterfeit, lost-and-stolen categories), EMVCo / scheme guidance on
-- card-testing and enumeration attacks, and the standard velocity / geographic-
-- impossibility controls that every issuer runs.
--
-- ACTION DESIGN
-- Real engines do not only block. They split into:
--    block  - decline at authorisation. Cheap to run, expensive when wrong,
--             because a genuine customer is refused at a checkout.
--    review - hold for an analyst. Costs investigator time, catches less, but
--             the false-positive cost is much lower because most reviewed
--             transactions are released.
-- Mixing the two is what makes the later cost model non-trivial. If every rule
-- blocked, the answer would just be "raise every threshold".
-- ===========================================================================

CREATE OR REPLACE TABLE rules AS
SELECT * FROM (VALUES
  ('R01','HIGH_VALUE_ECOM',
   'E-commerce transaction above GBP 750',
   'card_not_present', 'block',  750.0, 'GBP', 'Fraud Strategy'),

  ('R02','VELOCITY_5_IN_60MIN',
   '5 or more transactions on the same card within a rolling 60 minutes',
   'card_testing',     'block',    5.0, 'transactions', 'Fraud Strategy'),

  -- NOTE the card-present restriction. A card-not-present transaction has no
  -- physical location: the "country" on an e-commerce authorisation is the
  -- merchant's, not the cardholder's. Applying a geographic-impossibility rule
  -- to e-commerce is a common real-world misconfiguration and it generates
  -- enormous alert volume at almost no precision.
  ('R03','GEO_IMPOSSIBLE_3H_CP',
   'Card-present transaction in a different country within 3 hours of the previous one',
   'counterfeit',      'block',    3.0, 'hours', 'Fraud Strategy'),

  ('R04','DORMANT_REACTIVATION',
   'Card unused for 90+ days, then a transaction above GBP 200',
   'account_takeover', 'review',  90.0, 'days', 'Financial Crime Ops'),

  ('R05','HIGH_RISK_MCC_VALUE',
   'High-risk merchant category and amount above GBP 300',
   'counterfeit',      'review', 300.0, 'GBP', 'Fraud Strategy'),

  ('R06','CNP_NEW_MERCHANT',
   'E-commerce, first ever transaction at this merchant for this customer, above GBP 150',
   'card_not_present', 'review', 150.0, 'GBP', 'Fraud Strategy'),

  ('R07','ROUND_AMOUNT_CROSS_BORDER',
   'Non-UK merchant, amount is an exact multiple of GBP 50 and at least GBP 250',
   'counterfeit',      'block',  250.0, 'GBP', 'Financial Crime Ops'),

  ('R08','NOCTURNAL_BURST',
   '3 or more transactions between 00:00 and 05:00 within a rolling 2 hours',
   'lost_stolen',      'review',   3.0, 'transactions', 'Fraud Strategy')
) AS t(rule_id, rule_name, rule_logic_description, typology_covered,
       default_action, threshold_value, threshold_unit, owning_team);


-- ---------------------------------------------------------------------------
-- Apply every rule. One SELECT per rule, UNION ALL'd - deliberately verbose.
-- A fraud strategy manager needs to read a rule definition and recognise their
-- own rule in it. A clever generic loop would be shorter and unreadable.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE rule_alerts AS
WITH fired AS (
    SELECT transaction_id, 'R01' AS rule_id FROM fct_transactions
     WHERE channel = 'ecommerce' AND amount > 750

    UNION ALL
    SELECT transaction_id, 'R02' FROM fct_transactions
     WHERE txn_count_60min >= 5

    UNION ALL
    SELECT transaction_id, 'R03' FROM fct_transactions
     WHERE prev_country IS NOT NULL
       AND country <> prev_country
       AND minutes_since_prev_txn <= 180
       AND channel IN ('card_present_chip', 'contactless', 'manual_entry')

    UNION ALL
    SELECT transaction_id, 'R04' FROM fct_transactions
     WHERE days_since_prev_txn >= 90 AND amount > 200

    UNION ALL
    SELECT transaction_id, 'R05' FROM fct_transactions
     WHERE merchant_risk_tier = 'high' AND amount > 300

    UNION ALL
    SELECT transaction_id, 'R06' FROM fct_transactions
     WHERE channel = 'ecommerce'
       AND is_first_txn_at_merchant = 1
       AND amount > 150

    UNION ALL
    SELECT transaction_id, 'R07' FROM fct_transactions
     WHERE country <> 'GB'
       AND amount >= 250
       AND amount = ROUND(amount / 50.0) * 50.0

    UNION ALL
    SELECT transaction_id, 'R08' FROM fct_transactions
     WHERE txn_hour BETWEEN 0 AND 5 AND txn_count_120min >= 3
)
SELECT
    ROW_NUMBER() OVER (ORDER BY f.transaction_datetime, fired.rule_id) AS alert_id,
    fired.transaction_id,
    fired.rule_id,
    f.transaction_datetime          AS rule_triggered_datetime,
    r.default_action                AS action_taken,
    f.is_fraud,
    f.amount
FROM fired
JOIN fct_transactions f ON f.transaction_id = fired.transaction_id
JOIN rules            r ON r.rule_id        = fired.rule_id;


-- ---------------------------------------------------------------------------
-- Transaction-level outcome.
--
-- A transaction is DECLINED if any blocking rule fired on it.
-- A transaction is REVIEWED if only review rules fired; the analyst decision is
-- applied in Python (src/02_build_warehouse.py) because it needs a stochastic
-- error rate, and that belongs in code where the assumption is visible rather
-- than buried in a CASE expression.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE txn_rule_outcome AS
SELECT
    f.transaction_id,
    COUNT(a.alert_id)                                        AS alerts_fired,
    COUNT(DISTINCT a.rule_id)                                AS distinct_rules_fired,
    MAX(CASE WHEN a.action_taken = 'block'  THEN 1 ELSE 0 END) AS any_block_rule,
    MAX(CASE WHEN a.action_taken = 'review' THEN 1 ELSE 0 END) AS any_review_rule,
    STRING_AGG(DISTINCT a.rule_id, ',' ORDER BY a.rule_id)   AS rules_fired
FROM fct_transactions f
LEFT JOIN rule_alerts a ON a.transaction_id = f.transaction_id
GROUP BY f.transaction_id;
