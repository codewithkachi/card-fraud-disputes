# Rebuilding this in Power BI

The live artefact in this repo is `dashboard/index.html`, because a portfolio needs
something a hiring manager can click without installing anything. This file is the spec
for rebuilding the same thing natively in Power BI Desktop.

## Data source

Point Power BI at `data/processed/fraud.duckdb` via the DuckDB ODBC driver, or import
the Parquet/CSV in `data/processed/` and `outputs/tables/` directly. Import mode, not
DirectQuery — the fact table is 2.4m rows and refreshes in seconds.

## Star schema

```
                 dim_date
                     |
 dim_customer --- fct_transactions --- dim_merchant
                     |         |
               dim_rule <-- fct_alerts
                     |
                fct_disputes
```

| Table | Grain | Key | Rows |
|---|---|---|---|
| `fct_transactions` | one card transaction | `transaction_id` | 2,412,960 |
| `fct_alerts` | one rule firing on one transaction | `alert_id` | 376,560 |
| `fct_disputes` | one chargeback case | `dispute_id` | 2,171 |
| `dim_customer` | one customer | `customer_id` | 14,000 |
| `dim_merchant` | one merchant | `merchant_id` | 2,400 |
| `dim_rule` | one detection rule | `rule_id` | 8 |
| `dim_date` | one day | `date` | 365 |

**Relationships:** all one-to-many from dimension to fact, single-direction filter.
Do **not** enable bi-directional filtering between `dim_rule` and `fct_alerts` — a
transaction can fire several rules, and bi-directional filtering there will
double-count transactions in every transaction-level measure.

`dim_date` must be marked as a date table (Modelling → Mark as date table) or the time
intelligence measures below return blanks.

## Core measures (DAX)

```dax
Transactions = COUNTROWS ( fct_transactions )

Total Spend = SUM ( fct_transactions[amount] )

Fraud Value =
CALCULATE ( [Total Spend], fct_transactions[is_fraud] = 1 )

-- Basis points is the industry expression. Never present a raw fraud count.
Fraud Rate (bps) = DIVIDE ( [Fraud Value], [Total Spend] ) * 10000

Prevented Fraud Value =
CALCULATE ( [Total Spend],
    fct_transactions[is_fraud] = 1, fct_transactions[is_declined] = 1 )

Realised Fraud Value =
CALCULATE ( [Total Spend],
    fct_transactions[is_fraud] = 1, fct_transactions[is_declined] = 0 )

Prevention Rate = DIVIDE ( [Prevented Fraud Value], [Fraud Value] )

False Declines =
CALCULATE ( [Transactions],
    fct_transactions[is_fraud] = 0, fct_transactions[is_declined] = 1 )

False Decline Value =
CALCULATE ( [Total Spend],
    fct_transactions[is_fraud] = 0, fct_transactions[is_declined] = 1 )

Fraud Caught =
CALCULATE ( [Transactions],
    fct_transactions[is_fraud] = 1, fct_transactions[is_declined] = 1 )

-- The number a fraud manager feels in their gut
False Positives per Catch = DIVIDE ( [False Declines], [Fraud Caught] )
```

### Rule-level measures

```dax
Alert Volume = COUNTROWS ( fct_alerts )

Alert True Positives =
CALCULATE ( [Alert Volume], fct_alerts[is_fraud] = 1 )

Rule Precision = DIVIDE ( [Alert True Positives], [Alert Volume] )

-- Recall must divide by ALL fraud, not the filtered fraud, so the rule filter
-- has to be removed from the denominator.
Rule Recall =
DIVIDE (
    [Alert True Positives],
    CALCULATE ( [Fraud Caught] + 0, ALL ( dim_rule ), fct_transactions[is_fraud] = 1 )
)

-- The metric that actually decides retirement: fraud only THIS rule saw.
Unique Catches =
CALCULATE (
    [Alert True Positives],
    FILTER ( VALUES ( fct_alerts[transaction_id] ),
             CALCULATE ( DISTINCTCOUNT ( fct_alerts[rule_id] ), ALL ( dim_rule ) ) = 1 )
)
```

### Cost model measures

The five constants live in a one-row disconnected table `Assumptions`, loaded from
`outputs/tables/assumptions.csv`, so they can be surfaced as what-if parameters.

```dax
Margin Rate      = MAX ( Assumptions[issuer_margin_rate] )
Retry Recovery   = MAX ( Assumptions[decline_retry_recovery] )
Review Cost      = MAX ( Assumptions[analyst_cost_per_hour] )
                 * MAX ( Assumptions[review_minutes_per_alert] ) / 60
Relationship Cost = MAX ( Assumptions[relationship_cost_per_false_decline] )

Fraud Write Off = [Realised Fraud Value] * ( 1 - 0.169 )

Chargeback Op Cost =
CALCULATE ( [Transactions],
    fct_transactions[is_fraud] = 1, fct_transactions[is_declined] = 0 )
    * 0.86 * 42.87

FD Lost Margin = [False Decline Value] * ( 1 - [Retry Recovery] ) * [Margin Rate]

FD Relationship Cost = [False Declines] * [Relationship Cost]

Review Op Cost =
CALCULATE ( [Alert Volume], dim_rule[default_action] = "review" ) * [Review Cost]

Total Cost of Fraud =
[Fraud Write Off] + [Chargeback Op Cost] + [FD Lost Margin]
    + [FD Relationship Cost] + [Review Op Cost]

-- The whole argument in one measure
Reported Share of True Cost = DIVIDE ( [Fraud Write Off], [Total Cost of Fraud] )
```

## Page layout

| Page | Visuals |
|---|---|
| **Executive** | 5 KPI cards (Total Cost of Fraud, Fraud Write Off, Fraud Rate bps, False Declines, False Positives per Catch) · 100% stacked bar of cost components · line chart of Total Cost of Fraud by month |
| **Rule performance** | Scatter: Alert Volume (x, log) vs Rule Precision (y, log), size = Unique Catches, one colour · table of all eight rules with precision and its interval · bar of alerts per rule |
| **Fraud patterns** | Matrix heatmap hour × channel on Fraud Rate (bps) · bar of Fraud Rate (bps) by MCC · histogram of amount band |
| **Customer impact** | Clustered bar of false-decline rate by segment × channel · table of affected customers · card for the measured behavioural effect |
| **Scenario** | What-if parameter on each threshold, driving a recalculated Total Cost of Fraud, with a fraud-loss guardrail line |

## Two things to get right

**Use a log scale on the rule scatter.** Alert volumes run from 133 to 124,702 and
precisions from 0% to 4.6%. On a linear axis six of the eight rules sit on top of each
other in one corner.

**Do not build a dual-axis chart of fraud rate against volume.** Power BI makes it easy
and it is the single most misleading chart in this domain — the apparent relationship
changes completely depending on where you set the two axis ranges. Use two stacked
charts sharing an x-axis, as `outputs/charts/04_hour_profile.png` does.
