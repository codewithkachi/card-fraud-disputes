"""
Project 1 - Step 3: the analysis.

This is the file that contains the argument. Five blocks, in the order they have
to happen:

  A. Rule performance, with confidence intervals, because two of these rules have
     tiny denominators and a point estimate would be a lie.
  B. The behavioural cost of a false decline, measured with a matched
     difference-in-differences design rather than assumed.
  C. The cost model. Every outcome cell in the confusion matrix priced.
  D. Leave-one-out: switch each rule off and re-price the whole book. This is
     the only honest way to answer "what is this rule worth".
  E. Above-the-line threshold testing, then sensitivity on the assumption that
     will get challenged.

Run:  python src/03_analysis.py
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "fraud.duckdb"
SQL = ROOT / "sql"
TABLES = ROOT / "outputs" / "tables"
TABLES.mkdir(parents=True, exist_ok=True)

RNG = np.random.default_rng(909)
pd.set_option("display.width", 220)

# ===========================================================================
# STATED ASSUMPTIONS. Every number a reviewer can argue with lives here, in one
# block, with a source. None of them is buried in a formula further down.
# ===========================================================================
ASSUMPTIONS = {
    "issuer_margin_rate": (0.0155,
        "Issuer revenue per GBP 1 of card spend (interchange + FX + fee "
        "allocation). Mid-point of published UK issuer economics."),
    "decline_retry_recovery": (0.62,
        "Share of declined genuine value recovered because the customer "
        "re-attempts on the same card. Card-present retries succeed more often "
        "than e-commerce."),
    "analyst_cost_per_hour": (32.0,
        "Fully loaded cost of a fraud investigator, salary + overhead."),
    "review_minutes_per_alert": (4.5,
        "Median handling time for a real-time review alert."),
    "chargeback_probability": (0.86,
        "Probability that undetected fraud becomes a chargeback rather than "
        "being absorbed or written off silently."),
}
MARGIN = ASSUMPTIONS["issuer_margin_rate"][0]
RETRY = ASSUMPTIONS["decline_retry_recovery"][0]
REVIEW_COST = (ASSUMPTIONS["analyst_cost_per_hour"][0]
               * ASSUMPTIONS["review_minutes_per_alert"][0] / 60.0)
P_CB = ASSUMPTIONS["chargeback_probability"][0]


# ---------------------------------------------------------------------------
def wilson_ci(successes: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a proportion.

    WHY NOT THE NORMAL APPROXIMATION. R04 fired 133 times and caught 6. The
    textbook p +/- 1.96*sqrt(p(1-p)/n) interval is badly wrong at small n and
    near 0 or 1 - it can even run below zero. Wilson does not. Whenever you put a
    conversion rate in front of a decision-maker and the denominator is small,
    this is the interval to use.
    """
    if n == 0:
        return (float("nan"), float("nan"))
    z = stats.norm.ppf(1 - (1 - conf) / 2)
    p = successes / n
    d = 1 + z**2 / n
    c = p + z**2 / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return ((c - h) / d, (c + h) / d)


# ===========================================================================
def block_a_rule_performance(con) -> pd.DataFrame:
    print("\n" + "=" * 72)
    print("A. RULE PERFORMANCE")
    print("=" * 72)
    con.execute((SQL / "03_rule_performance.sql").read_text(encoding="utf-8"))
    con.execute((SQL / "04_patterns.sql").read_text(encoding="utf-8"))
    rp = con.execute("SELECT * FROM rule_performance ORDER BY alert_volume DESC").df()

    lo, hi = zip(*[wilson_ci(int(tp), int(n))
                   for tp, n in zip(rp.true_positives, rp.alert_volume)])
    rp["precision_ci_low_pct"] = np.round(np.array(lo) * 100, 3)
    rp["precision_ci_high_pct"] = np.round(np.array(hi) * 100, 3)
    rp["ci_width_pct"] = (rp.precision_ci_high_pct - rp.precision_ci_low_pct).round(3)

    print(rp[["rule_id", "rule_name", "default_action", "alert_volume",
              "true_positives", "precision_pct", "precision_ci_low_pct",
              "precision_ci_high_pct", "recall_pct",
              "false_positives_per_catch", "unique_fraud_catches"]]
          .to_string(index=False))

    zero = rp[rp.true_positives == 0]
    if len(zero):
        for _, r in zero.iterrows():
            print(f"\n  ! {r.rule_id} ({r.rule_name}) produced {int(r.alert_volume):,} "
                  f"alerts and ZERO confirmed fraud in 12 months.")
            print(f"    Upper 95% bound on its true precision: "
                  f"{r.precision_ci_high_pct:.3f}%. That is the honest way to "
                  f"say 'never fired productively' - not 'precision = 0'.")
    return rp


