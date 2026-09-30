"""
Project 1 - Step 4: charts, dashboard, executive summary.

CHART RULES APPLIED THROUGHOUT (they are not decoration, they change what people
conclude):
  - Never a dual axis. Two measures on different scales get two panels.
  - Categorical colours in fixed slot order, never cycled, never more than four.
  - Scatter and bubble charts use ONE hue plus direct labels, because a
    colourblind reader cannot separate eight hues in an all-pairs layout.
  - Every chart that carries a decision also ships as a CSV in outputs/tables.
  - Rates, never counts, wherever the denominator varies.

Run:  python src/04_report.py
"""
from __future__ import annotations

import sys
import duckdb
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analystkit import (PALETTE, INK, STATUS, SEQ_BLUE, apply_theme, save_fig,
                        fmt_gbp, Dashboard, Kpi)      # noqa: E402
from analystkit.theme import despine, DIVERGING       # noqa: E402

DB = ROOT / "data" / "processed" / "fraud.duckdb"
T = ROOT / "outputs" / "tables"
CH = ROOT / "outputs" / "charts"
CH.mkdir(parents=True, exist_ok=True)
apply_theme()

gbp = FuncFormatter(lambda v, _: f"{v/1000:,.0f}k" if abs(v) >= 1000 else f"{v:,.0f}")


def load(name):
    return pd.read_csv(T / f"{name}.csv")


# ---------------------------------------------------------------------------
def chart_cost_stack(comp: pd.DataFrame) -> Path:
    """What the board sees vs what it costs. One chart, whole argument."""
    fig, ax = plt.subplots(figsize=(8.6, 3.4))
    labels = ["What the board is\nshown each month",
              "What it actually\ncosts the bank"]
    reported = comp.loc[comp.component.str.startswith("Fraud written off"),
                        "annual_cost_gbp"].iloc[0]
    left = np.zeros(2)
    # Ordinal ramp: one hue, light to dark, starting no lighter than step 250.
    ramp = [SEQ_BLUE[3], SEQ_BLUE[5], SEQ_BLUE[7], SEQ_BLUE[9], SEQ_BLUE[11]]
    for i, (_, r) in enumerate(comp.iterrows()):
        vals = np.array([r.annual_cost_gbp if i == 0 else 0.0, r.annual_cost_gbp])
        ax.barh(labels, vals, left=left, color=ramp[i], height=0.52,
                label=r.component, edgecolor=INK["surface"], linewidth=2)
        for j in range(2):
            if vals[j] > 42_000:
                ax.text(left[j] + vals[j] / 2, j, f"{vals[j]/1000:,.0f}k",
                        ha="center", va="center", fontsize=9,
                        color="white", fontweight="600")
        left = left + vals
    total = comp.annual_cost_gbp.sum()
    ax.text(total * 1.01, 1, f"  {fmt_gbp(total)}", va="center",
            fontsize=11.5, fontweight="700", color=INK["primary"])
    ax.text(reported * 1.03, 0, f"  {fmt_gbp(reported)}", va="center",
            fontsize=11.5, fontweight="700", color=INK["primary"])
    ax.set_xlim(0, total * 1.22)
    ax.xaxis.set_major_formatter(gbp)
    ax.set_xlabel("Annual cost (GBP)")
    ax.set_title("The fraud loss figure is 15% of the cost of fraud")
    ax.grid(axis="y", visible=False)
    despine(ax, keep=("bottom",))
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, -0.62), ncol=2, fontsize=8.6)
    return save_fig(fig, CH / "01_cost_of_fraud.png",
                    "Twelve months. Components priced in src/03_analysis.py; "
                    "assumptions in outputs/tables/assumptions.csv.")


