"""
Project 1 - Step 2b: build the customer spend panel with a behavioural response
to false declines.

WHY THIS IS A SEPARATE, EXPLICIT STEP
-------------------------------------
Question 8 in the brief is "do customers who experience a false decline reduce
their subsequent card usage, and by how much?". With real data you would simply
measure it. With synthetic data you cannot measure an effect that was never put
there - and quietly reporting a number you invented would be dishonest.

So the effect is INJECTED HERE, with a stated size, and then RECOVERED in step 3
by a matched difference-in-differences design that does not know the true value.
That turns a weakness into the most useful thing in the project: because ground
truth is known, step 3 can show that the naive comparison gets the answer badly
wrong and the matched design gets it right. On real data you would only ever see
the naive and matched estimates disagree; here you can prove which one is
trustworthy.

THE INJECTED EFFECT (ground truth, hidden from step 3)
  card-present false decline : 11.0% mean reduction in spend over the next 60 days
  e-commerce false decline   :  6.0% mean reduction
  heterogeneous across customers (Beta-distributed), so it is not a constant

Run:  python src/02b_behaviour_panel.py
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "fraud.duckdb"
RNG = np.random.default_rng(4242)

TRUE_EFFECT_CARD_PRESENT = 0.110
TRUE_EFFECT_ECOMMERCE = 0.060
WINDOW_DAYS = 60


def main() -> None:
    con = duckdb.connect(str(DB))

    # ---- who was falsely declined, and when for the first time -----------
    print("Identifying first false decline per customer ...")
    treated = con.execute("""
        SELECT customer_id,
               MIN(transaction_datetime)                        AS first_fd_at,
               ARG_MIN(channel, transaction_datetime)           AS first_fd_channel,
               COUNT(*)                                         AS false_declines
        FROM fct_outcomes
        WHERE is_fraud = 0 AND is_declined = 1
        GROUP BY customer_id
    """).df()
    treated["first_fd_at"] = pd.to_datetime(treated.first_fd_at)
    print(f"  customers with at least one false decline: {len(treated):,}")

    # Heterogeneous effect: Beta scaled to the stated mean, so some customers
    # barely react and a few react strongly. A constant effect would be a
    # giveaway and would also make the matched estimator look better than it is.
    mean_eff = np.where(treated.first_fd_channel == "ecommerce",
                        TRUE_EFFECT_ECOMMERCE, TRUE_EFFECT_CARD_PRESENT)
    treated["true_effect"] = np.clip(
        RNG.beta(2.0, 2.0, len(treated)) * 2 * mean_eff, 0, 0.60)
    con.register("treated_df", treated[["customer_id", "first_fd_at",
                                        "first_fd_channel", "true_effect"]])
    con.execute("CREATE OR REPLACE TABLE fd_treated AS SELECT * FROM treated_df")

    # ---- daily approved spend per customer -------------------------------
    print("Building the daily spend panel ...")
    con.execute("""
        CREATE OR REPLACE TABLE spend_daily_raw AS
        SELECT customer_id,
               CAST(transaction_datetime AS DATE) AS spend_date,
               SUM(amount)  AS spend,
               COUNT(*)     AS txns
        FROM fct_outcomes
        WHERE is_declined = 0 AND is_fraud = 0
        GROUP BY 1, 2
    """)

    # ---- apply the response ----------------------------------------------
    # Everything outside a treated customer's 60-day post-decline window is
    # untouched, which is what makes the pre-period a clean baseline.
    print(f"Applying the modelled response over {WINDOW_DAYS} days ...")
    con.execute(f"""
        CREATE OR REPLACE TABLE behaviour_panel AS
        SELECT
            s.customer_id,
            s.spend_date,
            s.txns,
            s.spend                                            AS spend_counterfactual,
            CASE WHEN t.customer_id IS NOT NULL
                  AND s.spend_date >  CAST(t.first_fd_at AS DATE)
                  AND s.spend_date <= CAST(t.first_fd_at AS DATE) + {WINDOW_DAYS}
                 THEN s.spend * (1 - t.true_effect)
                 ELSE s.spend END                              AS spend_observed,
            CASE WHEN t.customer_id IS NOT NULL THEN 1 ELSE 0 END AS is_treated,
            CAST(t.first_fd_at AS DATE)                        AS first_fd_date,
            t.first_fd_channel
        FROM spend_daily_raw s
        LEFT JOIN fd_treated t ON t.customer_id = s.customer_id
    """)

    chk = con.execute("""
        SELECT SUM(spend_counterfactual) AS cf, SUM(spend_observed) AS obs
        FROM behaviour_panel
    """).df().iloc[0]
    print(f"  spend suppressed by the response: "
          f"GBP {chk.cf - chk.obs:,.0f} "
          f"({(chk.cf - chk.obs)/chk.cf*100:.3f}% of total genuine spend)")
    print("  (step 3 must recover this without being told)")
    con.close()


if __name__ == "__main__":
    main()