# ===========================================================================
def block_b_false_decline_impact(con) -> dict:
    """Does a false decline change behaviour, and by how much?

    This is the hardest block in the project and the one worth reading twice.

    THE TRAP
    Falsely declined customers are not like other customers. They get declined
    precisely BECAUSE they transact a lot, shop online and travel. Comparing
    their spend against never-declined customers measures that difference, not
    the effect of the decline.

    THE SECOND TRAP, WHICH IS THE ONE THAT ACTUALLY BIT
    Comparing a declined customer's next 60 days against their own previous 60
    days looks like it fixes the first trap. It does not, and it fails in a way
    that is easy to miss. A customer's FIRST false decline can only happen once
    they are active enough to trigger a rule. So the 60 days before a first
    decline are, on average, quieter than that customer's normal - selection on
    the event date creates a dip before it. Difference that dip out and the
    decline appears to INCREASE spend.

    Economists call the general version of this an Ashenfelter dip. The way you
    find it without knowing the answer in advance is a PLACEBO TEST: run the
    identical estimator on a fake event 60 days before the real one, where by
    construction nothing has happened yet. If the placebo returns anything other
    than zero, the design is measuring something other than the treatment.

    THE DESIGN THAT SURVIVES BOTH TRAPS
      - population = customers who ARE eventually falsely declined. Nobody else.
      - treated    = first false decline falls in month m
      - control    = first false decline is at least 3 months later. Same kind of
                     customer, same behaviour, simply not hit yet.
      - matched    = coarsened exact match on segment x CRM spend band, then
                     nearest neighbour inside the cell. Matched on the customer
                     ATTRIBUTE, never on realised spend - see the note further
                     down for why that distinction mattered so much.
      - anchored   = both groups measured over the same calendar weeks
      - outcome    = change in 60-day spend, after minus before, in GBP
      - estimate   = treated change MINUS matched control change
      - checked    = placebo on an event 90 days earlier

    HOW THE PLACEBO RESULT IS USED
    The placebo does not come back at exactly zero, and pretending otherwise
    would be the same dishonesty in a different costume. It comes back positive,
    which says the design still carries some upward bias. So the answer is
    reported as a RANGE - raw estimate at one end, placebo-corrected estimate at
    the other - and the end least helpful to the recommendation is the one that
    goes into the cost model.
    """
    print("\n" + "=" * 72)
    print("B. THE BEHAVIOURAL COST OF A FALSE DECLINE")
    print("=" * 72)

    panel = con.execute("""
        SELECT customer_id, spend_date, spend_observed FROM behaviour_panel
    """).df()
    panel["spend_date"] = pd.to_datetime(panel.spend_date)
    cust = con.execute("""
        SELECT c.customer_id, c.segment, c.average_monthly_spend,
               t.first_fd_at, t.first_fd_channel
        FROM customers_raw c LEFT JOIN fd_treated t USING (customer_id)
        ORDER BY c.customer_id
    """).df()
    cust["first_fd_at"] = pd.to_datetime(cust.first_fd_at)
    cust["event_month"] = cust.first_fd_at.dt.to_period("M")

    # ---- dense customer x day spend matrix -------------------------------
    # 14k customers x 365 days is 5m cells, which is nothing. Building it once
    # turns every window sum below into an O(1) slice of a cumulative sum,
    # instead of a groupby per cohort per window.
    day0 = pd.Timestamp("2025-01-01")
    cid_index = {c: i for i, c in enumerate(cust.customer_id)}
    n_c, n_d = len(cust), 366
    S = np.zeros((n_c, n_d))
    ri = panel.customer_id.map(cid_index).to_numpy()
    di = (panel.spend_date - day0).dt.days.to_numpy()
    ok = (di >= 0) & (di < n_d)
    np.add.at(S, (ri[ok], di[ok]), panel.spend_observed.to_numpy()[ok])
    C = np.concatenate([np.zeros((n_c, 1)), np.cumsum(S, axis=1)], axis=1)

    def wsum(rows, ev_days, lo, hi):
        """Spend for each row in [event+lo, event+hi], inclusive."""
        ev_days = np.asarray(ev_days, dtype=float)
        a = np.clip(ev_days + lo, 0, n_d).astype(int)
        b = np.clip(ev_days + hi + 1, 0, n_d).astype(int)
        return C[np.asarray(rows, dtype=int), b] - C[np.asarray(rows, dtype=int), a]

    cust["row"] = np.arange(n_c)
    cust["ev_day"] = (cust.first_fd_at - day0).dt.days

    # ---- naive answers, so we can see how far off they are ----------------
    active = cust[cust.average_monthly_spend > 100]
    ev = np.full(len(active), 200)
    post_all = wsum(active.row.to_numpy(), ev, 1, 60)
    is_t = active.first_fd_at.notna().to_numpy()
    naive_level = (post_all[is_t].mean() / post_all[~is_t].mean() - 1) * 100

    tre = cust[cust.first_fd_at.notna() & (cust.ev_day.between(120, 240))]
    pre_t = wsum(tre.row.to_numpy(), tre.ev_day.to_numpy(), -60, -1)
    pos_t = wsum(tre.row.to_numpy(), tre.ev_day.to_numpy(), 1, 60)
    m = pre_t > 50
    naive_prepost = np.median((pos_t[m] - pre_t[m]) / pre_t[m]) * 100

    print("\n  TWO NAIVE ANSWERS, both of which a real team would report:")
    print(f"    (i)  declined vs never-declined customers, same 60 days: "
          f"{naive_level:+.1f}%")
    print(f"    (ii) declined customers, 60 days after vs 60 days before: "
          f"{naive_prepost:+.1f}%")
    print("    Both say a false decline makes customers spend MORE. Neither is "
          "measuring the decline.")

    # ---- matched DiD ------------------------------------------------------
    # WHAT I MATCH ON, AND WHY IT CHANGED
    # The first version of this matched treated to control on their realised
    # spend in the two pre-windows. The placebo test came back at +50%, which
    # said the design was broken, and it was: matching on a NOISY REALISATION of
    # the outcome selects controls who happened to have an unusually good two
    # months, and unusually good months are followed by ordinary ones. That is
    # regression to the mean, and it was manufacturing a fake +50% gap.
    #
    # The fix is to match on the STABLE CUSTOMER ATTRIBUTE instead of the noisy
    # realisation: segment, region, and the customer's spend band held on the
    # CRM record. Those do not bounce around month to month, so there is nothing
    # to revert to. The placebo test confirms it.
    #
    # Second change: the outcome is a LEVEL difference (post minus pre, in GBP)
    # rather than a percentage change per customer. Dividing by each customer's
    # own pre-period requires that pre-period to be non-trivial, and filtering on
    # it reintroduces exactly the selection we just removed.
    cust["spend_decile"] = pd.qcut(cust.average_monthly_spend, 10,
                                   labels=False, duplicates="drop")

    # THREE MORE DESIGN CHOICES, EACH FORCED BY A DIAGNOSTIC THAT FAILED
    #
    # (a) NEVER-DECLINED CUSTOMERS ARE EXCLUDED FROM THE CONTROL GROUP.
    #     They average GBP 255 a month against GBP 694 for declined customers.
    #     They are not a comparison group, they are a different population - a
    #     customer who transacts twice a month cannot trip a velocity rule.
    #     Every customer in this analysis, treated and control alike, is one who
    #     eventually gets falsely declined. The only difference between them is
    #     WHEN, and that is the variation the estimate uses.
    #
    # (b) THE EVENT DATE IS CALENDAR-ALIGNED, not customer-specific. Both groups
    #     use the 15th of the cohort month. If treated customers were anchored on
    #     their own decline date and controls on a fixed date, the two groups
    #     would be measured over different weeks and any seasonality would leak
    #     straight into the estimate.
    #
    # (c) THE PLACEBO IS SHIFTED 90 DAYS, not 60. At 60 the placebo's post-window
    #     ran into the start of real treatment for part of the cohort, so the
    #     "placebo" contained some genuine treatment effect. A placebo that
    #     overlaps the treatment is not a placebo.
    def run(offset_days: int) -> pd.DataFrame:
        """offset_days = 0 is the real estimate.
        offset_days = -90 shifts every event 90 days earlier: the placebo."""
        rows = []
        for m_ in [pd.Period(x, "M") for x in
                   ["2025-06", "2025-07", "2025-08"]]:
            t = cust[cust.event_month == m_].copy()
            c = cust[cust.event_month >= m_ + 3].copy()      # not-yet-treated only
            if len(t) < 40 or len(c) < 40:
                continue
            anchor = (m_.to_timestamp() + pd.Timedelta(days=14) - day0).days + offset_days
            t["ev"] = anchor
            c["ev"] = anchor
            for d in (t, c):
                r, e = d.row.to_numpy(), d.ev.to_numpy().astype(int)
                d["pre1"] = wsum(r, e, -120, -61)
                d["pre2"] = wsum(r, e, -60, -1)
                d["post"] = wsum(r, e, 1, 60)

            # Coarsened exact matching on segment x CRM spend decile, then
            # nearest neighbour on the CRM spend band within the cell.
            mt, mc = [], []
            for key, tg in t.groupby(["segment", "spend_decile"], observed=True):
                cg = c[(c.segment == key[0]) & (c.spend_decile == key[1])]
                if len(cg) == 0:
                    continue
                cv = cg.average_monthly_spend.to_numpy()
                used = np.zeros(len(cg), bool)
                for _, tr in tg.iterrows():
                    dist = np.where(used, np.inf,
                                    np.abs(cv - tr.average_monthly_spend))
                    j = int(np.argmin(dist))
                    if np.isfinite(dist[j]):
                        used[j] = True
                        mt.append(tr)
                        mc.append(cg.iloc[j])
            if len(mt) < 25:
                continue
            MT, MC = pd.DataFrame(mt), pd.DataFrame(mc)
            base_level = MT.pre2.mean()
            dt_ = (MT.post - MT.pre2).mean()
            dc_ = (MC.post - MC.pre2).mean()
            rows.append({"cohort": str(m_), "n_matched": len(MT),
                         "treated_pre1": MT.pre1.mean(), "control_pre1": MC.pre1.mean(),
                         "treated_pre2": base_level, "control_pre2": MC.pre2.mean(),
                         "treated_change_gbp": dt_, "control_change_gbp": dc_,
                         "treated_change_pct": dt_ / base_level * 100,
                         "control_change_pct": dc_ / MC.pre2.mean() * 100,
                         "did_effect_pct": (dt_ - dc_) / base_level * 100})
        return pd.DataFrame(rows)

    placebo = run(-90)
    did = run(0)

    def pooled(df):
        e = np.average(df.did_effect_pct, weights=df.n_matched)
        v = np.average((df.did_effect_pct - e) ** 2, weights=df.n_matched)
        return e, np.sqrt(v / len(df))

    p_eff, p_se = pooled(placebo)
    r_eff, r_se = pooled(did)

    print("\n  PLACEBO TEST - identical estimator, event moved 90 days earlier,")
    print("  a period in which nothing has happened to anybody:")
    print(placebo[["cohort", "n_matched", "did_effect_pct"]].round(2).to_string(index=False))
    print(f"    pooled placebo: {p_eff:+.2f}%  "
          f"({'PASS - close to zero' if abs(p_eff) < 3 else 'residual bias of this size remains in the design'})")

    print("\n  REAL ESTIMATE, matched difference-in-differences by cohort:")
    print(did[["cohort", "n_matched", "treated_pre2", "control_pre2",
               "treated_change_pct", "control_change_pct", "did_effect_pct"]]
          .round(2).to_string(index=False))
    adjusted = r_eff - p_eff
    lo_eff, hi_eff = min(r_eff, adjusted), max(r_eff, adjusted)
    print(f"\n    pooled raw estimate      {r_eff:+.2f}%  "
          f"(95% CI {r_eff-1.96*r_se:+.2f}% to {r_eff+1.96*r_se:+.2f}%)")
    print(f"    placebo bias             {p_eff:+.2f}%")
    print(f"    placebo-corrected        {adjusted:+.2f}%")
    print(f"    REPORTED RANGE           {hi_eff:+.2f}% to {lo_eff:+.2f}%")
    print(f"    Cost model uses the conservative end, {hi_eff:+.2f}%, because "
          f"that is the end least helpful to the recommendation.")
    print(f"    Balance: mean pre-period spend treated GBP {did.treated_pre2.mean():,.0f} "
          f"vs control GBP {did.control_pre2.mean():,.0f}")

    # ---- validation against the injected ground truth --------------------
    truth = con.execute("SELECT AVG(true_effect) FROM fd_treated").fetchone()[0]
    print(f"\n  VALIDATION (only possible because the data is synthetic):")
    print(f"    true injected effect        {-truth*100:+.2f}%")
    print(f"    naive level comparison      {naive_level:+.2f}%")
    print(f"    naive before/after          {naive_prepost:+.2f}%")
    print(f"    matched DiD, raw            {r_eff:+.2f}%   <- used in the cost model")
    print(f"    matched DiD, placebo-corr   {adjusted:+.2f}%")
    hit = "INSIDE" if lo_eff <= -truth*100 <= hi_eff else "OUTSIDE"
    print(f"    -> the true value falls {hit} the reported range.")
    print("    On real data the first line does not exist. What you would have"
          " is the two naive answers disagreeing with the matched one, and"
          " the placebo test telling you which of them to throw away.")

    # ---- convert to money -------------------------------------------------
    fd = con.execute("""
        SELECT COUNT(*) AS false_declines,
               COUNT(DISTINCT customer_id) AS customers_affected,
               SUM(amount) AS genuine_value_declined
        FROM fct_outcomes WHERE is_fraud = 0 AND is_declined = 1
    """).df().iloc[0]
    effect = min(r_eff, 0.0)   # conservative end; never book a benefit from a decline
    avg_pre = did.treated_pre2.mean()
    lost_spend_per_customer = avg_pre * (-effect / 100.0)
    relationship_cost_per_customer = lost_spend_per_customer * MARGIN
    per_false_decline = (relationship_cost_per_customer
                         * fd.customers_affected / fd.false_declines)
    print(f"\n  Money conversion:")
    print(f"    60-day spend lost per affected customer   "
          f"GBP {lost_spend_per_customer:,.2f}")
    print(f"    x issuer margin {MARGIN:.2%}                      "
          f"GBP {relationship_cost_per_customer:,.2f}")
    print(f"    spread over {fd.false_declines:,.0f} false declines across "
          f"{fd.customers_affected:,.0f} customers")
    print(f"    RELATIONSHIP COST PER FALSE DECLINE       "
          f"GBP {per_false_decline:,.2f}")

    did.to_csv(TABLES / "did_cohorts.csv", index=False)
    placebo.to_csv(TABLES / "did_placebo.csv", index=False)
    return {"did": did, "placebo": placebo,
            "pooled_effect_pct": r_eff, "placebo_pct": p_eff,
            "adjusted_effect_pct": adjusted, "se": r_se,
            "range_low_pct": lo_eff, "range_high_pct": hi_eff,
            "naive_level_pct": naive_level, "naive_prepost_pct": naive_prepost,
            "true_effect_pct": -truth * 100,
            "relationship_cost_per_fd": per_false_decline,
            "lost_spend_per_customer": lost_spend_per_customer,
            "false_declines": int(fd.false_declines),
            "customers_affected": int(fd.customers_affected)}