def chart_rule_map(rp: pd.DataFrame) -> Path:
    """Alert volume against precision. ONE hue plus direct labels: an all-pairs
    scatter cannot carry eight distinguishable colours for a CVD reader."""
    fig, ax = plt.subplots(figsize=(7.4, 5.0))
    x = rp.alert_volume.to_numpy()
    y = np.maximum(rp.precision_pct.to_numpy(), 0.03)
    size = 40 + rp.unique_fraud_catches.to_numpy() * 0.55
    ax.scatter(x, y, s=size, color=PALETTE[0], alpha=0.78,
               edgecolor=INK["surface"], linewidth=2, zorder=3)
    for _, r in rp.iterrows():
        ax.annotate(f"{r.rule_id}  {r.rule_name}",
                    (r.alert_volume, max(r.precision_pct, 0.03)),
                    textcoords="offset points", xytext=(11, -3),
                    fontsize=8.4, color=INK["secondary"])
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.axhline(1.0, color=INK["baseline"], lw=1, ls="--", zorder=1)
    ax.text(rp.alert_volume.min() * 0.9, 1.06, "1% precision",
            fontsize=8.2, color=INK["muted"])
    ax.set_xlabel("Alerts per year (log scale)")
    ax.set_ylabel("Precision, % of alerts that are fraud (log scale)")
    ax.set_title("Two rules generate 65% of the alert volume\n"
                 "and catch fraud once in every 250 alerts")
    ax.set_xlim(60, 500_000)
    despine(ax)
    return save_fig(fig, CH / "02_rule_map.png",
                    "Bubble size = unique catches, the fraud no other rule found. "
                    "Rules with zero catches are drawn at the axis floor.")


def chart_net_contribution(loo: pd.DataFrame) -> Path:
    """Diverging: two hues, neutral grey at zero, never a rainbow."""
    d = loo.sort_values("net_contribution")
    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    colours = [DIVERGING[5] if v < 0 else DIVERGING[1] for v in d.net_contribution]
    lbl = d.rule_id + "  " + d.rule_name.str.replace("_", " ").str.title()
    ax.barh(lbl, d.net_contribution, color=colours, height=0.62)
    for i, (v, r) in enumerate(zip(d.net_contribution, d.itertuples())):
        off = 5200 if v >= 0 else -5200
        ax.text(v + off, i, f"{v/1000:+,.0f}k", va="center",
                ha="left" if v >= 0 else "right", fontsize=9,
                color=INK["secondary"], fontweight="600")
    ax.axvline(0, color=INK["primary"], lw=1.1)
    ax.set_xlim(d.net_contribution.min() * 1.45, d.net_contribution.max() * 1.9)
    ax.xaxis.set_major_formatter(gbp)
    ax.set_xlabel("Net annual contribution (GBP). Negative = the rule costs more than it saves")
    ax.set_title("Four of eight rules destroy value")
    ax.grid(axis="y", visible=False)
    despine(ax, keep=("bottom",))
    return save_fig(fig, CH / "03_net_contribution.png",
                    "Leave-one-out: each rule switched off and the entire book "
                    "re-priced. Not an attribution formula.")


def chart_hourly(hc: pd.DataFrame) -> Path:
    """Two measures, two panels. NEVER a dual axis."""
    by_hour = (hc.groupby("txn_hour")
                 .agg(txns=("txns", "sum"), fraud=("fraud_txns", "sum")).reset_index())
    by_hour["share_of_volume"] = by_hour.txns / by_hour.txns.sum() * 100
    by_hour["fraud_rate"] = by_hour.fraud / by_hour.txns * 100

    fig, axes = plt.subplots(2, 1, figsize=(8.4, 5.0), sharex=True,
                             gridspec_kw={"hspace": 0.32})
    axes[0].bar(by_hour.txn_hour, by_hour.share_of_volume, color=PALETTE[0],
                width=0.72)
    axes[0].set_ylabel("% of all transactions")
    axes[0].set_title("Genuine spend is a daytime business")
    axes[1].bar(by_hour.txn_hour, by_hour.fraud_rate, color=PALETTE[1], width=0.72)
    axes[1].set_ylabel("% of transactions fraudulent")
    axes[1].set_xlabel("Hour of day")
    axes[1].set_title("Fraud is not")
    peak = by_hour.loc[by_hour.fraud_rate.idxmax()]
    axes[1].annotate(f"{peak.fraud_rate:.2f}% at {int(peak.txn_hour):02d}:00",
                     (peak.txn_hour, peak.fraud_rate),
                     textcoords="offset points", xytext=(8, -2),
                     fontsize=9, color=INK["secondary"], fontweight="600")
    for a in axes:
        a.set_xticks(range(0, 24, 2))
        despine(a)
    return save_fig(fig, CH / "04_hour_profile.png",
                    "Two panels rather than two y-axes. A dual-axis version of "
                    "this chart can be made to show any relationship you like.")


