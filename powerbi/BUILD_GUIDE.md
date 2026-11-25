# Build the Power BI report: Card Fraud and Disputes

Follow this in order. It replaces the older `README.md` in this folder, which has three errors (listed at the bottom). Allow a few hours. Everything you need is already prepared:

| File | What it is |
|---|---|
| `data/*.parquet`, `data/*.csv` | Slim star schema, import-ready |
| `data/expected_values.csv` | The headline numbers, computed independently. Your DAX must match these |
| `measures.dax` | Every measure, with comments |
| `theme_analystkit.json` | Colours that match the HTML dashboard |
| `export_powerbi_data.py` | Rebuilds `data/` from the warehouse if you ever need to |

All data in this project is synthetic. Say so on the report (step 6).

## 1. Load the data (Home > Get data)

1. **Parquet** (Get data > Parquet): `fct_transactions.parquet` (2.4m rows), `fct_alerts.parquet` (377k rows). Use Import mode, not DirectQuery.
2. **Text/CSV**: `dim_date`, `dim_customer`, `dim_merchant`, `dim_rule`, `fct_disputes`, `Assumptions`, `time_to_detection`, `scenario_results`.
3. In Power Query check the types: dates as Date, `txn_datetime` and `alert_datetime` as Date/Time, amounts as Fixed decimal or Decimal, flags (`is_fraud`, `is_declined`, `any_block_rule`, `any_review_rule`) as Whole number.
4. Close and apply.

## 2. Model (Model view)

Create these relationships. All many-to-one, single direction, cross-filter from the one side:

| From (many) | To (one) |
|---|---|
| `fct_transactions[txn_date]` | `dim_date[date]` |
| `fct_transactions[customer_id]` | `dim_customer[customer_id]` |
| `fct_transactions[merchant_id]` | `dim_merchant[merchant_id]` |
| `fct_alerts[transaction_id]` | `fct_transactions[transaction_id]` |
| `fct_alerts[rule_id]` | `dim_rule[rule_id]` |
| `fct_disputes[raised_date]` | `dim_date[date]` |
| `fct_disputes[customer_id]` | `dim_customer[customer_id]` |

Leave `Assumptions`, `time_to_detection` and `scenario_results` with no relationships.

**Do not turn on bi-directional filtering anywhere.** A transaction can fire several rules, and two-way filtering between `dim_rule` and `fct_alerts` would double-count.

Then:
- `dim_date`: right-click > Mark as date table, using `date`.
- Sort `dim_date[month_label]` by `month_sort`. Sort `dim_date[day_of_week]` by `dow_sort`.
- Hide every key column (ids) and `fct_alerts`/`fct_transactions` raw columns you will not drag into visuals.

## 3. Measures

Create a blank table `_Measures`, then paste the measures from `measures.dax` one at a time (Modeling > New measure; Power BI accepts one measure per box). Create the `Cost Components` table with Modeling > New table.

**Check before you continue.** Put the measures in a table visual and compare with `data/expected_values.csv`. They should match to the pound. If one does not, stop and fix it first:

| Measure | Expected |
|---|---|
| Transactions | 2,412,960 |
| Total Spend | £234,854,779 |
| Fraud Rate (bps) | 26.43 |
| Prevention Rate | 73.6% |
| False Declines | 99,518 |
| False Positives per Catch | 32.3 |
| Review Op Cost | £534,610 |
| Total Cost of Fraud | £907,714 |
| Reported Share of True Cost | 15.0% |

Rule table check (precision / recall / unique catches): R08 4.57% / 42.4% / 783, R02 4.44% / 24.7% / 173, R05 0.21% / 5.3% / 29.

## 4. Theme

View > Themes > Browse for themes > `theme_analystkit.json`. Use the Segoe UI font. Gridlines light, no chart borders other than the theme default.

## 5. Pages (set the page size to 16:9, 1280 x 720)