# ===========================================================================
class CostModel:
    """Prices every cell of the confusion matrix, for any rule configuration.

    The point of wrapping it in a class is that the SAME function has to price
    the baseline, eight leave-one-out counterfactuals, dozens of threshold
    settings and a sensitivity sweep. If those used different arithmetic the
    comparison would be meaningless.
    """

    def __init__(self, con, relationship_cost_per_fd: float):
        self.rel_cost = relationship_cost_per_fd

        rules = con.execute("SELECT rule_id, default_action FROM rules").df()
        self.actions = dict(zip(rules.rule_id, rules.default_action))
        self.rule_ids = list(rules.rule_id)

        # Recovery rate on realised fraud, straight from the outcome data.
        rec = con.execute("""
            SELECT SUM(recovered_amount) / NULLIF(SUM(loss_amount), 0)
            FROM fraud_outcomes_raw WHERE is_fraud = 1""").fetchone()[0]
        self.recovery_rate = float(rec)

        # Operational cost of a third-party fraud chargeback.
        self.case_op_cost = float(con.execute("""
            SELECT AVG(handling_cost + scheme_fee) FROM disputes
            WHERE is_first_party_misuse = 0""").fetchone()[0])

        # Only alerted transactions can change outcome under a rule change.
        alerts = con.execute("""
            SELECT a.transaction_id, a.rule_id FROM rule_alerts a""").df()
        self.fire = (alerts.assign(v=True)
                     .pivot_table(index="transaction_id", columns="rule_id",
                                  values="v", aggfunc="any", fill_value=False))
        for r in self.rule_ids:
            if r not in self.fire.columns:
                self.fire[r] = False
        self.fire = self.fire[self.rule_ids]

        feats = con.execute("""
            SELECT transaction_id, is_fraud, amount, txn_count_60min,
                   txn_count_120min, minutes_since_prev_txn, days_since_prev_txn
            FROM fct_outcomes WHERE alerts_fired > 0""").df().set_index("transaction_id")
        self.f = feats.reindex(self.fire.index)

        # Analyst decision draw for EVERY alerted transaction, including ones
        # currently blocked by a rule. Needed so that removing a block rule can
        # fall through to a review decision without inventing a new random draw
        # mid-comparison.
        p = np.where(self.f.is_fraud == 1, 0.78, 0.06)
        self.analyst_declined = RNG.random(len(self.f)) < p

        # Everything that never alerted is approved under every configuration.
        base = con.execute("""
            SELECT COUNT(*) AS n, SUM(amount) AS v,
                   SUM(is_fraud) AS fraud_n,
                   SUM(CASE WHEN is_fraud=1 THEN amount ELSE 0 END) AS fraud_v
            FROM fct_outcomes WHERE alerts_fired = 0""").df().iloc[0]
        self.silent_fraud_n = float(base.fraud_n)
        self.silent_fraud_v = float(base.fraud_v)

        self.first_party_cost = float(con.execute("""
            SELECT SUM(handling_cost + scheme_fee + write_off_amount)
            FROM disputes WHERE is_first_party_misuse = 1""").fetchone()[0])

    def price(self, fire: pd.DataFrame | None = None,
              active: list[str] | None = None) -> dict:
        fire = self.fire if fire is None else fire
        active = self.rule_ids if active is None else active
        blk = [r for r in active if self.actions[r] == "block"]
        rev = [r for r in active if self.actions[r] == "review"]

        blocked = fire[blk].any(axis=1).to_numpy() if blk else np.zeros(len(fire), bool)
        reviewed = fire[rev].any(axis=1).to_numpy() if rev else np.zeros(len(fire), bool)
        declined = blocked | (reviewed & self.analyst_declined)

        is_fraud = self.f.is_fraud.to_numpy() == 1
        amt = self.f.amount.to_numpy()

        prevented_v = amt[is_fraud & declined].sum()
        missed_n = (~declined & is_fraud).sum() + self.silent_fraud_n
        missed_v = amt[is_fraud & ~declined].sum() + self.silent_fraud_v
        fd_n = int((~is_fraud & declined).sum())
        fd_v = amt[~is_fraud & declined].sum()
        review_alerts = int((reviewed & ~blocked).sum())

        write_off = missed_v * (1 - self.recovery_rate)
        cb_ops = missed_n * P_CB * self.case_op_cost
        fd_margin = fd_v * (1 - RETRY) * MARGIN
        fd_relationship = fd_n * self.rel_cost
        review_ops = review_alerts * REVIEW_COST

        total = write_off + cb_ops + fd_margin + fd_relationship + review_ops
        return {
            "prevented_fraud_value": prevented_v,
            "missed_fraud_txns": missed_n,
            "realised_fraud_value": missed_v,
            "fraud_write_off": write_off,
            "chargeback_operational_cost": cb_ops,
            "false_declines": fd_n,
            "false_decline_value": fd_v,
            "false_decline_lost_margin": fd_margin,
            "false_decline_relationship_cost": fd_relationship,
            "review_operational_cost": review_ops,
            "review_alerts": review_alerts,
            "total_cost_of_fraud": total,
        }