def chart_segment_fp(fp: pd.DataFrame) -> Path:
    piv = (fp.pivot_table(index="segment", columns="channel",
                          values="false_decline_rate_pct", aggfunc="mean")
             .fillna(0))
    order = ["card_present_chip", "contactless", "ecommerce", "manual_entry"]
    piv = piv[[c for c in order if c in piv.columns]]
    piv = piv.loc[piv.mean(axis=1).sort_values(ascending=False).index]
    fig, ax = plt.subplots(figsize=(8.2, 4.0))
    n = len(piv.columns)
    w = 0.78 / n
    idx = np.arange(len(piv))
    for i, c in enumerate(piv.columns):
        ax.bar(idx + i * w, piv[c], width=w - 0.02, color=PALETTE[i],
               label=c.replace("_", " "))
    ax.set_xticks(idx + 0.78 / 2 - w / 2)
    ax.set_xticklabels(piv.index)
    ax.set_ylabel("Genuine transactions declined (%)")
    ax.set_title("Who pays for the over-tuning")
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.13))
    ax.grid(axis="x", visible=False)
    despine(ax)
    return save_fig(fig, CH / "05_false_declines_by_segment.png",
                    "Four series, adjacent-pair validated. Full table in "
                    "outputs/tables/false_positives_by_segment.csv.")


def chart_thresholds(sweep: pd.DataFrame, baseline: float) -> Path:
    rules = list(sweep.rule_id.unique())
    fig, axes = plt.subplots(1, len(rules), figsize=(2.55 * len(rules), 3.3),
                             sharey=True)
    for ax, r in zip(np.atleast_1d(axes), rules):
        d = sweep[sweep.rule_id == r].sort_values("threshold")
        ax.plot(d.threshold, d.total_cost / 1000, color=PALETTE[0], lw=2)
        best = d.loc[d.total_cost.idxmin()]
        ax.scatter([best.threshold], [best.total_cost / 1000], s=70,
                   color=STATUS["good"], zorder=4, edgecolor=INK["surface"], lw=2)
        ax.axhline(baseline / 1000, color=INK["baseline"], ls="--", lw=1)
        ax.set_title(f"{r}\n{d.unit.iloc[0]}", fontsize=9.5)
        ax.set_xlabel("Threshold")
        despine(ax)
    np.atleast_1d(axes)[0].set_ylabel("Total cost of fraud (GBP 000)")
    fig.suptitle("Above-the-line testing: total cost at each threshold setting",
                 x=0.02, ha="left", fontsize=12.5, fontweight="600")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    return save_fig(fig, CH / "06_threshold_sweep.png",
                    "Green marker = lowest-cost setting. Dashed line = today. "
                    "Upward sweeps only; below-the-line testing needs the "
                    "non-alerting population (see Project 2).")


def chart_did(beh: dict) -> Path:
    labels = ["Naive:\ndeclined vs\nnever-declined",
              "Naive:\nbefore vs after,\nsame customers",
              "Matched DiD\n(raw)",
              "Matched DiD\n(placebo-corrected)",
              "TRUE effect\n(known only because\nthe data is synthetic)"]
    vals = [beh["naive_level_pct"], beh["naive_prepost_pct"],
            beh["pooled_effect_pct"], beh["adjusted_effect_pct"],
            beh["true_effect_pct"]]
    shown = [min(v, 60) for v in vals]
    cols = [STATUS["critical"], STATUS["warning"], PALETTE[0], PALETTE[0],
            INK["muted"]]
    fig, ax = plt.subplots(figsize=(8.4, 4.0))
    ax.bar(labels, shown, color=cols, width=0.58)
    for i, (v, s) in enumerate(zip(vals, shown)):
        txt = f"{v:+.1f}%" + (" (clipped)" if v != s else "")
        ax.text(i, s + (2.2 if s >= 0 else -2.2), txt, ha="center",
                va="bottom" if s >= 0 else "top", fontsize=9.5,
                fontweight="600", color=INK["secondary"])
    ax.axhline(0, color=INK["primary"], lw=1.1)
    ax.set_ylabel("Estimated change in 60-day spend after a false decline")
    ax.set_title("Same question, five answers. Only one design gets it right.")
    ax.set_ylim(-32, 74)
    ax.grid(axis="x", visible=False)
    despine(ax, keep=("bottom",))
    return save_fig(fig, CH / "07_did_estimates.png",
                    "The naive level comparison is clipped for legibility; its "
                    "actual value is +536%.")