**Page 1: Executive**
- Five cards in a row: Total Cost of Fraud, Fraud Write Off, Fraud Rate (bps), False Declines, False Positives per Catch.
- 100% stacked bar: axis blank, legend `Cost Components[Component]`, value `[Component Cost]`. Title it with the finding: "Reported fraud is 15% of the real cost".
- Line chart: `dim_date[month_label]` (sorted by month_sort) against `[Total Cost of Fraud]`. Add a visual filter `dim_date[month_sort] <= 202512`, because only 50 transactions fall in January 2026 and they draw a misleading cliff.

**Page 2: Rule performance**
- Scatter: x `[Alert Volume]`, y `[Rule Precision]`, size `[Unique Catches]`, details `dim_rule[rule_name]`. **Use a log scale on both axes.** Alert volumes run from 133 to 124,702 and precision from 0% to 4.6%; on a linear axis six rules sit in one corner.
- Table: rule name, action, alert volume, precision, recall, unique catches.
- Bar: alerts per rule, sorted descending.

**Page 3: Fraud patterns**
- Matrix: rows `fct_transactions[txn_hour]`, columns `fct_transactions[channel]`, value `[Fraud Rate (bps)]`, with conditional formatting (background colour, one hue, light to dark).
- Bar: `dim_merchant[mcc_description]` by `[Fraud Rate (bps)]`, sorted descending.
- Column: `fct_transactions[amount_band]` by `[Fraud Rate (bps)]`.
- **Do not build a dual-axis chart of fraud rate against volume.** The apparent relationship changes completely with the axis ranges. Use two stacked charts that share an x-axis.

**Page 4: Customer impact**
- Clustered bar: `dim_customer[segment]` by `[False Decline Rate]`, legend `fct_transactions[channel]`.
- Text box (not a measure): "Customers who had a false decline spent 8.1% less over the next 60 days (95% CI -13.4% to -2.7%). A placebo test on an earlier date returned +11.3%, so treat this as indicative, not proven." This comes from the matched analysis in `src/03_analysis.py`; it cannot be rebuilt in DAX.
- Table: segment, false declines, false decline rate, False Positives per Catch.
- Optional: `[First Party Share]` and `[Avg Handling Cost]` cards for disputes (expected first-party share 30.0%).

**Page 5: Scenarios**
- Clustered column from `scenario_results`: axis `scenario`, values `total_cost_of_fraud`. Add a second visual with `realised_fraud_value` per scenario as the fraud-loss guardrail.
- Note under the chart: "Scenario figures come from the project's Python simulator, which re-simulates analyst decisions. They are not recalculated in DAX." (Real recalculation needs the threshold features, which are not in this model.)
- The simulator's baseline is £909,144 against the observed £907,714 on page 1, a 0.16% difference. Footnote it.

## 6. Finish

- Every page: small footer text "Synthetic data. Calibrated to UK Finance rates, not real bank data."
- Add alt text to every visual (Format > General > Alt text) and set a sensible tab order.
- File > Save as `card-fraud-disputes.pbix`. Do not commit it if it is large; check the size first.
- Take page screenshots (File > Export > PDF gives a clean set too). A 60 to 90 second screen recording walking through the five pages is worth more than anything else you can add.

## 7. Publishing

Do not assume you can embed a live report on your site. Publish to web may need a work or school Power BI licence, and published reports are public. Check what your account allows before you rely on it. The safe version: `.pbix` and screenshots in the GitHub repo, the video, and the existing HTML dashboard as the live link.

## Corrections to the older README.md

1. **Rule Recall** divided by "fraud caught". It must divide by all fraud transactions. R08 recall is 42.4%, not about 66%.
2. **Review Op Cost** counted every review-rule alert (286,945). It must count transactions that reached the review queue with no block rule (222,754) to reproduce the published £534,610.
3. **Fraud Write Off and Chargeback cost** hard-coded `0.169`, `0.86` and `42.87`. They now read from the `Assumptions` table so the constants have one home.