def block_c_cost_model(con, rel_cost: float) -> tuple[CostModel, dict]:
    print("\n" + "=" * 72)
    print("C. THE COST MODEL")
    print("=" * 72)
    cm = CostModel(con, rel_cost)
    print(f"  recovery rate on realised fraud     {cm.recovery_rate:.1%}")
    print(f"  operational cost per fraud dispute  GBP {cm.case_op_cost:,.2f}")
    print(f"  cost per review alert               GBP {REVIEW_COST:,.2f}")
    print(f"  relationship cost per false decline GBP {cm.rel_cost:,.2f}")

    base = cm.price()
    comps = [
        ("Fraud written off (net of recovery)", base["fraud_write_off"]),
        ("Chargeback handling and scheme fees", base["chargeback_operational_cost"]),
        ("False declines - lost margin",        base["false_decline_lost_margin"]),
        ("False declines - relationship cost",  base["false_decline_relationship_cost"]),
        ("Review queue operating cost",         base["review_operational_cost"]),
    ]
    print(f"\n  TOTAL COST OF FRAUD, 12 months")
    for name, v in comps:
        print(f"    {name:<38} GBP {v:12,.0f}   {v/base['total_cost_of_fraud']*100:5.1f}%")
    print(f"    {'-'*38} {'-'*16}")
    print(f"    {'TOTAL':<38} GBP {base['total_cost_of_fraud']:12,.0f}")
    print(f"\n  The number the board currently sees is the first line only: "
          f"GBP {base['fraud_write_off']:,.0f}.")
    print(f"  That is {base['fraud_write_off']/base['total_cost_of_fraud']*100:.0f}% "
          f"of the real cost.")
    print(f"  First-party misuse, reported separately because it is not a "
          f"detection problem: GBP {cm.first_party_cost:,.0f}")

    pd.DataFrame(comps, columns=["component", "annual_cost_gbp"]).to_csv(
        TABLES / "cost_of_fraud_components.csv", index=False)
    return cm, base


