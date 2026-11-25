# Card Fraud and Disputes Performance Review
### The true cost of getting detection wrong in both directions

**Sector:** Finance & Banking · **Level:** Intermediate / Mid-Level · **Modelling:** none

> A card issuer measured fraud performance by fraud losses alone. That metric ignores
> the cost of blocking genuine customers, so the detection engine had been tuned in one
> direction for years with nobody measuring the damage.

**[→ Open the live dashboard](https://onyekaogbonna.com/dashboards/card-fraud/)** · [case study](https://onyekaogbonna.com/projects/card-fraud-disputes.html)

---

## The headline

| | |
|---|---|
| What the board is shown each month | **£135k** gross fraud loss |
| What the detection engine actually costs | **£909k** |
| Genuine transactions declined per fraud caught | **32** |
| Recommended change | Retune four thresholds, retire one rule |
| Annual saving | **£118k** and 1.2 FTE of investigator time |
| Risk that change carries | **+£32k** of fraud loss, inside a stated 20% tolerance |

Net fraud loss lands at **7.0 basis points** of card spend, against a published UK
industry figure of roughly 6.9 bps. That reconciliation is in
[`outputs/reconciliation.md`](outputs/reconciliation.md).

---

## Three findings

**1. The reported number is 15% of the real number.**
Gross fraud loss is £135k. Add chargeback handling, the margin lost on genuine
transactions the engine declined, the behavioural cost of those declines, and the
operating cost of the review queue, and the engine costs £909k a year. The largest
single line is not fraud. It is **£535k of investigator time spent reviewing alerts
that are genuine in more than 99 cases out of 100.**

**2. Two rules produce 64% of the alert volume and catch fraud once in 250 alerts.**
`R05 HIGH_RISK_MCC_VALUE` fires 124,702 times a year at 0.21% precision. `R06
CNP_NEW_MERCHANT` fires 117,346 times at 0.61%. Separately, `R01 HIGH_VALUE_ECOM`
has **zero unique catches** — every fraud it stops is stopped by another rule too, so
it is pure cost. `R07 ROUND_AMOUNT_CROSS_BORDER` fired 483 times in twelve months and
caught nothing at all; the honest statement is not "0% precision" but "the 95% upper
bound on its precision is 0.79%".

**3. The cheapest rule set is not the acceptable one, and saying so is the job.**
Minimising total cost means retiring four rules — £375k saved, and net fraud loss
nearly triples. No fraud director signs that, because fraud loss is a board metric
and a scheme-monitoring metric, not just a cost line. Re-running the optimisation
**subject to fraud loss rising no more than 20%** gives a change that saves £118k,
releases 1.2 FTE and removes 12,964 false declines a year, while staying inside
tolerance.

---

## What is genuinely hard here, and how it was handled

### Measuring the behavioural cost of a false decline

The question is whether customers who are falsely declined reduce their card usage.
Three designs were tried. Two of them give the **wrong sign**.

| Design | Estimate | Verdict |
|---|---|---|
| Declined vs never-declined customers, same 60 days | **+536%** | Meaningless. Declined customers are declined *because* they are active; never-declined customers average £255/month against £694. |
| Declined customers, 60 days after vs 60 days before | **−10.0%** | Right by luck. The pre-period is artificially quiet because a first decline requires activity to trigger it. |
| Matched not-yet-treated difference-in-differences | **−8.1%** (95% CI −13.4 to −2.7) | The one to use. |

The matched design compares customers declined this month against customers who will
be declined three months from now — same population, same behaviour, only the timing
differs — matched on segment and CRM spend band and anchored to identical calendar
weeks.

**A placebo test on an event 90 days earlier returns +11.3% rather than zero**, so the
design still carries upward bias. The answer is therefore reported as a range, −8.1%
to −19.4%, and the cost model uses the conservative end.

Because the data is synthetic the true injected effect is known: **−9.75%**. It falls
inside the reported range. On real data you would never see that line; the placebo test
is the only thing that tells you which of the three estimates to trust.

The full diagnostic story, including two intermediate designs that failed and why, is
in the docstrings of [`src/03_analysis.py`](src/03_analysis.py).

### Making the synthetic data behave like real card data

The first version of the generator drew every transaction independently at a random
time. The rule evaluation immediately exposed it: a velocity rule scored **97%
precision**, which no velocity rule on earth achieves. Four things had to be added
before any finding meant anything:

| Missing | Consequence | Fix |
|---|---|---|
| Shopping **sessions** | velocity rule at 97% precision | transactions cluster 1–5 within ~2 hours |
| Round-number **heaping** | round-amount rule fired 7 times a year | 15.5% of amounts snap to multiples of 5/10/50 |
| **Travel** | geo rule fired 99,840 times at 1.3% precision | customers sit in GB and take occasional trips |
| **Dormancy** | dormant-card rule fired once a year | 12% of cards sleep for 90–210 days |

After the fix, velocity precision fell to 4.4% and geo volume halved — numbers a fraud
manager would recognise. This is the least glamorous work in the project and it decides
whether anything downstream is worth reading.

### One domain detail worth knowing

`R03` is restricted to **card-present** transactions. A card-not-present authorisation
carries the *merchant's* country, not the cardholder's, so a geographic-impossibility
rule applied to e-commerce generates enormous volume at almost no precision. That is a
real and common misconfiguration.

---

## How to run it

```bash
pip install -r requirements.txt
```

```bash
python src/01_generate_data.py && python src/02_build_warehouse.py && python src/02b_behaviour_panel.py && python src/03_analysis.py && python src/04_report.py
```

Roughly six minutes end to end, mostly the window functions over 2.4m rows.

| Step | File | What it does |
|---|---|---|
| 1 | `src/01_generate_data.py` | Builds the source tables. ~50s |
| 2 | `src/02_build_warehouse.py` | DuckDB model, applies the rules, builds disputes. ~4 min |
| 2b | `src/02b_behaviour_panel.py` | Daily spend panel with the modelled decline response. ~6s |
| 3 | `src/03_analysis.py` | Rule performance, DiD, cost model, sweeps, sensitivity. ~30s |
| 4 | `src/04_report.py` | Eleven charts and the dashboard. ~20s |

### The SQL

| File | Technique on show |
|---|---|
| [`sql/01_staging.sql`](sql/01_staging.sql) | `RANGE BETWEEN INTERVAL … PRECEDING` time windows, `LAG`, first-event detection with `ROW_NUMBER` |
| [`sql/02_rules.sql`](sql/02_rules.sql) | The eight rules, each mapped to a published typology |
| [`sql/03_rule_performance.sql`](sql/03_rule_performance.sql) | Per-rule confusion matrix and unique-catch attribution |
| [`sql/04_patterns.sql`](sql/04_patterns.sql) | Rate-based pattern analysis, time-to-detection per card |

---

## About the data — read this before judging the project

**The data is synthetic.** The public datasets Sparkov and PaySim require a Kaggle
download. This repo generates a dataset with the **same table and column structure those
datasets use**, so every SQL file and every analysis script runs unchanged once you
drop the real CSVs into `data/raw/`.

What that costs, stated plainly: I cannot claim to have analysed real card data, and
the absolute pound figures are properties of a generator, not of a bank.

What survives anyway: the rule-evaluation method, the two-sided cost model, the causal
design and its diagnostics, and the SQL. Those are the transferable parts, and they are
what the project is actually about.

**Calibration targets, all checkable in the script output:**

| Measure | Target | Achieved |
|---|---|---|
| Fraud rate by count | ~0.20% | 0.200% |
| Attempted fraud value | ~25 bps | 26.4 bps |
| Prevention rate | ~70% | 73.6% |
| Net fraud loss | ~7 bps (UK Finance) | 7.0 bps |
| First-party share of disputes | ~30% | 30% |

**Fraud is generated from behavioural typologies, not from the detection thresholds.**
If fraud were defined as "online over £750" and a rule tested "online over £750", every
rule would score 100% and the analysis would be circular. The typologies (card testing,
counterfeit, CNP takeover, lost/stolen) and the rules are written independently. The gap
between them is where precision and recall live.

---

## Limitations

- Rules were reconstructed from published typologies, not read out of a production
  engine. A real engine has scores and sub-conditions this does not model.
- **Above-the-line testing only.** Lowering a threshold requires re-scanning
  transactions that never alerted. A follow-up AML project tests thresholds in both directions.
- The behavioural effect is measured over 60 days. Attrition and reduced product
  take-up appear later and are not captured — which makes the false-decline cost an
  **under**-estimate, not an over-estimate.
- Analyst review is modelled at a 78% catch rate and a 6% false-decline rate. Real
  dispositions would replace it.
- First-party misuse (£77k) is reported separately and deliberately excluded from the
  detection cost model. It is a real cost, but no threshold change fixes it.

---

## Deliverables

| | |
|---|---|
| Live dashboard | [`dashboard/index.html`](dashboard/index.html) — 5 pages |
| Executive summary | [`outputs/executive_summary.md`](outputs/executive_summary.md) |
| Reconciliation | [`outputs/reconciliation.md`](outputs/reconciliation.md) |
| Rule recommendations | [`outputs/tables/rule_recommendations.csv`](outputs/tables/rule_recommendations.csv) |
| Assumptions register | [`outputs/tables/assumptions.csv`](outputs/tables/assumptions.csv) |
| Power BI report (5 pages, PDF, DAX and build guide) | [`powerbi/README.md`](powerbi/README.md) |
| Charts | `outputs/charts/` — 11 PNGs |
| Tables | `outputs/tables/` — 16 CSVs |

## Skills demonstrated

SQL window functions over 2.4m rows · DuckDB · confusion-matrix logic applied to rules
rather than models · Wilson confidence intervals on small denominators · two-sided cost
modelling · leave-one-out counterfactuals · above-the-line threshold sweeps ·
constrained optimisation under a business tolerance · matched difference-in-differences
with placebo testing · sensitivity analysis · synthetic data calibration against
published benchmarks
