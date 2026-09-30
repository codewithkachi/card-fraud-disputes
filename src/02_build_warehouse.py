"""
Project 1 - Step 2: build the DuckDB warehouse, apply the rules, create disputes.

WHY DUCKDB
----------
2.5 million transactions with rolling-window velocity features. pandas can do it,
but the window-function syntax in SQL is the thing an analyst interview will ask
about, and DuckDB runs it in-process against Parquet with no server to install.
This is the right tool for "a laptop, a few million rows, and a lot of window
functions".

WHAT HAPPENS HERE THAT IS NOT SQL
---------------------------------
Two things, and both are here on purpose because they are ASSUMPTIONS, and
assumptions belong in visible code rather than buried in a CASE expression:

1. The analyst decision on 'review' alerts. Human reviewers are good but not
   perfect. I model them as catching 78% of the fraud they look at, and wrongly
   declining 6% of the genuine transactions they look at. Both numbers are
   stated, both are swept in the sensitivity analysis.

2. Disputes. A chargeback only exists where fraud got through, plus a stream of
   first-party misuse - the cardholder disputing a transaction they actually
   made. Published card-scheme commentary puts first-party ("friendly") fraud at
   a substantial minority of all disputes; I target 30%.

Run:  python src/02_build_warehouse.py
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
SQL = ROOT / "sql"
PROC.mkdir(parents=True, exist_ok=True)

DB = PROC / "fraud.duckdb"
RNG = np.random.default_rng(11)

# ---- stated assumptions (every one of these is swept later) ---------------
ANALYST_FRAUD_CATCH_RATE = 0.78     # of fraud sent to review, share declined
ANALYST_FALSE_DECLINE_RATE = 0.06   # of genuine sent to review, share declined
CHARGEBACK_RATE_ON_MISSED_FRAUD = 0.86
FIRST_PARTY_SHARE_OF_DISPUTES = 0.30


def run_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    print(f"  running {path.name}")
    con.execute(path.read_text(encoding="utf-8"))


def main() -> None:
    if DB.exists():
        DB.unlink()
    con = duckdb.connect(str(DB))

    print("Loading raw ...")
    con.execute(f"CREATE TABLE transactions_raw AS "
                f"SELECT * FROM read_parquet('{(RAW/'transactions.parquet').as_posix()}')")
    con.execute(f"CREATE TABLE fraud_outcomes_raw AS "
                f"SELECT * FROM read_parquet('{(RAW/'fraud_outcomes.parquet').as_posix()}')")
    con.execute(f"CREATE TABLE customers_raw AS "
                f"SELECT * FROM read_csv_auto('{(RAW/'customers.csv').as_posix()}')")
    con.execute(f"CREATE TABLE merchants_raw AS "
                f"SELECT * FROM read_csv_auto('{(RAW/'merchants.csv').as_posix()}')")

    print("Building model ...")
    run_sql_file(con, SQL / "01_staging.sql")
    run_sql_file(con, SQL / "02_rules.sql")

    n_alerts = con.execute("SELECT COUNT(*) FROM rule_alerts").fetchone()[0]
    print(f"  rule_alerts: {n_alerts:,}")

    # ---------------------------------------------------------------------
    # Analyst decisions on the review queue.
    # ---------------------------------------------------------------------
    print("Applying analyst decisions to the review queue ...")
    rev = con.execute("""
        SELECT o.transaction_id, f.is_fraud
        FROM txn_rule_outcome o
        JOIN fct_transactions f USING (transaction_id)
        WHERE o.any_block_rule = 0 AND o.any_review_rule = 1
    """).df()
    p = np.where(rev.is_fraud == 1, ANALYST_FRAUD_CATCH_RATE,
                 ANALYST_FALSE_DECLINE_RATE)
    rev["analyst_declined"] = (RNG.random(len(rev)) < p).astype(int)
    con.register("rev_decisions", rev[["transaction_id", "analyst_declined"]])

    con.execute("""
        CREATE OR REPLACE TABLE fct_outcomes AS
        SELECT
            f.*,
            o.alerts_fired,
            o.distinct_rules_fired,
            o.any_block_rule,
            o.any_review_rule,
            o.rules_fired,
            CASE
              WHEN o.any_block_rule = 1 THEN 'declined_by_rule'
              WHEN o.any_review_rule = 1 AND COALESCE(d.analyst_declined,0) = 1
                   THEN 'declined_by_analyst'
              WHEN o.any_review_rule = 1 THEN 'released_by_analyst'
              ELSE 'approved'
            END AS authorisation_outcome,
            CASE WHEN o.any_block_rule = 1
                   OR (o.any_review_rule = 1 AND COALESCE(d.analyst_declined,0) = 1)
                 THEN 1 ELSE 0 END AS is_declined
        FROM fct_transactions f
        JOIN txn_rule_outcome o USING (transaction_id)
        LEFT JOIN rev_decisions d USING (transaction_id)
    """)

    # ---- the headline calibration check ---------------------------------
    chk = con.execute("""
        SELECT
          SUM(amount)                                              AS total_value,
          SUM(CASE WHEN is_fraud=1 THEN amount END)                AS attempted_fraud_value,
          SUM(CASE WHEN is_fraud=1 AND is_declined=1 THEN amount END) AS prevented_fraud_value,
          SUM(CASE WHEN is_fraud=1 AND is_declined=0 THEN amount END) AS realised_fraud_value,
          SUM(CASE WHEN is_fraud=0 AND is_declined=1 THEN amount END) AS false_decline_value,
          COUNT(*)                                                 AS txns,
          SUM(is_declined)                                         AS declines
        FROM fct_outcomes
    """).df().iloc[0]
    prevention = chk.prevented_fraud_value / chk.attempted_fraud_value
    net_bps = chk.realised_fraud_value / chk.total_value * 10_000
    print(f"\n  CALIBRATION")
    print(f"    prevention rate       {prevention*100:5.1f}%  (target ~70%)")
    print(f"    net fraud loss        {net_bps:5.1f} bps (target ~7 bps)")
    print(f"    overall decline rate  {chk.declines/chk.txns*100:5.2f}%")

    # ---------------------------------------------------------------------
    # Disputes. Two populations, and separating them is a finding in itself.
    # ---------------------------------------------------------------------
    print("\nBuilding disputes ...")
    missed = con.execute("""
        SELECT transaction_id, amount, loss_amount, recovered_amount,
               transaction_datetime, customer_id
        FROM fct_outcomes WHERE is_fraud = 1 AND is_declined = 0
    """).df()
    third_party = missed[RNG.random(len(missed)) < CHARGEBACK_RATE_ON_MISSED_FRAUD].copy()
    third_party["dispute_reason_code"] = RNG.choice(
        ["10.4 Other Fraud - CNP", "10.1 EMV Liability Shift",
         "10.5 Visa Fraud Monitoring", "83 Fraud - CNP"],
        len(third_party), p=[0.46, 0.20, 0.10, 0.24])
    third_party["is_first_party_misuse"] = 0

    # First-party misuse sized so it lands at the target share of all disputes.
    n_first = int(len(third_party) * FIRST_PARTY_SHARE_OF_DISPUTES
                  / (1 - FIRST_PARTY_SHARE_OF_DISPUTES))
    # NOTE: DuckDB applies USING SAMPLE at the table scan, BEFORE the WHERE
    # clause. Sampling an already-filtered subquery is the only way to get
    # exactly n rows that satisfy the predicate. This bit me once; it is the
    # kind of thing worth writing down.
    genuine_pool = con.execute(f"""
        SELECT * FROM (
            SELECT transaction_id, amount, 0.0 AS loss_amount,
                   0.0 AS recovered_amount, transaction_datetime, customer_id
            FROM fct_outcomes
            WHERE is_fraud = 0 AND is_declined = 0 AND amount > 40
        ) USING SAMPLE {n_first} ROWS
    """).df()
    genuine_pool["dispute_reason_code"] = RNG.choice(
        ["13.1 Merchandise Not Received", "13.3 Not as Described",
         "13.7 Cancelled Merchandise", "10.4 Other Fraud - CNP"],
        len(genuine_pool), p=[0.38, 0.27, 0.20, 0.15])
    genuine_pool["is_first_party_misuse"] = 1

    disputes = pd.concat([third_party, genuine_pool], ignore_index=True)
    disputes["dispute_id"] = [f"D{i:07d}" for i in range(len(disputes))]
    disputes["raised_date"] = (disputes.transaction_datetime
                               + pd.to_timedelta(RNG.gamma(2.2, 7.0, len(disputes)), "D"))
    # Investigation effort is not uniform. First-party cases take longer because
    # the evidence is contested rather than obvious.
    hours = np.where(disputes.is_first_party_misuse == 1,
                     RNG.gamma(3.0, 0.42, len(disputes)),
                     RNG.gamma(2.2, 0.30, len(disputes)))
    disputes["investigation_hours"] = np.round(np.clip(hours, 0.15, 12), 2)
    disputes["handling_cost"] = np.round(disputes.investigation_hours * 32.0 + 9.50, 2)
    disputes["scheme_fee"] = 12.0
    disputes["outcome"] = np.where(
        disputes.is_first_party_misuse == 1,
        RNG.choice(["represented_won", "written_off"], len(disputes), p=[0.55, 0.45]),
        RNG.choice(["written_off", "recovered"], len(disputes), p=[0.79, 0.21]))
    disputes["write_off_amount"] = np.where(
        disputes.outcome.isin(["written_off"]),
        (disputes.amount - disputes.recovered_amount).clip(lower=0), 0.0).round(2)
    disputes["dispute_stage"] = "closed"

    con.register("disputes_df", disputes)
    con.execute("CREATE OR REPLACE TABLE disputes AS SELECT * FROM disputes_df")

    print(f"  disputes: {len(disputes):,}  "
          f"({disputes.is_first_party_misuse.mean()*100:.0f}% first-party misuse)")
    print(f"  chargeback rate: "
          f"{len(disputes)/chk.txns*100:.2f}% of transactions (fraud-related only; service disputes are out of scope)")
    print(f"  mean handling cost per case: GBP {disputes.handling_cost.mean():.2f}")

    con.execute(f"COPY fct_outcomes TO '{(PROC/'fct_outcomes.parquet').as_posix()}' (FORMAT PARQUET)")
    con.close()
    print(f"\nWarehouse written to {DB}")


if __name__ == "__main__":
    main()