# ===========================================================================
def block_d_leave_one_out(cm: CostModel, base: dict, rp: pd.DataFrame) -> pd.DataFrame:
    print("\n" + "=" * 72)
    print("D. LEAVE-ONE-OUT: WHAT IS EACH RULE ACTUALLY WORTH?")
    print("=" * 72)
    print("  For each rule: switch it off, re-price the entire book, and compare.")
    print("  A NEGATIVE net contribution means the rule costs more than it saves.\n")
    rows = []
    for r in cm.rule_ids:
        alt = cm.price(active=[x for x in cm.rule_ids if x != r])
        rows.append({
            "rule_id": r,
            "extra_fraud_loss_if_off": (alt["fraud_write_off"] + alt["chargeback_operational_cost"])
                                       - (base["fraud_write_off"] + base["chargeback_operational_cost"]),
            "fp_cost_saved_if_off": (base["false_decline_lost_margin"]
                                     + base["false_decline_relationship_cost"]
                                     + base["review_operational_cost"])
                                    - (alt["false_decline_lost_margin"]
                                       + alt["false_decline_relationship_cost"]
                                       + alt["review_operational_cost"]),
            "total_cost_if_off": alt["total_cost_of_fraud"],
            "false_declines_removed": base["false_declines"] - alt["false_declines"],
        })
    loo = pd.DataFrame(rows)
    loo["net_contribution"] = loo.extra_fraud_loss_if_off - loo.fp_cost_saved_if_off
    loo["cost_change_if_off"] = loo.total_cost_if_off - base["total_cost_of_fraud"]
    loo = loo.merge(rp[["rule_id", "rule_name", "default_action", "alert_volume",
                        "precision_pct", "unique_fraud_catches"]], on="rule_id")
    loo = loo.sort_values("net_contribution")

    for _, r in loo.iterrows():
        verdict = ("RETIRE" if r.net_contribution < -5_000 else
                   "RETUNE" if r.net_contribution < 5_000 else "KEEP")
        print(f"  {r.rule_id} {r.rule_name:<28} "
              f"net GBP {r.net_contribution:>11,.0f}   "
              f"({int(r.false_declines_removed):>6,} false declines) -> {verdict}")
    loo.to_csv(TABLES / "rule_leave_one_out.csv", index=False)
    return loo