def chart_detection(ttd: pd.DataFrame) -> Path:
    d = ttd.sort_values("detection_band")
    fig, ax = plt.subplots(figsize=(7.6, 3.8))
    ramp = [SEQ_BLUE[3], SEQ_BLUE[5], SEQ_BLUE[7], SEQ_BLUE[9], SEQ_BLUE[11],
            SEQ_BLUE[12]][:len(d)]
    ax.barh(d.detection_band, d.loss_before_detection, color=ramp, height=0.6)
    for i, (v, c) in enumerate(zip(d.loss_before_detection, d.cards)):
        ax.text(v * 1.02, i, f"{fmt_gbp(v)}   ({int(c):,} cards)",
                va="center", fontsize=9, color=INK["secondary"])
    ax.set_xlim(0, d.loss_before_detection.max() * 1.55)
    ax.xaxis.set_major_formatter(gbp)
    ax.set_xlabel("Fraud value that got through before the first block (GBP)")
    ax.set_title("Where the loss accumulates: the detection window")
    ax.grid(axis="y", visible=False)
    despine(ax, keep=("bottom",))
    return save_fig(fig, CH / "08_time_to_detection.png",
                    "Per compromised card, not per transaction. The question is "
                    "how long a compromised card keeps working.")


def chart_sensitivity(sens: pd.DataFrame) -> Path:
    fig, ax = plt.subplots(figsize=(7.4, 3.8))
    ax.plot(sens.relationship_cost_per_fd, sens.total_cost_of_fraud / 1000,
            color=PALETTE[0], marker="o", lw=2, markersize=7,
            markeredgecolor=INK["surface"], markeredgewidth=2)
    for _, r in sens.iterrows():
        if r.scenario in ("zero (fraud losses only)", "central estimate", "triple"):
            ax.annotate(r.scenario, (r.relationship_cost_per_fd,
                                     r.total_cost_of_fraud / 1000),
                        textcoords="offset points", xytext=(6, 9),
                        fontsize=8.6, color=INK["secondary"])
    ax.set_xlabel("Assumed relationship cost per false decline (GBP)")
    ax.set_ylabel("Total cost of fraud (GBP 000)")
    ax.set_title("The recommendation does not depend on the contested assumption")
    txt = ("Same four rules retire under every scenario,\n"
           "including one where a false decline costs nothing.")
    ax.text(0.03, 0.90, txt, transform=ax.transAxes, fontsize=9,
            color=INK["secondary"], va="top")
    despine(ax)
    return save_fig(fig, CH / "09_sensitivity.png",
                    "Sensitivity on the number a reviewer is most likely to "
                    "challenge. Full grid in outputs/tables/sensitivity_fp_cost.csv.")


def chart_mcc(mcc: pd.DataFrame) -> Path:
    d = mcc.nlargest(12, "fraud_bps").sort_values("fraud_bps")
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    ax.barh(d.mcc_description.str.slice(0, 34) + "  (" + d.mcc.astype(str) + ")",
            d.fraud_bps, color=PALETTE[0], height=0.62)
    for i, (v, val) in enumerate(zip(d.fraud_bps, d.fraud_value)):
        ax.text(v * 1.02, i, f"{v:,.0f} bps   ({fmt_gbp(val)})", va="center",
                fontsize=8.8, color=INK["secondary"])
    ax.set_xlim(0, d.fraud_bps.max() * 1.55)
    ax.set_xlabel("Fraud value as basis points of category spend")
    ax.set_title("Fraud rate by merchant category, as a rate not a count")
    ax.grid(axis="y", visible=False)
    despine(ax, keep=("bottom",))
    return save_fig(fig, CH / "10_fraud_by_mcc.png",
                    "Basis points is the industry expression. A raw count would "
                    "just rank the categories by size.")



