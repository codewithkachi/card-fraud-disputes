# Executive Summary
## Card Fraud and Disputes Performance Review

**To:** Director of Financial Crime  
**From:** Data Analysis  
**Period:** 12 months to 31 December 2025  
**Scope:** 2,412,960 card transactions, GBP 234.9m of spend, 8 detection rules, 14,000 customers

---

### 1. The problem with the number we report

Fraud performance reaches the Executive as a single figure: gross fraud loss.
That figure is **GBP 135,208** and it has fallen again this year.

It measures one of the two ways a detection engine can be wrong. The other way
- declining a genuine customer - costs the bank money in three separate places,
none of which appears in a fraud loss figure, and which together are five times
larger than the fraud losses.

### 2. What the engine actually costs

| Component | Annual cost | Share |
|---|---:|---:|
| Fraud written off (net of recovery) | GBP 135,208 | 15% |
| Chargeback handling and scheme fees | GBP 65,884 | 7% |
| False declines - lost margin | GBP 158,937 | 17% |
| False declines - relationship cost | GBP 14,506 | 2% |
| Review queue operating cost | GBP 534,610 | 59% |
| **Total cost of fraud** | **GBP 909,144** | **100%** |

The reported figure is **15%** of the real cost.

The single largest line is not fraud. It is **GBP 534,610** of
investigator time spent reviewing 222,754 alerts a year,
of which fewer than one in a hundred turns out to be fraudulent.

Separately, first-party misuse - customers disputing transactions they made
themselves - costs **GBP 77,487** a year and accounts for 30% of
dispute volume. No threshold change fixes it. It needs its own workstream.

### 3. Where the alert volume comes from

| Rule | Alerts/yr | Precision | 95% CI | Unique catches |
|---|---:|---:|---|---:|
| R05 HIGH_RISK_MCC_VALUE | 124,702 | 0.21% | 0.18-0.23% | 29 |
| R06 CNP_NEW_MERCHANT | 117,346 | 0.61% | 0.57-0.66% | 373 |
| R03 GEO_IMPOSSIBLE_3H_CP | 50,658 | 1.57% | 1.47-1.68% | 485 |
| R08 NOCTURNAL_BURST | 44,764 | 4.57% | 4.38-4.77% | 783 |
| R02 VELOCITY_5_IN_60MIN | 26,805 | 4.44% | 4.20-4.69% | 173 |
| R01 HIGH_VALUE_ECOM | 11,669 | 0.66% | 0.53-0.82% | 0 |
| R07 ROUND_AMOUNT_CROSS_BORDER | 483 | 0.00% | 0.00-0.79% | 0 |
| R04 DORMANT_REACTIVATION | 133 | 4.51% | 2.08-9.49% | 1 |

Two rules (R05, R06) generate **64% of all alerts** and together catch fraud
once in every 250 alerts. R01 has **zero unique catches** - everything it stops
is stopped by another rule too, so it contributes nothing but cost. R07 produced
no confirmed fraud at all in twelve months; on 483 alerts the 95% upper bound on
its true precision is 0.79%.

### 4. Recommendation

**retire R01; raise R05 300 -> 500; leave R02, R06, R08 unchanged**

| | Today | Recommended |
|---|---:|---:|
| Total cost of fraud | GBP 909,144 | GBP 791,625 |
| Net fraud loss | GBP 162,779 | GBP 195,006 |
| Fraud rate | 6.9 bps | 8.3 bps |
| Review alerts | 222,754 | 196,640 |
| Genuine transactions declined | 99,807 | 86,843 |

**Saving: GBP 117,520 a year**, plus 1,959 investigator hours
(1.2 FTE) and 12,964
fewer genuine customers declined.

**The risk: net fraud loss rises by GBP 32,226 (+20%).** That is inside the 20%
tolerance this analysis was constrained to, and takes the fraud rate from
6.9 to 8.3 basis points against a published industry
figure near 7.

### 5. What we did NOT recommend, and why

The cost model's unconstrained answer is to retire four rules. It saves
GBP 374,534. It also takes
net fraud loss from GBP 162,779 to GBP 444,466 - a fraud rate of about 19 basis points against an
industry norm near 7.

That is not a cost trade-off. Fraud loss is a board metric, a scheme-monitoring
metric, and past a certain point a regulatory conversation. A model that only
minimises total cost does not know any of that, and an analyst who presents its
answer unqualified will not be asked for a second one.

### 6. Confidence and challenge

The number most likely to be challenged is the behavioural cost of a false
decline. It was **measured, not assumed**. A matched comparison of customers
declined this month against customers who will be declined three months from now
returns a **-8.1% change in 60-day spend (95% CI -13.4% to -2.7%)**. A placebo
test on an earlier event returns +11.3% rather than zero, so residual upward bias
remains; the answer is therefore reported as a range and the model uses the
conservative end.

**The recommendation does not depend on it.** Under every scenario tested -
including one where a false decline is assumed to cost nothing beyond the lost
transaction - the same rules come out as value-destroying. Full sensitivity grid
in `outputs/tables/sensitivity_fp_cost.csv`.

### 7. Next steps

1. **Retire R01 now.** Zero unique catches means zero detection risk. Removes
   11,669 alerts a year.
2. **Run R05 at the new threshold in shadow for two weeks** before switching the
   action, and confirm the volume reduction matches the forecast.
3. **Retire R07 subject to a typology review.** It has caught nothing, but
   "we have a round-amount control" may be an answer someone gives an auditor.
   Confirm nothing depends on it before removal.
4. **Stand up a monthly rule scorecard**: alert volume, precision with an
   interval, and unique catches per rule. None of this analysis was possible
   before, because nobody was recording which rule fired.
5. **Open a separate workstream on first-party misuse.** 30% of dispute volume
   and entirely outside the scope of threshold tuning.

---

*Method, code and reproducible outputs: see README.md. Figures produced from
synthetic data built to the structure named in the project brief and calibrated
to published UK Finance fraud rates. Every assumption is listed in
`outputs/tables/assumptions.csv`.*