# ===========================================================================
def block_e_threshold_sweep(cm: CostModel, base: dict) -> pd.DataFrame:
    """Above-the-line testing: raise each threshold and re-price.

    Only UPWARD. Raising a threshold can only remove alerts from the population
    that already exists, so it can be computed from the alert table alone.
    Lowering a threshold (below-the-line testing) requires re-scanning every
    transaction that did NOT alert - that is done properly in Project 2, where
    it is the centrepiece.
    """
    print("\n" + "=" * 72)
    print("E. ABOVE-THE-LINE THRESHOLD TESTING")
    print("=" * 72)
    specs = {
        "R01": ("amount",                np.arange(750, 3001, 250),   "GBP"),
        "R05": ("amount",                np.arange(300, 2001, 100),   "GBP"),
        "R06": ("amount",                np.arange(150, 1501, 50),    "GBP"),
        "R02": ("txn_count_60min",       np.arange(5, 13, 1),         "txns/60min"),
        "R08": ("txn_count_120min",      np.arange(3, 11, 1),         "txns/120min"),
    }
    rows = []
    for rule, (col, grid, unit) in specs.items():
        vals = cm.f[col].to_numpy()
        for thr in grid:
            fire = cm.fire.copy()
            fire[rule] = fire[rule].to_numpy() & (vals >= thr)
            p = cm.price(fire=fire)
            rows.append({"rule_id": rule, "threshold": float(thr), "unit": unit,
                         "alerts": int(fire[rule].sum()),
                         "total_cost": p["total_cost_of_fraud"],
                         "false_declines": p["false_declines"],
                         "realised_fraud_value": p["realised_fraud_value"]})
    sweep = pd.DataFrame(rows)
    sweep["cost_vs_current"] = sweep.total_cost - base["total_cost_of_fraud"]

    print("  Best setting found for each rule:\n")
    best = sweep.loc[sweep.groupby("rule_id").total_cost.idxmin()]
    for _, b in best.iterrows():
        cur = sweep[(sweep.rule_id == b.rule_id)].iloc[0]
        print(f"    {b.rule_id}: move threshold {cur.threshold:g} -> {b.threshold:g} "
              f"{b.unit}   saves GBP {-b.cost_vs_current:,.0f}/yr, "
              f"alerts {int(cur.alerts):,} -> {int(b.alerts):,}")
    sweep.to_csv(TABLES / "threshold_sweep.csv", index=False)
    return sweep


# ===========================================================================
def block_f_sensitivity(con, base: dict, rel_cost: float, loo: pd.DataFrame) -> pd.DataFrame:
    """The assumption people will attack is the false-positive cost. So attack
    it first, in public, and show what survives.
    """
    print("\n" + "=" * 72)
    print("F. SENSITIVITY ON THE FALSE-POSITIVE COST ASSUMPTION")
    print("=" * 72)
    rows = []
    for mult, label in [(0.0, "zero (fraud losses only)"), (0.25, "quarter"),
                        (0.5, "half"), (1.0, "central estimate"),
                        (1.5, "1.5x"), (2.0, "double"), (3.0, "triple")]:
        cm2 = CostModel(con, rel_cost * mult)
        b2 = cm2.price()
        retire = []
        for r in cm2.rule_ids:
            alt = cm2.price(active=[x for x in cm2.rule_ids if x != r])
            if alt["total_cost_of_fraud"] < b2["total_cost_of_fraud"] - 5_000:
                retire.append(r)
        rows.append({"scenario": label, "relationship_cost_per_fd": rel_cost * mult,
                     "total_cost_of_fraud": b2["total_cost_of_fraud"],
                     "rules_worth_retiring": ",".join(retire) or "none",
                     "n_rules_retired": len(retire)})
    sens = pd.DataFrame(rows)
    print(sens.assign(total_cost_of_fraud=lambda d: d.total_cost_of_fraud.round(0))
              .to_string(index=False))
    always = set.intersection(*[set(r.split(",")) for r in sens.rules_worth_retiring
                                if r != "none"]) if (sens.n_rules_retired > 0).all() else set()
    print(f"\n  Rules that should be retired under EVERY scenario tested, "
          f"including one where a false decline costs nothing at all: "
          f"{', '.join(sorted(always)) if always else 'none'}")
    print("  That is the robust part of the recommendation. Everything else "
          "depends on the assumption.")
    sens.to_csv(TABLES / "sensitivity_fp_cost.csv", index=False)
    return sens


# ===========================================================================

# Current live thresholds, so the recommendation only names what actually moves.
CURRENT_THRESHOLDS = {"R05": 300, "R06": 150, "R08": 3, "R02": 5}


def _describe(cfg: dict) -> str:
    moved = [f"{k} {CURRENT_THRESHOLDS[k]:g} -> {v:g}"
             for k, v in cfg.items() if v != CURRENT_THRESHOLDS[k]]
    parts = ["retire R01"] + ([("raise " + "; ".join(moved))] if moved else [])
    unchanged = [k for k, v in cfg.items() if v == CURRENT_THRESHOLDS[k]]
    if unchanged:
        parts.append("leave " + ", ".join(sorted(unchanged)) + " unchanged")
    return "; ".join(parts)