def chart_configs(cfg: pd.DataFrame) -> Path:
    """The trade-off, drawn. Cost on one axis, the thing the fraud director
    actually cares about on the other."""
    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    nice = {"current": "Today",
            "retire_four": "Retire the four\nvalue-destroying rules",
            "retune_unconstrained": "Retune to the\ncost minimum",
            "retune_constrained": "RECOMMENDED\nretune within a 20%\nfraud-loss tolerance"}
    tone = {"current": INK["muted"], "retire_four": STATUS["critical"],
            "retune_unconstrained": STATUS["warning"],
            "retune_constrained": STATUS["good"]}
    for _, r in cfg.iterrows():
        ax.scatter(r.total_cost_of_fraud / 1000, r.realised_fraud_value / 1000,
                   s=170, color=tone.get(r.scenario, PALETTE[0]), zorder=3,
                   edgecolor=INK["surface"], linewidth=2.5)
        ax.annotate(nice.get(r.scenario, r.scenario),
                    (r.total_cost_of_fraud / 1000, r.realised_fraud_value / 1000),
                    textcoords="offset points", xytext=(11, 6), fontsize=8.8,
                    color=INK["secondary"])
    cur = cfg[cfg.scenario == "current"].iloc[0]
    ax.axhline(cur.realised_fraud_value / 1000 * 1.2, color=INK["baseline"],
               ls="--", lw=1.2)
    ax.text(cfg.total_cost_of_fraud.min() / 1000 * 0.99,
            cur.realised_fraud_value / 1000 * 1.22,
            "fraud-loss tolerance: today + 20%", fontsize=8.6, color=INK["muted"])
    ax.set_xlabel("Total cost of fraud (GBP 000)")
    ax.set_ylabel("Net fraud loss (GBP 000)")
    ax.set_title("The cheapest configuration is not the acceptable one")
    ax.set_xlim(cfg.total_cost_of_fraud.min() / 1000 * 0.90,
                cfg.total_cost_of_fraud.max() / 1000 * 1.14)
    ax.set_ylim(0, cfg.realised_fraud_value.max() / 1000 * 1.22)
    despine(ax)
    return save_fig(fig, CH / "11_configurations.png",
                    "Four rule-set configurations, each priced with the same "
                    "cost model. Lower-left is better on both axes.")


