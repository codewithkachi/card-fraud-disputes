"""Export a slim, typed star schema for Power BI from the project warehouse.

Run from the project root:  python powerbi/export_powerbi_data.py

Writes powerbi/data/*.parquet (two big facts) and *.csv (everything else), plus
expected_values.csv: the headline numbers computed straight from the warehouse, so
the DAX measures in Power BI can be checked against an independent calculation.
"""
from pathlib import Path
import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "powerbi" / "data"
OUT.mkdir(parents=True, exist_ok=True)
con = duckdb.connect(str(ROOT / "data" / "processed" / "fraud.duckdb"), read_only=True)


def pq(name: str, sql: str) -> None:
    con.execute(f"COPY ({sql}) TO '{(OUT / (name + '.parquet')).as_posix()}' (FORMAT PARQUET)")
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{(OUT / (name + '.parquet')).as_posix()}')").fetchone()[0]
    print(f"{name}.parquet  {n:,} rows")


def csv(name: str, sql: str) -> pd.DataFrame:
    df = con.execute(sql).df()
    df.to_csv(OUT / f"{name}.csv", index=False)
    print(f"{name}.csv  {len(df):,} rows")
    return df


BAND = """CASE WHEN amount < 10 THEN 'a. under 10' WHEN amount < 50 THEN 'b. 10-50'
  WHEN amount < 100 THEN 'c. 50-100' WHEN amount < 250 THEN 'd. 100-250'
  WHEN amount < 500 THEN 'e. 250-500' WHEN amount < 1000 THEN 'f. 500-1k' ELSE 'g. 1k+' END"""

# ---- facts -----------------------------------------------------------------
pq("fct_transactions", f"""
    SELECT transaction_id, CAST(transaction_datetime AS DATE) AS txn_date,
           CAST(transaction_datetime AS TIMESTAMP) AS txn_datetime, txn_hour,
           customer_id, merchant_id, channel, country, amount, {BAND} AS amount_band,
           is_fraud, COALESCE(fraud_type, 'none') AS fraud_type,
           COALESCE(loss_amount, 0) AS loss_amount, COALESCE(recovered_amount, 0) AS recovered_amount,
           is_declined, authorisation_outcome, alerts_fired, any_block_rule, any_review_rule
    FROM fct_outcomes""")

pq("fct_alerts", """
    SELECT a.alert_id, a.transaction_id, a.rule_id, a.action_taken, a.is_fraud, a.amount,
           CAST(a.rule_triggered_datetime AS TIMESTAMP) AS alert_datetime,
           COUNT(DISTINCT a.rule_id) OVER (PARTITION BY a.transaction_id) AS rules_on_txn
    FROM rule_alerts a""")

csv("fct_disputes", """
    SELECT dispute_id, transaction_id, customer_id, CAST(raised_date AS DATE) AS raised_date,
           CASE WHEN is_first_party_misuse = 1 THEN 'First-party misuse' ELSE 'Third-party fraud' END AS dispute_class,
           dispute_reason_code, amount, loss_amount, recovered_amount, handling_cost, scheme_fee,
           write_off_amount, investigation_hours, outcome, dispute_stage
    FROM disputes""")

# ---- dimensions ---------------------------------------------------------------
dates = con.execute("SELECT MIN(CAST(transaction_datetime AS DATE)), MAX(CAST(transaction_datetime AS DATE)) FROM fct_outcomes").fetchone()
d = pd.DataFrame({"date": pd.date_range(dates[0], dates[1], freq="D")})
d["year"] = d.date.dt.year
d["month_start"] = d.date.dt.to_period("M").dt.to_timestamp()
d["month_label"] = d.date.dt.strftime("%b %Y")
d["month_sort"] = d.year * 100 + d.date.dt.month
d["day_of_week"] = d.date.dt.strftime("%a")
d["dow_sort"] = d.date.dt.dayofweek + 1
d.to_csv(OUT / "dim_date.csv", index=False)
print(f"dim_date.csv  {len(d):,} rows")

