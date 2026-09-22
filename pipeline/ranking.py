"""Step 8 - Expected-value ranking, with sensitivity to the margin assumption.

    Expected value = P(purchase within horizon)
                   x expected award value
                   x assumed gross margin
                   - assumed engagement cost

Input : data/processed/{account_scores,accounts,contracts,account_actions}.parquet
Output: data/processed/ev_ranking.parquet
        reports/ev_sensitivity.json
        reports/ev_sensitivity.png

Where each term comes from
--------------------------
  P(purchase)          survival model, or the rules baseline if the model did not
                       beat it - survival.py decides, this module obeys
  expected award value DERIVED from real award amounts (median of recent awards)
  gross margin         ASSUMPTION A-01, anchored to a public filing
  engagement cost      ASSUMPTION A-02, no public source, stated as such

Two of the four terms are assumptions, so the sensitivity analysis is part of the
deliverable rather than an appendix.

One thing to be clear about, because it is easy to oversell: margin is a positive
scalar and engagement cost is a constant subtraction, so NEITHER can change the
order of accounts. They only move how many clear zero. A "100% top-50 overlap
across the margin grid" is arithmetic, not evidence of robustness.

The choices that genuinely reorder the ranking are which probability source is
used and how expected award value is estimated, and those are tested separately
under `structural_sensitivity`.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assumptions as A  # noqa: E402
import config as C  # noqa: E402

P = C.PROCESSED
OUT = P / "ev_ranking.parquet"
SENS_JSON = C.REPORTS / "ev_sensitivity.json"
SENS_PNG = C.REPORTS / "ev_sensitivity.png"

MARGIN_GRID = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
RECENT_AWARDS_FOR_VALUE = 5


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def expected_award_value(con: pd.DataFrame) -> pd.Series:
    """Median of each account's most recent awards (A-04, DERIVED).

    Median rather than mean: federal award amounts are extremely heavy-tailed and
    a single multi-hundred-million-dollar instrument would otherwise set the
    expected value for the whole account.
    """
    d = con["base_obligation_date"].fillna(con["start_date"])
    k = (con.assign(_d=d)
         .dropna(subset=["_d", "account_id"])
         .sort_values("_d")
         .groupby("account_id")
         .tail(RECENT_AWARDS_FOR_VALUE))
    return k.groupby("account_id")["award_amount"].median().rename("expected_award_value")


def compute_ev(df: pd.DataFrame, margin: float, cost: float) -> pd.Series:
    return df["p_purchase"] * df["expected_award_value"] * margin - cost


def main() -> None:
    for n in ("account_scores.parquet", "accounts.parquet", "contracts.parquet"):
        if not (P / n).exists():
            raise SystemExit(f"missing {P / n} - run the earlier steps first")

    scores = pd.read_parquet(P / "account_scores.parquet")
    acc = pd.read_parquet(P / "accounts.parquet")
    con = pd.read_parquet(P / "contracts.parquet")
    actions = pd.read_parquet(P / "account_actions.parquet") \
        if (P / "account_actions.parquet").exists() else None

    reg = A.as_dict()
    margin = float(reg[A.MARGIN]["value"])
    cost = float(reg[A.ENGAGEMENT_COST]["value"])
    horizon = int(reg[A.HORIZON]["value"])
    method = scores["scoring_method"].iat[0] if len(scores) else "unknown"
    log(f"margin={margin:.1%} (A-01)  cost=${cost:,.0f} (A-02)  horizon={horizon}d (A-03)")
    log(f"probability source: {method}")

    df = (acc[["account_id", "account_name", "sub_agency", "department", "segment", "region",
               "size_band", "cohort_fy", "total_obligated", "award_count",
               "dormant", "months_since_last_award", "coverage_rate",
               "uncovered_assets", "expiring_contracts", "expiring_value",
               "whitespace_count", "channel_shift"]]
          .merge(scores[["account_id", "p_purchase_365d", "p_purchase_365d_model",
                         "p_purchase_365d_rules", "elapsed_days_since_last_award",
                         "scoring_method"]],
                 on="account_id", how="inner")
          .merge(expected_award_value(con), on="account_id", how="left"))
    df = df.rename(columns={"p_purchase_365d": "p_purchase"})
    df["expected_award_value"] = df["expected_award_value"].fillna(
        df["expected_award_value"].median())

    if actions is not None:
        df = df.merge(actions[["account_id", "primary_reason", "recommended_action",
                               "all_reason_codes", "reason_count", "evidence_rows",
                               "primary_evidence", "primary_provenance"]],
                      on="account_id", how="left")

    df["expected_value"] = compute_ev(df, margin, cost)
    df["gross_opportunity"] = df["p_purchase"] * df["expected_award_value"] * margin
    df = df.sort_values("expected_value", ascending=False).reset_index(drop=True)
    df["ev_rank"] = np.arange(1, len(df) + 1)
    df["ev_positive"] = df["expected_value"] > 0

    # ---------------- sensitivity to the margin assumption
    base_order = df.set_index("account_id")["expected_value"]
    sens = []
    top_n = min(50, max(10, len(df) // 10))
    base_top = set(df.head(top_n)["account_id"])
    for m in MARGIN_GRID:
        ev = compute_ev(df, m, cost)
        ranked = df.assign(ev=ev).sort_values("ev", ascending=False)
        top = set(ranked.head(top_n)["account_id"])
        sens.append({
            "margin": m,
            "positive_ev_accounts": int((ev > 0).sum()),
            "positive_ev_share": round(float((ev > 0).mean()), 4),
            "total_expected_value": float(ev.clip(lower=0).sum()),
            "median_expected_value": float(ev.median()),
            "spearman_vs_base": round(float(
                pd.Series(ev.to_numpy(), index=df["account_id"]).rank()
                .corr(base_order.rank(), method="pearson")), 4),
            f"top{top_n}_overlap_with_base": round(len(top & base_top) / max(top_n, 1), 4),
        })
        log(f"  margin {m:.0%}: {sens[-1]['positive_ev_accounts']:>5,} positive-EV accounts, "
            f"top{top_n} overlap {sens[-1][f'top{top_n}_overlap_with_base']:.0%}")

    # The engagement-cost assumption decides the cut-off, so show it too.
    cost_grid = [4000, 8000, 12000, 20000, 40000]
    cost_sens = [{"engagement_cost": c,
                  "positive_ev_accounts": int((compute_ev(df, margin, c) > 0).sum()),
                  "positive_ev_share": round(float((compute_ev(df, margin, c) > 0).mean()), 4)}
                 for c in cost_grid]

    # ---------------- the sensitivities that can actually REORDER the ranking
    #
    # Margin is a positive scalar and engagement cost is a constant subtraction,
    # so neither can change the ORDER of EV - only how many accounts clear zero.
    # Reporting "100% top-N overlap across margins" as evidence of stability would
    # be circular. The choices that genuinely move the ordering are which
    # probability source is used and how expected award value is estimated, so
    # those are the ones worth testing.
    def overlap(alt_ev: pd.Series) -> float:
        alt_top = set(df.assign(e=alt_ev).sort_values("e", ascending=False)
                      .head(top_n)["account_id"])
        return round(len(alt_top & base_top) / max(top_n, 1), 4)

    structural = []
    if "p_purchase_365d_rules" in df.columns:
        alt = df["p_purchase_365d_rules"] * df["expected_award_value"] * margin - cost
        structural.append({
            "variant": "probability from the rules baseline instead of the survival model",
            "can_reorder": True,
            f"top{top_n}_overlap_with_base": overlap(alt),
            "positive_ev_accounts": int((alt > 0).sum()),
            "rank_correlation": round(float(
                df["p_purchase"].rank().corr(df["p_purchase_365d_rules"].rank())), 4),
        })
    mean_val = (con.assign(_d=con["base_obligation_date"].fillna(con["start_date"]))
                .dropna(subset=["_d", "account_id"]).sort_values("_d")
                .groupby("account_id").tail(RECENT_AWARDS_FOR_VALUE)
                .groupby("account_id")["award_amount"].mean())
    alt_v = df["account_id"].map(mean_val).fillna(df["expected_award_value"])
    alt = df["p_purchase"] * alt_v * margin - cost
    structural.append({
        "variant": "expected award value as the MEAN of recent awards instead of the median",
        "can_reorder": True,
        f"top{top_n}_overlap_with_base": overlap(alt),
        "positive_ev_accounts": int((alt > 0).sum()),
        "note": ("Federal award amounts are heavy-tailed; the mean lets one large "
                 "instrument set an account's whole expected value. The gap between "
                 "these two rankings is the cost of that choice."),
    })
    for s_ in structural:
        log(f"  {s_['variant'][:58]:<58} top{top_n} overlap "
            f"{s_[f'top{top_n}_overlap_with_base']:.0%}")

    # ---------------- plot
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2), dpi=150)
    s = pd.DataFrame(sens)
    ax[0].plot(s["margin"], s["positive_ev_share"] * 100, "o-", color="#1f4e79", lw=1.8, ms=5)
    ax[0].axvline(margin, ls="--", lw=1, color="#b8860b")
    ax[0].annotate(f"A-01 = {margin:.0%}", (margin, ax[0].get_ylim()[1]),
                   textcoords="offset points", xytext=(4, -12), fontsize=8, color="#b8860b")
    ax[0].set_xlabel("assumed gross margin")
    ax[0].set_ylabel("% of accounts with positive EV")
    ax[0].set_title("How many accounts clear the cost floor", fontsize=10)
    ax[0].grid(alpha=.25, lw=.6)

    ax[1].plot(s["margin"], s[f"top{top_n}_overlap_with_base"] * 100, "o-",
               color="#1f4e79", lw=1.8, ms=5)
    ax[1].axvline(margin, ls="--", lw=1, color="#b8860b")
    ax[1].set_ylim(0, 105)
    ax[1].set_xlabel("assumed gross margin")
    ax[1].set_ylabel(f"% overlap with base top {top_n}")
    ax[1].set_title(f"Does the margin assumption reorder the top {top_n}?", fontsize=10)
    ax[1].grid(alpha=.25, lw=.6)
    fig.suptitle("Sensitivity of the expected-value ranking to assumption A-01", fontsize=11)
    fig.tight_layout()
    fig.savefig(SENS_PNG)
    plt.close(fig)

    df.to_parquet(OUT, index=False)

    rep = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "formula": "EV = P(purchase within horizon) x expected award value x margin - engagement cost",
        "terms": {
            "p_purchase": {"provenance": "DERIVED", "source": f"survival.py, using {method}"},
            "expected_award_value": {"provenance": "DERIVED",
                                     "source": f"median of each account's last "
                                               f"{RECENT_AWARDS_FOR_VALUE} real awards"},
            "gross_margin": {"provenance": "ASSUMPTION", "id": "A-01", "value": margin},
            "engagement_cost": {"provenance": "ASSUMPTION", "id": "A-02", "value": cost},
            "horizon_days": {"provenance": "ASSUMPTION", "id": "A-03", "value": horizon},
        },
        "accounts_ranked": int(len(df)),
        "positive_ev_accounts": int(df["ev_positive"].sum()),
        "total_expected_value_positive_only": float(
            df.loc[df["ev_positive"], "expected_value"].sum()),
        "margin_sensitivity": sens,
        "engagement_cost_sensitivity": cost_sens,
        "structural_sensitivity": structural,
        "why_margin_cannot_reorder": (
            "Expected value is P x V x margin - cost. Margin is a positive scalar "
            "and cost is a constant, so neither changes the ORDER of accounts - "
            "only how many clear zero. The 100% top-N overlap across the margin "
            "grid is arithmetic, not evidence of a robust ranking. The choices "
            "that do move the order are listed under structural_sensitivity."),
        "top_n_for_overlap": top_n,
        "reading": (
            "Two of the four terms are assumptions. The left panel shows how many "
            "accounts clear the cost floor as the margin assumption moves; the right "
            "panel shows whether the ordering itself is stable. If the ordering holds "
            "while the count moves, the ranking is usable for prioritisation even "
            "where the absolute values are not trusted."),
        "warning": ("Expected values are planning figures built on stated assumptions. "
                    "They are not forecasts of revenue and must not be presented as such."),
        "assumption_register": A.register(),
    }
    SENS_JSON.write_text(json.dumps(rep, indent=2, default=str))
    log(f"ranking -> {OUT} ({len(df):,} accounts, "
        f"{int(df['ev_positive'].sum()):,} with positive EV)")
    log(f"sensitivity -> {SENS_JSON}, {SENS_PNG.name}")


if __name__ == "__main__":
    main()