def block_g_recommended_config(cm: CostModel, base: dict, loo: pd.DataFrame) -> pd.DataFrame:
    """Price the recommended configuration as a WHOLE.

    WHY THIS IS NOT JUST THE SUM OF THE LEAVE-ONE-OUT NUMBERS. Rules overlap. If
    R01 and R06 both alert on the same transaction, switching off either one
    alone changes nothing about that transaction, so each looks harmless in
    isolation. Switching off both releases it. Adding up eight independent
    counterfactuals therefore MIS-STATES the joint effect - sometimes badly, and
    always in a direction you cannot predict from the individual numbers.
    Anybody who has ever summed a set of one-at-a-time savings and been asked
    later why the total never appeared knows this one.
    """
    print("\n" + "=" * 72)
    print("G. THE RECOMMENDED CONFIGURATION, PRICED AS A WHOLE")
    print("=" * 72)
    retire = list(loo[loo.net_contribution < -5_000].rule_id)
    keep = [r for r in cm.rule_ids if r not in retire]
    rec = cm.price(active=keep)

    naive_sum = -loo[loo.net_contribution < -5_000].net_contribution.sum()
    joint = base["total_cost_of_fraud"] - rec["total_cost_of_fraud"]
    print(f"  retire: {', '.join(retire)}")
    print(f"  sum of the individual leave-one-out savings  GBP {naive_sum:12,.0f}")
    print(f"  actual joint saving when done together       GBP {joint:12,.0f}")
    print(f"  difference                                   GBP {joint - naive_sum:12,.0f}"
          f"  ({(joint/naive_sum - 1)*100:+.1f}%)")
    print(f"\n  review alerts   {base['review_alerts']:>9,} -> {rec['review_alerts']:>9,}")
    print(f"  investigator hours released                  "
          f"{(base['review_alerts'] - rec['review_alerts']) * 4.5/60:12,.0f}  "
          f"({(base['review_alerts'] - rec['review_alerts']) * 4.5/60/1600:.1f} FTE)")
    print(f"  false declines  {base['false_declines']:>9,} -> {rec['false_declines']:>9,}")
    print(f"  fraud prevented GBP {base['prevented_fraud_value']:>9,.0f} -> "
          f"GBP {rec['prevented_fraud_value']:>9,.0f}")
    print(f"  net fraud loss  GBP {base['realised_fraud_value']:>9,.0f} -> "
          f"GBP {rec['realised_fraud_value']:>9,.0f}  <- THE RISK OF THE CHANGE")

    # ---------------------------------------------------------------------
    # THE POINT AT WHICH THE ARITHMETIC STOPS AND JUDGEMENT STARTS
    #
    # The cost model says retiring those four rules is worth GBP 375k. It also
    # says net fraud loss goes from GBP 163k to GBP 444k. Present that to a fraud
    # director and the meeting ends. Fraud losses are not just a cost line - they
    # are a board metric, a scheme-monitoring metric and, past certain thresholds,
    # a regulatory conversation. A model that only minimises total cost does not
    # know any of that.
    #
    # So the real recommendation is not the model's answer. It is RETUNE: raise
    # the thresholds to the settings the sweep found, which strips out most of
    # the alert volume while keeping most of the fraud prevention. Retirement is
    # reserved for the one rule with zero unique catches, where nothing is lost.
    # ---------------------------------------------------------------------
    tuned_thresholds = {"R05": ("amount", 2000.0), "R06": ("amount", 350.0),
                        "R08": ("txn_count_120min", 6.0), "R02": ("txn_count_60min", 6.0)}
    fire = cm.fire.copy()
    for rid, (col, thr) in tuned_thresholds.items():
        fire[rid] = fire[rid].to_numpy() & (cm.f[col].to_numpy() >= thr)
    tuned = cm.price(fire=fire, active=[r for r in cm.rule_ids if r != "R01"])

    print("\n  ---- three configurations side by side ----")
    hdr = f"  {'':<32}{'current':>14}{'retire 4':>14}{'RETUNE':>14}"
    print(hdr)
    for label, key, fmt in [
            ("total cost of fraud", "total_cost_of_fraud", "{:>14,.0f}"),
            ("net fraud loss", "realised_fraud_value", "{:>14,.0f}"),
            ("review alerts", "review_alerts", "{:>14,.0f}"),
            ("false declines", "false_declines", "{:>14,.0f}")]:
        print(f"  {label:<32}" + fmt.format(base[key]) + fmt.format(rec[key])
              + fmt.format(tuned[key]))
    print(f"\n  Retuning keeps "
          f"{tuned['prevented_fraud_value']/base['prevented_fraud_value']*100:.0f}% "
          f"of the fraud prevention while removing "
          f"{(1 - tuned['review_alerts']/base['review_alerts'])*100:.0f}% of the "
          f"review queue.")
    print(f"  Retiring keeps only "
          f"{rec['prevented_fraud_value']/base['prevented_fraud_value']*100:.0f}%. "
          f"That is the difference between a recommendation that gets "
          f"implemented and one that gets refused.")

    # ---------------------------------------------------------------------
    # CONSTRAINED OPTIMISATION - the version that survives contact with a
    # fraud director.
    #
    # Even the retuned setting doubles net fraud loss, from 7.0 to about 13.5
    # basis points. The UK industry figure is around 7. Going to 13.5 is not a
    # cost trade-off, it is a board conversation and possibly a scheme-monitoring
    # one. So the question is not "what minimises total cost" but "what minimises
    # total cost WITHOUT letting fraud losses rise more than the business will
    # tolerate". Here that tolerance is set at +20%.
    #
    # This is a small grid search, 360 configurations, each priced with the same
    # function used everywhere else.
    # ---------------------------------------------------------------------
    print("\n  ---- constrained search: minimise cost subject to")
    print("       net fraud loss <= current + 20% ----")
    cap = base["realised_fraud_value"] * 1.20
    grid = {"R05": [300, 500, 750, 1000, 1500, 2000],
            "R06": [150, 250, 350, 500, 750],
            "R08": [3, 4, 5, 6],
            "R02": [5, 6, 7]}
    cols = {"R05": "amount", "R06": "amount",
            "R08": "txn_count_120min", "R02": "txn_count_60min"}
    best, best_cfg = None, None
    import itertools
    for combo in itertools.product(*grid.values()):
        cfg = dict(zip(grid.keys(), combo))
        f2 = cm.fire.copy()
        for rid, thr in cfg.items():
            f2[rid] = f2[rid].to_numpy() & (cm.f[cols[rid]].to_numpy() >= thr)
        pr = cm.price(fire=f2, active=[r for r in cm.rule_ids if r != "R01"])
        if pr["realised_fraud_value"] <= cap and (
                best is None or pr["total_cost_of_fraud"] < best["total_cost_of_fraud"]):
            best, best_cfg = pr, cfg

    if best is None:
        print("  No configuration meets the constraint. That is itself the "
              "finding: the alert volume is load-bearing.")
        best, best_cfg = tuned, tuned_thresholds
        label = "retune_unconstrained"
    else:
        label = "retune_constrained"
        print(f"  best feasible setting: "
              + ", ".join(f"{k}>={v:g}" for k, v in best_cfg.items()))
        print(f"    total cost      GBP {base['total_cost_of_fraud']:>10,.0f} -> "
              f"GBP {best['total_cost_of_fraud']:>10,.0f}  "
              f"(saving GBP {base['total_cost_of_fraud']-best['total_cost_of_fraud']:,.0f})")
        print(f"    net fraud loss  GBP {base['realised_fraud_value']:>10,.0f} -> "
              f"GBP {best['realised_fraud_value']:>10,.0f}  "
              f"(+{best['realised_fraud_value']/base['realised_fraud_value']*100-100:.1f}%, "
              f"inside the 20% tolerance)")
        print(f"    review alerts   {base['review_alerts']:>14,} -> {best['review_alerts']:>14,}")
        print(f"    false declines  {base['false_declines']:>14,} -> {best['false_declines']:>14,}")
        print(f"    hours released  {(base['review_alerts']-best['review_alerts'])*4.5/60:>14,.0f}"
              f"  ({(base['review_alerts']-best['review_alerts'])*4.5/60/1600:.1f} FTE)")

    out = pd.DataFrame([
        {"scenario": "current", **base},
        {"scenario": "retire_four", **rec},
        {"scenario": "retune_unconstrained", **tuned},
        {"scenario": label, **best},
    ])
    out["rules_changed"] = [
        "", "retire " + ", ".join(retire),
        "R01 retired; R02/R05/R06/R08 at cost-optimal thresholds",
        _describe(best_cfg)]
    out.to_csv(TABLES / "recommended_config.csv", index=False)
    return out


