# Reconciliation

Rule 1 of the quality bar: *if your figures do not tie back to a published
total or a source-system control count, you have not finished.*

## Control counts

| Check | Expected | Model | Status |
|---|---:|---:|---|
| Transactions loaded from `data/raw` | 2,412,960 | 2,412,960 | tie |
| One outcome row per transaction | 2,412,960 | 2,412,960 | tie |
| Alerts joining to a valid rule | 376,560 | 376,560 | tie |
| Disputes joining to a real transaction | 2,171 | 2,171 | tie |
| Declines = block fires + analyst declines | 102,599 | 102,599 | tie |
| Fraud outcome rows with no matching transaction | 0 | 0 | tie |

## Reconciliation against published industry figures

The generator was calibrated, not fitted. These checks decide whether the
synthetic layer is worth analysing at all.

| Measure | Published benchmark | Source | This dataset |
|---|---|---|---|
| Net card fraud loss | ~6.9 bps of card spend | UK Finance Annual Fraud Report: roughly GBP 550m of losses on roughly GBP 800bn of card spend | **6.9 bps** |
| Fraud prevention rate | ~72% of attempted fraud value stopped | UK Finance: roughly GBP 1.45bn prevented against GBP 550m lost | **73.6%** |
| Attempted fraud value | ~25 bps | prevented + realised, from the two rows above | **26.4 bps** |
| Fraud rate by transaction count | 0.1% to 1.5% | project brief | **0.200%** |
| First-party share of disputes | commonly quoted around a third | card scheme commentary | **30.0%** |
| Fraud-related chargeback rate | ~0.05% to 0.1% of transactions | issuer-side scheme monitoring norms | **0.09%** |

## What is NOT reconciled, and why

- **Absolute pound totals.** GBP 909,144 is a property of a
  14,000-customer synthetic portfolio, not of any real bank. Only the *rates*
  above tie to anything external, and only those should be quoted.
- **Service disputes** (goods not received, not as described) are out of scope,
  so the total dispute rate here is lower than a real issuer would report.
- **Rule-level precision** has no public benchmark. Issuers do not publish
  per-rule performance - which is exactly why this analysis is worth doing.

## Reproducibility

Every figure in the dashboard and the executive summary is written to
`outputs/tables/` as CSV by `src/03_analysis.py`. Random seeds are fixed
(20260822, 11, 4242, 909). Running the five scripts in order reproduces every
number in this repository.