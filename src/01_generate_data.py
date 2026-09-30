"""
Project 1 - Step 1: build the source data.

WHY THIS FILE EXISTS
--------------------
The brief names Sparkov / PaySim as the real sources. Those need a Kaggle
download. This script produces a dataset with the SAME table and column
structure the brief specifies, so every downstream file (SQL, analysis, charts)
runs unchanged against the real CSVs once you drop them in.

THE ONE RULE THAT MAKES OR BREAKS A SYNTHETIC FRAUD DATASET
-----------------------------------------------------------
The fraud process and the detection rules must be generated INDEPENDENTLY.

If I generate fraud by saying "fraud = any online transaction over GBP 750" and
then write a rule that says "flag any online transaction over GBP 750", the rule
scores 100% precision and 100% recall. The analysis is then circular and worth
nothing. Everyone who builds synthetic fraud data for the first time does this.

So instead: fraud is generated from BEHAVIOURAL TYPOLOGIES (a compromised card
gets a burst of activity, a skimmed card gets tested with a small amount first,
an account-takeover pushes value through a new payee). The rules in step 3 are
written separately, from published fraud-typology descriptions, and only
APPROXIMATE those behaviours. That gap is where precision and recall live, and
it is the entire subject of the project.

CALIBRATION TARGETS (all stated, all checkable)
-----------------------------------------------
  Fraud rate by count          ~0.20% of transactions
  Attempted fraud value        ~25 basis points of total spend
  Prevention rate              ~70% of attempted fraud value stopped
  Net loss                     ~7 bps  (UK Finance reports UK card fraud losses
                                        of roughly GBP 550m on roughly GBP 800bn
                                        of card spend, which is ~6.9 bps)
  Chargeback rate              ~0.6% of transactions raise a dispute
  First-party misuse           ~30% of disputes are the cardholder disputing
                               their own genuine purchase

Run:  python src/01_generate_data.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

RNG = np.random.default_rng(20260822)

N_CUSTOMERS = 14_000
N_MERCHANTS = 2_400
START = pd.Timestamp("2025-01-01")
DAYS = 365

# --------------------------------------------------------------------------
# Merchant category codes. Real MCCs, with the risk tiering that card schemes
# actually apply. Keeping real codes means a fraud manager recognises the output.
# --------------------------------------------------------------------------
MCCS = [
    # code, description, base share of spend, avg ticket, risk tier
    (5411, "Grocery Stores, Supermarkets",      0.170,  42.0, "low"),
    (5812, "Eating Places, Restaurants",        0.105,  31.0, "low"),
    (5541, "Service Stations",                  0.095,  55.0, "low"),
    (5912, "Drug Stores and Pharmacies",        0.055,  19.0, "low"),
    (5311, "Department Stores",                 0.060,  68.0, "medium"),
    (5691, "Mens and Womens Clothing Stores",   0.055,  74.0, "medium"),
    (5732, "Electronics Stores",                0.038, 285.0, "high"),
    (5944, "Jewellery, Watches, Silverware",    0.012, 640.0, "high"),
    (7995, "Betting, Casino Gambling",          0.020, 118.0, "high"),
    (5967, "Direct Marketing - Inbound Tele",   0.010, 156.0, "high"),
    (4816, "Computer Network Services",         0.030,  46.0, "medium"),
    (4900, "Utilities",                         0.060,  98.0, "low"),
    (5999, "Miscellaneous Retail",              0.070,  61.0, "medium"),
    (7011, "Lodging - Hotels, Motels",          0.030, 232.0, "medium"),
    (4511, "Airlines, Air Carriers",            0.022, 348.0, "high"),
    (5814, "Fast Food Restaurants",             0.085,  14.0, "low"),
    (7994, "Video Game Arcades / Digital Goods", 0.025,  27.0, "high"),
    (5045, "Computers, Peripherals, Software",  0.020, 412.0, "high"),
    (5651, "Family Clothing Stores",            0.038,  58.0, "medium"),
]
MCC_DF = pd.DataFrame(MCCS, columns=["mcc", "mcc_description", "share", "avg_ticket", "risk_tier"])
MCC_DF["share"] = MCC_DF["share"] / MCC_DF["share"].sum()

CHANNELS = ["card_present_chip", "contactless", "ecommerce", "manual_entry"]
CHANNEL_P = [0.30, 0.34, 0.33, 0.03]

SEGMENTS = ["Mass", "Mass Affluent", "Premier", "Student", "Basic"]
SEGMENT_P = [0.46, 0.22, 0.08, 0.12, 0.12]
SEGMENT_SPEND = {"Mass": 1.00, "Mass Affluent": 1.85, "Premier": 3.40,
                 "Student": 0.45, "Basic": 0.55}
REGIONS = ["North East", "North West", "Yorkshire", "Midlands", "East",
           "London", "South East", "South West", "Wales", "Scotland", "N Ireland"]
REGION_P = [0.045, 0.115, 0.085, 0.155, 0.105, 0.135, 0.145, 0.085, 0.05, 0.065, 0.015]


def build_customers() -> pd.DataFrame:
    seg = RNG.choice(SEGMENTS, N_CUSTOMERS, p=SEGMENT_P)
    df = pd.DataFrame({
        "customer_id": [f"C{i:06d}" for i in range(N_CUSTOMERS)],
        "card_id":     [f"K{i:06d}" for i in range(N_CUSTOMERS)],
        "segment":     seg,
        "tenure_months": RNG.integers(1, 240, N_CUSTOMERS),
        "region":      RNG.choice(REGIONS, N_CUSTOMERS, p=REGION_P),
        "age":         np.clip(RNG.normal(45, 16, N_CUSTOMERS), 18, 92).astype(int),
    })
    df["card_product"] = np.where(
        df.segment == "Premier", "World Elite",
        np.where(df.segment == "Mass Affluent", "Platinum",
                 np.where(df.segment == "Student", "Student Classic", "Classic")))
    # Monthly spend is lognormal - a small number of customers spend a lot.
    base = RNG.lognormal(mean=6.05, sigma=0.62, size=N_CUSTOMERS)
    df["average_monthly_spend"] = np.round(
        base * df.segment.map(SEGMENT_SPEND).to_numpy(), 2)
    return df


def build_merchants() -> pd.DataFrame:
    idx = RNG.choice(len(MCC_DF), N_MERCHANTS, p=MCC_DF["share"].to_numpy())
    picked = MCC_DF.iloc[idx].reset_index(drop=True)
    return pd.DataFrame({
        "merchant_id":   [f"M{i:05d}" for i in range(N_MERCHANTS)],
        "merchant_name": [f"{r.mcc_description.split(',')[0][:18].strip()} #{i:04d}"
                          for i, r in enumerate(picked.itertuples())],
        "mcc":              picked["mcc"],
        "mcc_description":  picked["mcc_description"],
        "risk_tier":        picked["risk_tier"],
        "avg_ticket":       picked["avg_ticket"],
        "country": RNG.choice(["GB", "IE", "US", "ES", "FR", "NL", "DE"],
                              N_MERCHANTS, p=[0.86, 0.02, 0.045, 0.025, 0.02, 0.02, 0.01]),
    })


def build_genuine_transactions(customers: pd.DataFrame,
                               merchants: pd.DataFrame) -> pd.DataFrame:
    """One row per genuine purchase.

    FOUR THINGS REAL CARD DATA DOES THAT NAIVE SYNTHETIC DATA DOES NOT
    ------------------------------------------------------------------
    The first version of this function drew every transaction independently at a
    uniformly random time, at a random merchant, for a lognormal amount. The rule
    evaluation immediately exposed it as fake, because four detection rules that
    fire constantly in production fired almost never here:

      1. SESSIONS. Real people buy petrol, then a coffee, then lunch inside an
         hour. Independent draws never produce that, so a velocity rule scored
         97% precision - which no velocity rule on earth achieves. Genuine bursts
         are the entire reason velocity rules have false positives.

      2. ROUND-NUMBER HEAPING. Real amounts pile up on multiples of 5, 10 and 50
         (topping up, cash-like purchases, set prices). A pure lognormal draw
         lands on an exact multiple of 50 essentially never, so a round-amount
         rule had 7 alerts in a year.

      3. TRAVEL. Country was being drawn per merchant, so customers hopped
         between countries all day and a geographic-impossibility rule fired
         100,000 times at 1.3% precision. Real customers sit in one country and
         occasionally take a trip.

      4. DORMANCY. Every customer here transacted constantly, so a
         dormant-reactivation rule had 1 alert. Real portfolios have a long tail
         of cards that sleep for months.

    Every one of those four is fixed below. This is the least glamorous work in
    the project and the part that decides whether the findings mean anything.
    """
    n_cust = len(customers)
    monthly_txns = np.clip(
        RNG.normal(customers.average_monthly_spend / 46.0, 5.5), 3, 130)
    n_txn_per_cust = RNG.poisson(monthly_txns * (DAYS / 30.0)).astype(int)

    # ---- (1) sessions ----------------------------------------------------
    # Group each customer's transactions into shopping sessions of 1-5, spread
    # over a couple of hours. Mean ~2.2 transactions per session.
    n_sessions = np.maximum(1, np.ceil(n_txn_per_cust / 2.2).astype(int))
    sess_cust = np.repeat(np.arange(n_cust), n_sessions)
    n_sess_total = len(sess_cust)
    sess_size = np.clip(1 + RNG.poisson(1.2, n_sess_total), 1, 5)

    sess_day = RNG.integers(0, DAYS, n_sess_total)
    bump = RNG.random(n_sess_total) < 0.10           # Friday/Saturday weighting
    sess_day = np.clip(np.where(bump, (sess_day // 7) * 7
                                + RNG.choice([4, 5], n_sess_total), sess_day),
                       0, DAYS - 1)
    peak = RNG.random(n_sess_total)
    sess_hour = np.where(peak < 0.42, RNG.normal(12.8, 2.0, n_sess_total),
                         np.where(peak < 0.92, RNG.normal(18.2, 2.2, n_sess_total),
                                  RNG.normal(2.4, 2.4, n_sess_total)))
    sess_hour = np.clip(sess_hour, 0, 23.4)
    sess_start_min = (sess_day * 1440.0 + sess_hour * 60.0
                      + RNG.integers(0, 60, n_sess_total))

    cust_idx = np.repeat(sess_cust, sess_size)
    total = len(cust_idx)
    # Offset within the session: exponential gaps, mean 22 minutes.
    within = RNG.exponential(22.0, total)
    # position of each transaction inside its own session (0 = first)
    seq = np.concatenate([np.arange(s) for s in sess_size])
    within = np.where(seq == 0, 0.0, within)
    grp = np.repeat(np.arange(n_sess_total), sess_size)
    offs = pd.Series(within).groupby(grp).cumsum().to_numpy()
    minute_of_year = np.repeat(sess_start_min, sess_size) + offs
    print(f"  genuine transactions: {total:,} in {n_sess_total:,} sessions")

    ts = START + pd.to_timedelta(minute_of_year, "m") \
               + pd.to_timedelta(RNG.integers(0, 60, total), "s")

    # ---- (4) dormancy ----------------------------------------------------
    # 12% of cards sleep for a stretch of 90-210 days. Implemented by deleting
    # every transaction inside that window, which is what dormancy actually is.
    dormant_cust = RNG.random(n_cust) < 0.12
    dorm_start = RNG.integers(20, DAYS - 100, n_cust)
    dorm_len = RNG.integers(90, 210, n_cust)
    doy = minute_of_year / 1440.0
    in_dormancy = (dormant_cust[cust_idx]
                   & (doy >= dorm_start[cust_idx])
                   & (doy < dorm_start[cust_idx] + dorm_len[cust_idx]))
    keep = ~in_dormancy
    cust_idx, ts, doy = cust_idx[keep], ts[keep], doy[keep]
    total = len(cust_idx)
    print(f"  after dormancy windows: {total:,}")

    # ---- merchant and amount --------------------------------------------
    m_idx = RNG.integers(0, len(merchants), total)
    avg_ticket = merchants.avg_ticket.to_numpy()[m_idx]
    cust_scale = (customers.average_monthly_spend.to_numpy()[cust_idx] / 900.0) ** 0.32
    amount = (RNG.lognormal(np.log(np.maximum(avg_ticket, 3)) - 0.18, 0.72)
              * cust_scale)
    amount = np.clip(amount, 0.75, 9500)

    # ---- (2) round-number heaping ---------------------------------------
    r = RNG.random(total)
    amount = np.where(r < 0.020, np.maximum(np.round(amount / 50.0) * 50.0, 50.0),
             np.where(r < 0.075, np.maximum(np.round(amount / 10.0) * 10.0, 10.0),
             np.where(r < 0.155, np.maximum(np.round(amount /  5.0) *  5.0,  5.0),
                      amount)))
    amount = np.round(amount, 2)

    channel = RNG.choice(CHANNELS, total, p=CHANNEL_P)
    # UK contactless is capped at GBP 100 per transaction.
    channel = np.where((channel == "contactless") & (amount > 100),
                       "card_present_chip", channel)
    mcc = merchants.mcc.to_numpy()[m_idx]
    channel = np.where(np.isin(mcc, [4816, 5967, 7994]), "ecommerce", channel)

    # ---- (3) travel -------------------------------------------------------
    # A customer is in GB unless they are on a trip. Card-present transactions
    # take the country the customer is standing in. E-commerce takes the
    # merchant's country, because card-not-present has no physical location -
    # which is exactly why a geographic-impossibility rule must exclude it.
    country = np.full(total, "GB", dtype=object)
    n_trips = RNG.poisson(0.9, n_cust)
    trip_cust, trip_start, trip_len, trip_country = [], [], [], []
    dests = np.array(["ES", "FR", "US", "IE", "NL", "DE", "IT", "PT", "GR", "TR"])
    for c in np.nonzero(n_trips > 0)[0]:
        for _ in range(int(n_trips[c])):
            trip_cust.append(c)
            trip_start.append(RNG.integers(0, DAYS - 12))
            trip_len.append(RNG.integers(3, 12))
            trip_country.append(RNG.choice(dests))
    trips = pd.DataFrame({"cust": trip_cust, "start": trip_start,
                          "len": trip_len, "country": trip_country})
    # Vectorised assignment, one pass per destination country.
    order = np.argsort(cust_idx, kind="stable")
    cust_sorted = cust_idx[order]
    doy_sorted = doy[order]
    for _, t in trips.iterrows():
        lo = np.searchsorted(cust_sorted, t.cust, "left")
        hi = np.searchsorted(cust_sorted, t.cust, "right")
        if hi <= lo:
            continue
        sl = slice(lo, hi)
        m = (doy_sorted[sl] >= t.start) & (doy_sorted[sl] < t.start + t.len)
        country[order[sl][m]] = t.country

    is_ecom = channel == "ecommerce"
    country = np.where(is_ecom, merchants.country.to_numpy()[m_idx], country)
    print(f"  transactions abroad: {(country != 'GB').mean()*100:.1f}% "
          f"({trips.shape[0]:,} trips)")

    return pd.DataFrame({
        "transaction_id": [f"T{i:09d}" for i in range(total)],
        "card_id":     customers.card_id.to_numpy()[cust_idx],
        "customer_id": customers.customer_id.to_numpy()[cust_idx],
        "transaction_datetime": ts,
        "amount": amount,
        "merchant_id": merchants.merchant_id.to_numpy()[m_idx],
        "merchant_category_code": mcc,
        "channel": channel,
        "country": country,
        "currency": "GBP",
        "is_fraud_process": False,
        "fraud_type": None,
    })


def build_fraud_transactions(customers: pd.DataFrame,
                             merchants: pd.DataFrame,
                             n_genuine: int) -> pd.DataFrame:
    """Fraud generated as BEHAVIOUR, not as a threshold.

    Four typologies, each with its own signature. Detection rules are written
    later and independently, so they catch these imperfectly - which is the
    point.

      card_testing      small probe, then escalating amounts, all online, fast
      counterfeit       card-present burst abroad or at high-risk MCCs
      cnp_takeover      account takeover: high-value ecommerce at new merchants
      lost_stolen       contactless run of small amounts in quick succession
    """
    target_fraud_txns = int(n_genuine * 0.0020 / (1 - 0.0020))
    typologies = ["card_testing", "counterfeit", "cnp_takeover", "lost_stolen"]
    typ_share = [0.26, 0.22, 0.30, 0.22]

    # Compromised cards skew towards higher-spend customers: fraudsters get more
    # value per compromised card, and issuers see this pattern in reality.
    w = (customers.average_monthly_spend.to_numpy() ** 0.55)
    w = w / w.sum()

    rows = []
    tid = 0
    made = 0
    while made < target_fraud_txns:
        c = RNG.choice(len(customers), p=w)
        typ = RNG.choice(typologies, p=typ_share)
        cust = customers.iloc[c]
        start_day = RNG.integers(0, DAYS - 3)
        # Fraud bursts are nocturnal far more often than genuine spend is.
        start_hour = int(np.clip(RNG.normal(1.8, 4.0), 0, 23))
        t0 = (START + pd.Timedelta(days=int(start_day))
              + pd.Timedelta(hours=start_hour)
              + pd.Timedelta(minutes=int(RNG.integers(0, 60))))

        if typ == "card_testing":
            n = int(RNG.integers(4, 12))
            gaps = RNG.exponential(3.5, n).cumsum()          # minutes
            amts = np.concatenate([[round(float(RNG.uniform(0.5, 3.0)), 2)],
                                   np.round(RNG.lognormal(3.90, 0.85, n - 1), 2)])
            pool = merchants[merchants.mcc.isin([4816, 7994, 5967, 5045])]
            chans = np.array(["ecommerce"] * n)
        elif typ == "counterfeit":
            n = int(RNG.integers(2, 7))
            gaps = RNG.exponential(55, n).cumsum()
            amts = np.round(RNG.lognormal(4.80, 0.75, n), 2)
            pool = merchants[merchants.risk_tier.isin(["high", "medium"])]
            chans = RNG.choice(["card_present_chip", "manual_entry"], n, p=[0.7, 0.3])
        elif typ == "cnp_takeover":
            n = int(RNG.integers(1, 5))
            gaps = RNG.exponential(140, n).cumsum()
            amts = np.round(RNG.lognormal(5.70, 0.70, n), 2)
            pool = merchants[merchants.mcc.isin([5732, 5944, 5045, 4511, 7011, 5311])]
            chans = np.array(["ecommerce"] * n)
        else:  # lost_stolen
            n = int(RNG.integers(3, 10))
            gaps = RNG.exponential(14, n).cumsum()
            amts = np.round(RNG.uniform(6, 99, n), 2)
            pool = merchants[merchants.risk_tier.isin(["low", "medium"])]
            chans = np.array(["contactless"] * n)

        if len(pool) == 0:
            continue
        m_idx = RNG.integers(0, len(pool), n)
        picked = pool.iloc[m_idx]
        for k in range(n):
            rows.append((
                f"F{tid:09d}", cust.card_id, cust.customer_id,
                t0 + pd.Timedelta(minutes=float(gaps[k])),
                float(np.clip(amts[k], 0.5, 6000)),
                picked.merchant_id.iloc[k], int(picked.mcc.iloc[k]),
                chans[k], picked.country.iloc[k], "GBP", True, typ))
            tid += 1
        made += n

    df = pd.DataFrame(rows, columns=[
        "transaction_id", "card_id", "customer_id", "transaction_datetime",
        "amount", "merchant_id", "merchant_category_code", "channel",
        "country", "currency", "is_fraud_process", "fraud_type"])
    # Counterfeit fraud is disproportionately cross-border.
    abroad = (df.fraud_type == "counterfeit") & (RNG.random(len(df)) < 0.55)
    df.loc[abroad, "country"] = RNG.choice(["US", "ES", "RO", "BG", "TR", "BR"],
                                           int(abroad.sum()))
    print(f"  fraud transactions:   {len(df):,}")
    return df


def main() -> None:
    print("Generating source data for Project 1 ...")
    customers = build_customers()
    merchants = build_merchants()
    gen = build_genuine_transactions(customers, merchants)
    fraud = build_fraud_transactions(customers, merchants, len(gen))

    txn = (pd.concat([gen, fraud], ignore_index=True)
             .sort_values("transaction_datetime")
             .reset_index(drop=True))
    txn["authorisation_result"] = "approved"     # step 3 overwrites this
    txn["decline_reason"] = None

    fraud_rate = txn.is_fraud_process.mean() * 100
    bps = txn.loc[txn.is_fraud_process, "amount"].sum() / txn.amount.sum() * 10_000
    print(f"  fraud rate by count:  {fraud_rate:.3f}%   (target ~0.20%)")
    print(f"  attempted fraud value: {bps:.1f} bps  (target ~25 bps)")

    # fraud_outcomes: one row per transaction the bank later CONFIRMED as fraud.
    # Confirmation lags the event - that lag is what the time-to-detection
    # analysis is about.
    f = txn[txn.is_fraud_process].copy()
    lag_hours = np.where(f.fraud_type == "card_testing", RNG.exponential(9, len(f)),
                         np.where(f.fraud_type == "lost_stolen", RNG.exponential(20, len(f)),
                                  RNG.exponential(52, len(f))))
    outcomes = pd.DataFrame({
        "transaction_id": f.transaction_id.to_numpy(),
        "is_fraud": 1,
        "fraud_type": f.fraud_type.to_numpy(),
        "confirmed_date": f.transaction_datetime.to_numpy()
                          + pd.to_timedelta(np.clip(lag_hours, 0.5, 720), "h"),
        "loss_amount": f.amount.to_numpy(),
        # Recovery from the acquirer succeeds sometimes; card-present recovers
        # least because liability usually sits with the issuer once chip is used.
        "recovered_amount": np.round(
            f.amount.to_numpy() * RNG.beta(1.2, 6.0, len(f)), 2),
    })

    # Genuine transactions confirmed as not-fraud (needed so the join is a real
    # left join, not an assumption that "missing means genuine").
    g = txn[~txn.is_fraud_process].sample(frac=0.02, random_state=7)
    outcomes = pd.concat([outcomes, pd.DataFrame({
        "transaction_id": g.transaction_id.to_numpy(), "is_fraud": 0,
        "fraud_type": None, "confirmed_date": g.transaction_datetime.to_numpy(),
        "loss_amount": 0.0, "recovered_amount": 0.0})], ignore_index=True)

    RAW.mkdir(parents=True, exist_ok=True)
    txn.drop(columns=["is_fraud_process", "fraud_type"]).to_parquet(
        RAW / "transactions.parquet", index=False)
    outcomes.to_parquet(RAW / "fraud_outcomes.parquet", index=False)
    customers.to_csv(RAW / "customers.csv", index=False)
    merchants.to_csv(RAW / "merchants.csv", index=False)

    print(f"\nWritten to {RAW}")
    print(f"  transactions.parquet   {len(txn):,} rows")
    print(f"  fraud_outcomes.parquet {len(outcomes):,} rows")
    print(f"  customers.csv          {len(customers):,} rows")
    print(f"  merchants.csv          {len(merchants):,} rows")


if __name__ == "__main__":
    main()