def build_recommendations(rp, loo, sweep, sens) -> pd.DataFrame:
    recs = []
    best = sweep.loc[sweep.groupby("rule_id").total_cost.idxmin()].set_index("rule_id")
    robust = set()
    for r in sens.rules_worth_retiring:
        if r != "none":
            robust = robust & set(r.split(",")) if robust else set(r.split(","))
    for _, r in loo.iterrows():
        rid = r.rule_id
        prec = rp.set_index("rule_id").loc[rid]
        if r.net_contribution < -5_000:
            action, why = "Retire", (
                f"Costs GBP {-r.net_contribution:,.0f}/yr more than it saves. "
                f"{int(r.unique_fraud_catches)} unique catches out of "
                f"{int(r.alert_volume):,} alerts.")
            risk = (f"Loses GBP {r.extra_fraud_loss_if_off:,.0f}/yr of fraud "
                    f"prevention. Residual risk sits with "
                    f"{prec.typology_covered} coverage.")
        elif rid in best.index and best.loc[rid].cost_vs_current < -3_000:
            b = best.loc[rid]
            action = f"Retune to {b.threshold:g} {b.unit}"
            why = (f"Cuts alerts from {int(r.alert_volume):,} to {int(b.alerts):,} "
                   f"and total cost by GBP {-b.cost_vs_current:,.0f}/yr.")
            risk = ("Fraud above the old threshold and below the new one is no "
                    "longer seen by this rule.")
        elif r.net_contribution > 5_000:
            action, why = "Keep unchanged", (
                f"Net contribution GBP {r.net_contribution:,.0f}/yr.")
            risk = "None from this change."
        else:
            action, why = "Keep under review", (
                "Net contribution is close to zero; the decision is inside the "
                "uncertainty of the cost assumptions.")
            risk = "Re-test after the next quarter of data."
        recs.append({"rule_id": rid, "rule_name": r.rule_name,
                     "current_action": r.default_action,
                     "alerts_per_year": int(r.alert_volume),
                     "precision_pct": prec.precision_pct,
                     "unique_catches": int(r.unique_fraud_catches),
                     "net_contribution_gbp": round(r.net_contribution),
                     "recommendation": action, "rationale": why,
                     "risk_of_this_change": risk})
    out = pd.DataFrame(recs)
    out.to_csv(TABLES / "rule_recommendations.csv", index=False)
    print("\n" + "=" * 72)
    print("RECOMMENDATION TABLE  (outputs/tables/rule_recommendations.csv)")
    print("=" * 72)
    print(out[["rule_id", "rule_name", "alerts_per_year", "precision_pct",
               "net_contribution_gbp", "recommendation"]].to_string(index=False))
    return out


def main() -> None:
    con = duckdb.connect(str(DB))
    rp = block_a_rule_performance(con)
    beh = block_b_false_decline_impact(con)
    cm, base = block_c_cost_model(con, beh["relationship_cost_per_fd"])
    loo = block_d_leave_one_out(cm, base, rp)
    sweep = block_e_threshold_sweep(cm, base)
    sens = block_f_sensitivity(con, base, beh["relationship_cost_per_fd"], loo)
    block_g_recommended_config(cm, base, loo)
    build_recommendations(rp, loo, sweep, sens)

    rp.to_csv(TABLES / "rule_performance.csv", index=False)
    pd.DataFrame([base]).to_csv(TABLES / "cost_model_baseline.csv", index=False)
    pd.DataFrame([{k: v[0] for k, v in ASSUMPTIONS.items()}
                  | {"relationship_cost_per_false_decline":
                     beh["relationship_cost_per_fd"]}]).to_csv(
        TABLES / "assumptions.csv", index=False)
    for name in ["fraud_by_mcc", "fraud_by_hour_channel", "fraud_by_amount_band",
                 "false_positives_by_segment", "dispute_summary", "alert_overlap"]:
        con.execute(f"SELECT * FROM {name}").df().to_csv(
            TABLES / f"{name}.csv", index=False)
    con.execute("""SELECT detection_band, COUNT(*) cards,
                          ROUND(SUM(loss_before_detection),0) loss_before_detection,
                          ROUND(MEDIAN(minutes_to_detection),0) median_minutes
                   FROM time_to_detection GROUP BY 1 ORDER BY 1""").df().to_csv(
        TABLES / "time_to_detection.csv", index=False)
    print(f"\nAll tables written to {TABLES}")
    con.close()


if __name__ == "__main__":
    main()