csv("dim_customer", "SELECT customer_id, card_id, segment, region, card_product, age, tenure_months, average_monthly_spend FROM customers_raw")
csv("dim_merchant", "SELECT merchant_id, merchant_name, mcc, mcc_description, risk_tier, avg_ticket, country FROM merchants_raw")
csv("dim_rule", "SELECT rule_id, rule_name, rule_logic_description, typology_covered, default_action, CAST(threshold_value AS DOUBLE) AS threshold_value, threshold_unit, owning_team FROM rules")

# ---- one-row assumptions, plus the two constants the analysis derives from data
a = pd.read_csv(ROOT / "outputs" / "tables" / "assumptions.csv")
rec = con.execute("SELECT SUM(recovered_amount) / SUM(loss_amount) FROM fraud_outcomes_raw WHERE is_fraud = 1").fetchone()[0]
case = con.execute("SELECT AVG(handling_cost + scheme_fee) FROM disputes WHERE is_first_party_misuse = 0").fetchone()[0]
a["recovery_rate_on_realised"] = rec
a["case_op_cost_third_party"] = case
a.to_csv(OUT / "Assumptions.csv", index=False)
print("Assumptions.csv  1 row")

# ---- small reference tables that cannot be derived row by row in DAX
csv("time_to_detection", "SELECT card_id, minutes_to_detection, loss_before_detection, detection_band, fraud_txns_on_card FROM time_to_detection")
sc = pd.read_csv(ROOT / "outputs" / "tables" / "recommended_config.csv")
sc.insert(0, "scenario_sort", range(1, len(sc) + 1))
sc.to_csv(OUT / "scenario_results.csv", index=False)
print("scenario_results.csv  ", len(sc), "rows")
# ---- expected values: the same measures, computed independently in SQL
cost_per_review = float(a.analyst_cost_per_hour.iloc[0] * a.review_minutes_per_alert.iloc[0] / 60)
q = con.execute("""
SELECT COUNT(*) AS txns, SUM(amount) AS spend,
  SUM(amount) FILTER (is_fraud=1) AS fraud_value,
  SUM(amount) FILTER (is_fraud=1 AND is_declined=1) AS prevented,
  SUM(amount) FILTER (is_fraud=1 AND is_declined=0) AS realised,
  COUNT(*) FILTER (is_fraud=1 AND is_declined=0) AS missed_n,
  COUNT(*) FILTER (is_fraud=0 AND is_declined=1) AS false_declines,
  SUM(amount) FILTER (is_fraud=0 AND is_declined=1) AS fd_value,
  COUNT(*) FILTER (is_fraud=1 AND is_declined=1) AS fraud_caught,
  COUNT(*) FILTER (any_review_rule=1 AND any_block_rule=0) AS review_alerts
FROM fct_outcomes""").df().iloc[0]
r = a.iloc[0]
rel = r.relationship_cost_per_false_decline
exp = {
    "Transactions": q.txns, "Total Spend": q.spend, "Fraud Value": q.fraud_value,
    "Fraud Rate (bps)": q.fraud_value / q.spend * 10000,
    "Prevented Fraud Value": q.prevented, "Realised Fraud Value": q.realised,
    "Prevention Rate": q.prevented / q.fraud_value,
    "False Declines": q.false_declines, "Fraud Caught": q.fraud_caught,
    "False Positives per Catch": q.false_declines / q.fraud_caught,
    "Fraud Write Off": q.realised * (1 - rec),
    "Chargeback Op Cost": q.missed_n * r.chargeback_probability * case,
    "FD Lost Margin": q.fd_value * (1 - r.decline_retry_recovery) * r.issuer_margin_rate,
    "FD Relationship Cost": q.false_declines * rel,
    "Review Op Cost": q.review_alerts * cost_per_review,
}
exp["Total Cost of Fraud"] = (exp["Fraud Write Off"] + exp["Chargeback Op Cost"] + exp["FD Lost Margin"]
                              + exp["FD Relationship Cost"] + exp["Review Op Cost"])
exp["Reported Share of True Cost"] = exp["Fraud Write Off"] / exp["Total Cost of Fraud"]
pd.Series(exp, name="expected").rename_axis("measure").to_frame().to_csv(OUT / "expected_values.csv")
print("\nExpected values (check these against the Power BI cards):")
for k, v in exp.items():
    print(f"  {k:<30}{v:>16,.4f}" if abs(v) < 100 else f"  {k:<30}{v:>16,.0f}")