# ---------------------------------------------------------------------------
def main() -> None:
    con = duckdb.connect(str(DB))
    comp = load("cost_of_fraud_components")
    rp = load("rule_performance")
    loo = load("rule_leave_one_out")
    sweep = load("threshold_sweep")
    sens = load("sensitivity_fp_cost")
    fp = load("false_positives_by_segment")
    mcc = load("fraud_by_mcc")
    hc = load("fraud_by_hour_channel")
    ttd = load("time_to_detection")
    recs = load("rule_recommendations")
    base = load("cost_model_baseline").iloc[0]
    did = load("did_cohorts")
    placebo = load("did_placebo")
    disp = load("dispute_summary")
    cfg = load("recommended_config")

    beh = {
        "naive_level_pct": 536.35, "naive_prepost_pct": -10.05,
        "pooled_effect_pct": np.average(did.did_effect_pct, weights=did.n_matched),
        "adjusted_effect_pct": (np.average(did.did_effect_pct, weights=did.n_matched)
                                - np.average(placebo.did_effect_pct,
                                             weights=placebo.n_matched)),
        "true_effect_pct": -9.75,
    }

    print("Rendering charts ...")
    c1 = chart_cost_stack(comp)
    c2 = chart_rule_map(rp)
    c3 = chart_net_contribution(loo)
    c4 = chart_hourly(hc)
    c5 = chart_segment_fp(fp)
    c6 = chart_thresholds(sweep, base.total_cost_of_fraud)
    c7 = chart_did(beh)
    c8 = chart_detection(ttd)
    c9 = chart_sensitivity(sens)
    c10 = chart_mcc(mcc)
    c11 = chart_configs(cfg)

    # ---- headline numbers, every one read from the priced configurations --
    cur = cfg[cfg.scenario == "current"].iloc[0]
    recm = cfg[cfg.scenario.str.startswith("retune_constrained")].iloc[0]
    total = float(cur.total_cost_of_fraud)
    reported = comp.loc[comp.component.str.startswith("Fraud written off"),
                        "annual_cost_gbp"].iloc[0]
    saving = total - float(recm.total_cost_of_fraud)
    fd_removed = int(cur.false_declines - recm.false_declines)
    alerts_removed = int(cur.review_alerts - recm.review_alerts)
    hours_released = alerts_removed * 4.5 / 60
    fte = hours_released / 1600
    extra_fraud = float(recm.realised_fraud_value - cur.realised_fraud_value)
    fp_per_catch = con.execute("""
        SELECT ROUND(1.0 * COUNT(*) FILTER (WHERE is_fraud=0 AND is_declined=1)
               / NULLIF(COUNT(*) FILTER (WHERE is_fraud=1 AND is_declined=1),0), 0)
        FROM fct_outcomes""").fetchone()[0]

    print("\nHEADLINE NUMBERS")
    print(f"  total cost of fraud        {fmt_gbp(total)}")
    print(f"  reported as fraud loss     {fmt_gbp(reported)} "
          f"({reported/total*100:.0f}%)")
    print(f"  recommended saving         {fmt_gbp(saving)}")
    print(f"  false declines removed     {fd_removed:,}")
    print(f"  review alerts removed      {alerts_removed:,}")
    print(f"  investigator hours freed   {hours_released:,.0f}  ({fte:.1f} FTE)")
    print(f"  extra fraud loss accepted  {fmt_gbp(extra_fraud)}")
    print(f"  false positives per catch  {fp_per_catch:,.0f}")

    # ---- dashboard --------------------------------------------------------
    print("\nBuilding dashboard ...")
    d = Dashboard(
        "Card Fraud and Disputes Performance Review",
        "The true cost of getting detection wrong in both directions. "
        "2.4 million card transactions, eight detection rules, twelve months.",
        "Synthetic data, generated to the structure named in the project brief "
        "and calibrated to published UK Finance fraud rates. Every figure is "
        "reproducible from src/ and every assumption is listed in "
        "outputs/tables/assumptions.csv. Not real customer data.",
        eyebrow="Project 01 / Finance & Banking / Rule evaluation")

    p = d.page("Executive view")
    d.kpis(p, [
        Kpi("Total cost of fraud", fmt_gbp(total), "12 months, all components"),
        Kpi("Reported to the board", fmt_gbp(reported),
            f"{reported/total*100:.0f}% of the real cost", "critical"),
        Kpi("Net fraud loss", "7.0 bps",
            "vs ~6.9 bps published UK benchmark", "good"),
        Kpi("Genuine transactions declined", f"{int(cur.false_declines):,}",
            f"{fp_per_catch:,.0f} genuine stopped per fraud caught", "serious"),
        Kpi("Recommended saving", fmt_gbp(saving),
            "within a 20% fraud-loss tolerance", "good"),
    ])
    d.chart(p, c1, "Five cost components. Only the first is reported today.", wide=True)
    d.note(p, "The finding in one paragraph",
           "The fraud team reports gross fraud losses, which fell again this year. "
           f"Those losses are **{fmt_gbp(reported)}**. The full economic cost of the "
           f"detection engine is **{fmt_gbp(total)}**, because the engine also "
           f"declines **{int(cur.false_declines):,}** genuine transactions a year "
           f"and pushes **{int(cur.review_alerts):,}** alerts into a review queue. "
           "The single largest line is not fraud at all - it is the cost of "
           "reviewing alerts that are, in more than 99 cases out of 100, genuine "
           "customers "
           "trying to spend their own money.")
    d.chart(p, c3, "Leave-one-out net contribution per rule.")
    d.chart(p, c11, "The trade-off a fraud director actually has to sign.")
    d.chart(p, c9, "The conclusion survives every version of the contested assumption.")

    p = d.page("Rule performance")
    d.chart(p, c2, "Alert volume against precision. Bubble = unique catches.", wide=True)
    d.table(p, rp[["rule_id", "rule_name", "default_action", "alert_volume",
                   "true_positives", "precision_pct", "precision_ci_low_pct",
                   "precision_ci_high_pct", "recall_pct",
                   "false_positives_per_catch", "unique_fraud_catches"]]
            .round(3),
            "Precision with 95% Wilson intervals. R04 fired 133 times: its "
            "interval runs from 2.1% to 9.5%, which is why it gets an interval "
            "and not a point estimate.")
    d.chart(p, c6, "", wide=True)
    d.note(p, "The metric that decides retirement is not precision",
           "It is **unique catches** - the fraud a rule finds that no other rule "
           "found. R01 HIGH_VALUE_ECOM has respectable-looking precision and 77 "
           "catches a year, and its unique catch count is **zero**. Every single "
           "fraud it stops is also caught by at least one other rule, so switching "
           "it off loses no detection at all "
           "and removes 11,669 alerts.\n\n"
           "R07 ROUND_AMOUNT_CROSS_BORDER produced 483 alerts and no confirmed "
           "fraud at all in twelve months. The honest way to report that is not "
           "'precision 0%' but 'the 95% upper bound on its precision is 0.79%'.")

    p = d.page("Customer impact")
    d.chart(p, c5, "")
    d.chart(p, c7, "")
    d.note(p, "Why the causal design mattered more than the arithmetic",
           "The obvious comparison - declined customers against never-declined "
           "customers - says a false decline **increases** spend by 536%. It does "
           "not. Customers get declined because they are active, and comparing "
           "them against inactive customers measures activity, not the decline.\n\n"
           "The design that works compares customers declined this month against "
           "customers who will be declined three months from now, matched on "
           "segment and CRM spend band, over identical calendar weeks. That "
           "returns **-8.1%** (95% CI -13.4% to -2.7%).\n\n"
           "A placebo test on an event 90 days earlier returns **+11.3%** rather "
           "than zero, so some upward bias remains. The reported answer is "
           "therefore a range, -8.1% to -19.4%, and the cost model uses the "
           "conservative end. The true injected value was -9.75%, inside the range.")
    d.table(p, fp.head(20), "False decline rate by segment and channel.")

    p = d.page("Fraud patterns")
    d.chart(p, c10, "", wide=True)
    d.chart(p, c4, "")
    d.chart(p, c8, "")
    d.table(p, disp, "Disputes split by class. First-party misuse is a third of "
                     "case volume and is not a detection failure - it needs a "
                     "different intervention entirely.")

    p = d.page("Recommendations")
    d.kpis(p, [
        Kpi("Annual saving", fmt_gbp(saving), "net of the fraud given up", "good"),
        Kpi("Review alerts removed", f"{alerts_removed:,}", "per year"),
        Kpi("Investigator hours freed", f"{hours_released:,.0f}", f"{fte:.1f} FTE"),
        Kpi("False declines removed", f"{fd_removed:,}", "genuine customers"),
        Kpi("Extra fraud loss accepted", fmt_gbp(extra_fraud),
            "the risk this change carries", "warning"),
    ])
    d.table(p, recs, "Every recommendation carries the risk it creates. "
                     "A tuning paper without a risk column does not get signed off.",
            max_rows=10)
    d.note(p, "What this analysis cannot tell you",
           "- The rules were reconstructed from published fraud typologies, not "
           "read out of a production engine. A real engine has scores and "
           "sub-conditions this does not model.\n"
           "- Above-the-line testing only. Lowering a threshold requires "
           "re-scanning transactions that never alerted, which is done properly "
           "in Project 2.\n"
           "- The behavioural effect is measured over 60 days. Anything that "
           "shows up after that - attrition, reduced product take-up - is not "
           "captured and would make the false-decline cost larger, not smaller.\n"
           "- The analyst review outcome is modelled at a 78% catch rate and a "
           "6% false-decline rate. Real dispositions would replace it.")

    out = d.render(ROOT / "dashboard" / "index.html")
    print(f"  {out}")
    con.close()


if __name__ == "__main__":
    main()
