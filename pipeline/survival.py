"""Step 7 - Time-to-next-award survival model, time-based validation, calibration.

Input : data/processed/contracts.parquet, accounts.parquet
Output: data/processed/account_scores.parquet
        reports/survival_report.json
        reports/survival_coefficients.csv
        reports/calibration.png
        reports/baseline_hazard.png

The target
---------
Time from one PURCHASE EPISODE to the next, for the same account. An episode is
an account-day on which awards were placed, not an individual award: contracting
offices sign dozens of delivery orders against one vehicle on a single day, and
counting each as an event makes the median inter-award gap 1 day - modelling
line-item batching rather than the repurchase cycle. Collapsing to account-days
gives an interval that means "how long until this office comes back to buy".

Accounts that have not bought again are RIGHT-CENSORED at the observation
boundary - they contribute "at least this long", not a zero. That is the whole
point of using a survival model instead of a binary label over an arbitrary
window.

Validation
----------
Strictly time-based. Nothing random.
  train : gaps that START on or before TRAIN_CUTOFF, censored AT TRAIN_CUTOFF, so
          the fit cannot see a single day beyond the cutoff.
  test  : accounts whose last episode is on or before TRAIN_CUTOFF. We predict
          P(next episode within HORIZON_DAYS after the cutoff), conditioned on
          the gap already elapsed, then check what actually happened.

          Reported twice: over all test accounts, and over the subset still
          plausibly in play (last purchase within 730 days). The full-population
          AUC is flattered by offices silent for years; the subset is the harder
          and more decision-relevant number.

Features that would NOT have been observable before the cutoff are listed in the
report under `excluded_unobservable` and are not used. Every feature is rebuilt
from awards dated on or before the as-of moment.

Honesty rule
------------
The model is compared against a rules baseline (recency alone). If it does not
beat that baseline on both discrimination and calibration, the report says so and
the ranking keeps the baseline. See README.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

from lifelines import CoxPHFitter, NelsonAalenFitter  # noqa: E402
from lifelines.utils import concordance_index  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)

P = C.PROCESSED
OUT_SCORES = P / "account_scores.parquet"
REPORT = C.REPORTS / "survival_report.json"
COEF_CSV = C.REPORTS / "survival_coefficients.csv"
CALIB_PNG = C.REPORTS / "calibration.png"
HAZARD_PNG = C.REPORTS / "baseline_hazard.png"

CUTOFF = pd.Timestamp(C.TRAIN_CUTOFF)
AS_OF = pd.Timestamp(C.AS_OF)
H = C.HORIZON_DAYS

# Features used by the model. All are computed from awards dated at or before the
# as-of moment, so each is observable at prediction time.
FEATURES = [
    "log_cum_obligated", "awards_to_date", "tenure_years", "mean_gap_days",
    "log_last_award_amount", "maint_share_to_date", "distinct_vendors_to_date",
    "distinct_psc_to_date", "hw_share_to_date", "is_defense",
]
# Deliberately excluded: they encode information from after the prediction moment.
EXCLUDED_UNOBSERVABLE = {
    "dormant": "computed against the as-of date, not the cutoff",
    "coverage_rate": "uses maintenance awards placed after the cutoff",
    "expansion_vs_cohort": "computed over the account's full history including the test window",
    "expiring_contracts / next_expiry": "derived from the as-of date",
    "months_since_last_award (as-of)": "recomputed at the cutoff instead",
    "health_score / telemetry / support cases": "SYNTHETIC; excluded from all modelling",
}


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


# ------------------------------------------------------------------ gaps
def award_events(con: pd.DataFrame) -> pd.DataFrame:
    """One row per PURCHASE EPISODE: an account-day on which it placed awards.

    Not one row per award. A contracting office routinely signs dozens of
    delivery orders against the same vehicle on the same day, and treating each
    as an event makes the median "time to next award" 1 day - which models
    line-item batching, not the repurchase cycle we actually want to predict.

    Collapsing to distinct account-days gives an interval with a meaning: how
    long until this office next comes back to buy.
    """
    d = con["base_obligation_date"].fillna(con["start_date"])
    ev = pd.DataFrame({
        "account_id": con["account_id"],
        "date": d.dt.normalize(),
        "amount": con["award_amount"].fillna(0.0),
        "is_maintenance": con["is_maintenance"].astype(int),
        "is_hardware": con["is_hardware"].astype(int),
        "vendor": con["recipient_entity_id"],
        "psc": con["psc_code"],
        "segment": con["segment"],
    }).dropna(subset=["account_id", "date"])

    ep = (ev.groupby(["account_id", "date"], as_index=False)
          .agg(amount=("amount", "sum"),
               is_maintenance=("is_maintenance", "max"),
               is_hardware=("is_hardware", "max"),
               vendor=("vendor", "first"),
               psc=("psc", "first"),
               segment=("segment", "first"),
               awards_that_day=("amount", "size")))
    return ep.sort_values(["account_id", "date"]).reset_index(drop=True)


def running_features(ev: pd.DataFrame) -> pd.DataFrame:
    """Cumulative, as-of-this-award features. No row sees its own future."""
    g = ev.groupby("account_id", sort=False)
    out = ev.copy()
    out["awards_to_date"] = g.cumcount() + 1
    out["cum_obligated"] = g["amount"].cumsum()
    out["cum_maint"] = g["is_maintenance"].cumsum()
    out["cum_hw"] = g["is_hardware"].cumsum()
    out["first_date"] = g["date"].transform("min")
    out["tenure_years"] = (out["date"] - out["first_date"]).dt.days / 365.25
    out["prev_date"] = g["date"].shift(1)
    out["gap_days"] = (out["date"] - out["prev_date"]).dt.days
    # Mean gap so far has a closed form: the gaps to date sum to
    # (this date - first date), over (awards to date - 1) of them. Exactly the
    # expanding mean, without the per-group apply.
    span = (out["date"] - out["first_date"]).dt.days
    out["mean_gap_days"] = np.where(out["awards_to_date"] > 1,
                                    span / (out["awards_to_date"] - 1).clip(lower=1),
                                    np.nan)
    out["log_last_award_amount"] = np.log10(out["amount"].clip(lower=1))
    out["log_cum_obligated"] = np.log10(out["cum_obligated"].clip(lower=1))
    out["maint_share_to_date"] = out["cum_maint"] / out["awards_to_date"]
    out["hw_share_to_date"] = out["cum_hw"] / out["awards_to_date"]

    # Expanding distinct counts, done per account so nothing leaks forward.
    for col, name in (("vendor", "distinct_vendors_to_date"), ("psc", "distinct_psc_to_date")):
        vals = []
        for _a, grp in out.groupby("account_id", sort=False):
            seen, run = set(), []
            for v in grp[col]:
                if pd.notna(v):
                    seen.add(v)
                run.append(len(seen))
            vals.extend(run)
        out[name] = vals

    out["is_defense"] = (out["segment"] == "Defense").astype(int)
    # First episode has no prior gap. Fill from rows at or before the training
    # cutoff only - a global median would leak the test period into the fit.
    fill = out.loc[out["date"] <= CUTOFF, "mean_gap_days"].median()
    out["mean_gap_days"] = out["mean_gap_days"].fillna(fill)
    return out


def build_gaps(ev: pd.DataFrame, boundary: pd.Timestamp) -> pd.DataFrame:
    """Gap dataset censored at `boundary`. Features are as of the gap's START."""
    e = ev[ev["date"] <= boundary].copy()
    if e.empty:
        return e
    g = e.groupby("account_id", sort=False)
    e["next_date"] = g["date"].shift(-1)
    e["event"] = e["next_date"].notna().astype(int)
    e["duration"] = np.where(
        e["event"] == 1,
        (e["next_date"] - e["date"]).dt.days,
        (boundary - e["date"]).dt.days)
    e = e[e["duration"] >= 0].copy()
    # A zero-length gap (two awards signed the same day) breaks the Cox partial
    # likelihood; nudge to half a day rather than dropping a real event.
    e["duration"] = e["duration"].clip(lower=0.5)
    return e


def features_asof(ev: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Per-account state at `asof`: last award, elapsed gap, and its features."""
    e = ev[ev["date"] <= asof]
    if e.empty:
        return pd.DataFrame()
    last = e.groupby("account_id").tail(1).set_index("account_id")
    last["elapsed_days"] = (asof - last["date"]).dt.days.clip(lower=0)
    return last


# ------------------------------------------------------------------ model
def conditional_purchase_prob(cph: CoxPHFitter, X: pd.DataFrame,
                              elapsed: np.ndarray, horizon: int) -> np.ndarray:
    """P(next award within `horizon` | already waited `elapsed`).

    Cox is proportional hazards, so the conditional survival is
        S(t0+h | t0) = S(t0+h)^exp(bx) / S(t0)^exp(bx)
    evaluated on the fitted baseline survival curve.
    """
    base = cph.baseline_survival_
    times = base.index.to_numpy(dtype=float)
    s0 = base.iloc[:, 0].to_numpy(dtype=float)

    def s0_at(t):
        idx = np.clip(np.searchsorted(times, t, side="right") - 1, 0, len(s0) - 1)
        return np.where(np.asarray(t) < times[0], 1.0, s0[idx])

    pr = np.exp(np.asarray(cph.predict_log_partial_hazard(X), dtype=float))
    s_t0 = np.power(np.clip(s0_at(elapsed), 1e-12, 1.0), pr)
    s_t1 = np.power(np.clip(s0_at(elapsed + horizon), 1e-12, 1.0), pr)
    return np.clip(1.0 - (s_t1 / np.clip(s_t0, 1e-12, None)), 0.0, 1.0)


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    df = pd.DataFrame({"y": y, "p": p})
    # Ranked deciles, so ties in p do not collapse the bins.
    df["decile"] = pd.qcut(df["p"].rank(method="first"), bins, labels=False) + 1
    t = df.groupby("decile").agg(n=("y", "size"), predicted=("p", "mean"),
                                 observed=("y", "mean")).reset_index()
    t["gap"] = t["observed"] - t["predicted"]
    return t


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Rank-based AUC without sklearn's import cost; handles ties."""
    y = np.asarray(y)
    n1, n0 = y.sum(), (1 - y).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = pd.Series(p).rank().to_numpy()
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def ece(t: pd.DataFrame) -> float:
    """Expected calibration error, weighted by bin size."""
    return float((t["n"] * (t["observed"] - t["predicted"]).abs()).sum() / t["n"].sum())


# ------------------------------------------------------------------ driver
def main() -> None:
    con = pd.read_parquet(P / "contracts.parquet")
    log(f"loaded {len(con):,} in-scope awards")
    ev_raw = award_events(con)
    ev = running_features(ev_raw)
    log(f"{len(ev):,} award events across {ev['account_id'].nunique():,} accounts")

    # ---------------- train on gaps that start at or before the cutoff
    train = build_gaps(ev, CUTOFF)
    train = train.dropna(subset=FEATURES)
    log(f"training gaps: {len(train):,} ({int(train['event'].sum()):,} events, "
        f"{int((1 - train['event']).sum()):,} right-censored) through {CUTOFF:%Y-%m-%d}")

    cph = CoxPHFitter(penalizer=0.1)
    cph.fit(train[FEATURES + ["duration", "event"]], duration_col="duration",
            event_col="event", robust=False)
    coefs = cph.summary[["coef", "exp(coef)", "se(coef)", "z", "p",
                         "coef lower 95%", "coef upper 95%"]].copy()
    coefs.to_csv(COEF_CSV)
    log("fitted Cox proportional hazards model")

    # in-sample concordance on the training gaps
    c_index_train = concordance_index(train["duration"],
                                      -cph.predict_partial_hazard(train[FEATURES]),
                                      train["event"])

    # ---------------- test: what actually happened after the cutoff
    state = features_asof(ev, CUTOFF)
    state = state.dropna(subset=FEATURES)
    after = ev[(ev["date"] > CUTOFF) & (ev["date"] <= CUTOFF + pd.Timedelta(days=H))]
    bought = set(after["account_id"].unique())
    y = state.index.isin(bought).astype(int)

    p_model = conditional_purchase_prob(cph, state[FEATURES],
                                        state["elapsed_days"].to_numpy(), H)

    # ---------------- rules baseline: recency alone, the thing a human would use
    # Shorter time since last award -> more likely to buy again. Converted to a
    # probability by the empirical rate within recency buckets, fitted on TRAIN data
    # only so the comparison is fair.
    tr_state = build_gaps(ev, CUTOFF)
    tr_state = tr_state[tr_state["duration"] > 0]
    edges = [0, 90, 180, 365, 730, 1095, 1e9]
    tr_bucket = pd.cut(tr_state["duration"], edges, labels=False, right=False)
    rate_by_bucket = (pd.DataFrame({"b": tr_bucket, "e": tr_state["event"]})
                      .groupby("b")["e"].mean())
    te_bucket = pd.cut(state["elapsed_days"], edges, labels=False, right=False)
    p_rules = te_bucket.map(rate_by_bucket).astype(float).fillna(
        float(rate_by_bucket.mean())).to_numpy()

    res = {}
    for name, p in (("survival_model", p_model), ("rules_baseline", p_rules)):
        t = calibration_table(y, p)
        res[name] = {"auc": auc(y, p), "brier": brier(y, p), "ece": ece(t),
                     "mean_predicted": float(np.mean(p)),
                     "observed_rate": float(np.mean(y)),
                     "calibration": t.round(4).to_dict(orient="records")}
        log(f"{name:<16} AUC={res[name]['auc']:.3f}  Brier={res[name]['brier']:.4f}  "
            f"ECE={res[name]['ece']:.4f}")

    # The full test set includes offices that stopped buying years before the
    # cutoff. They are free negatives and they flatter AUC. Re-score on the
    # subset that was still plausibly in play - last purchase within two years.
    recent = state["elapsed_days"].to_numpy() <= 730
    res["recently_active_subset"] = {
        "definition": "test accounts whose last purchase episode was within 730 days of the cutoff",
        "accounts": int(recent.sum()),
        "observed_rate": (round(float(np.mean(y[recent])), 4) if recent.sum() else None),
        "survival_model": {"auc": auc(y[recent], p_model[recent]),
                           "brier": brier(y[recent], p_model[recent])} if recent.sum() else {},
        "rules_baseline": {"auc": auc(y[recent], p_rules[recent]),
                           "brier": brier(y[recent], p_rules[recent])} if recent.sum() else {},
        "why": ("AUC over the full population is inflated by accounts that have "
                "been silent for years and are trivially predicted not to buy. "
                "This subset is the harder and more decision-relevant test."),
    }
    if recent.sum():
        log(f"recently-active subset ({int(recent.sum())} accounts): "
            f"model AUC={res['recently_active_subset']['survival_model']['auc']:.3f} "
            f"vs baseline {res['recently_active_subset']['rules_baseline']['auc']:.3f}")

    beats = (res["survival_model"]["auc"] > res["rules_baseline"]["auc"]
             and res["survival_model"]["brier"] < res["rules_baseline"]["brier"])
    chosen = "survival_model" if beats else "rules_baseline"
    log(f"model {'BEATS' if beats else 'DOES NOT BEAT'} the rules baseline "
        f"-> ranking uses {chosen}")

    # ---------------- plots
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4), dpi=150)
    for i, name in enumerate(("survival_model", "rules_baseline")):
        t = pd.DataFrame(res[name]["calibration"])
        ax[i].plot([0, max(t["predicted"].max(), t["observed"].max()) * 1.05],
                   [0, max(t["predicted"].max(), t["observed"].max()) * 1.05],
                   ls="--", lw=1, color="#8a8a8a", label="perfect")
        ax[i].plot(t["predicted"], t["observed"], "o-", color="#1f4e79" if i == 0 else "#b8860b",
                   lw=1.6, ms=5, label=name.replace("_", " "))
        ax[i].set_xlabel("predicted P(award within 365d)")
        ax[i].set_ylabel("observed rate")
        ax[i].set_title(f"{name.replace('_', ' ')}\nAUC {res[name]['auc']:.3f} | "
                        f"ECE {res[name]['ece']:.4f}", fontsize=10)
        ax[i].grid(alpha=.25, lw=.6)
        ax[i].legend(fontsize=8, frameon=False)
    fig.suptitle(f"Calibration by decile - trained through {CUTOFF:%Y-%m-%d}, "
                 f"evaluated on the {H} days after", fontsize=11)
    fig.tight_layout()
    fig.savefig(CALIB_PNG)
    plt.close(fig)

    naf = NelsonAalenFitter()
    naf.fit(train["duration"], train["event"])
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150)
    naf.plot_cumulative_hazard(ax=ax, color="#1f4e79", lw=1.6)
    ax.set_title("Baseline cumulative hazard of placing the next award", fontsize=11)
    ax.set_xlabel("days since previous award")
    ax.set_ylabel("cumulative hazard")
    ax.set_xlim(0, 1500)
    ax.grid(alpha=.25, lw=.6)
    fig.tight_layout()
    fig.savefig(HAZARD_PNG)
    plt.close(fig)

    # ---------------- score every account as of today, for the console
    live = features_asof(ev, AS_OF)
    live = live.dropna(subset=FEATURES)
    p_live_model = conditional_purchase_prob(cph, live[FEATURES],
                                             live["elapsed_days"].to_numpy(), H)
    live_bucket = pd.cut(live["elapsed_days"], edges, labels=False, right=False)
    p_live_rules = live_bucket.map(rate_by_bucket).astype(float).fillna(
        float(rate_by_bucket.mean())).to_numpy()

    scores = pd.DataFrame({
        "account_id": live.index,
        "p_purchase_365d_model": np.round(p_live_model, 6),
        "p_purchase_365d_rules": np.round(p_live_rules, 6),
        "elapsed_days_since_last_award": live["elapsed_days"].to_numpy(),
        "scoring_method": chosen,
    })
    scores["p_purchase_365d"] = (scores["p_purchase_365d_model"] if beats
                                 else scores["p_purchase_365d_rules"])
    scores.to_parquet(OUT_SCORES, index=False)

    med = float(np.median(train.loc[train["event"] == 1, "duration"]))
    rep = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "target": ("days from one PURCHASE EPISODE to the account's next, "
                   "right-censored. An episode is an account-day on which awards "
                   "were placed, not a single award: offices sign dozens of "
                   "delivery orders on one day, which would otherwise make the "
                   "median inter-award gap 1 day and model batching rather than "
                   "repurchase."),
        "censoring": {
            "train_censored_at": str(CUTOFF.date()),
            "train_gaps": int(len(train)),
            "events": int(train["event"].sum()),
            "right_censored": int((1 - train["event"]).sum()),
            "censoring_rate": round(float(1 - train["event"].mean()), 4),
        },
        "validation": {
            "scheme": "time-based; no random splits anywhere",
            "train_cutoff": str(CUTOFF.date()),
            "evaluation_window": [str((CUTOFF + pd.Timedelta(days=1)).date()),
                                  str((CUTOFF + pd.Timedelta(days=H)).date())],
            "test_accounts": int(len(state)),
            "test_positive_rate": round(float(np.mean(y)), 4),
            "excluded_unobservable": EXCLUDED_UNOBSERVABLE,
        },
        "features": FEATURES,
        "baseline_hazard": {
            "median_observed_gap_days": med,
            "note": "Nelson-Aalen cumulative hazard plotted in reports/baseline_hazard.png",
            "concordance_train": round(float(c_index_train), 4),
        },
        "coefficients": json.loads(coefs.round(5).reset_index().to_json(orient="records")),
        "results": res,
        "model_beats_baseline": bool(beats),
        "ranking_uses": chosen,
        "verdict": (
            "The Cox model beats the recency-only rules baseline on both AUC and "
            "Brier score, so the expected-value ranking uses it."
            if beats else
            "The Cox model does NOT beat the recency-only rules baseline on both "
            "AUC and Brier score. The ranking keeps the baseline. Reporting this is "
            "worth more than shipping a model that looks better than it is."),
        "calibration_note": (
            "Calibration matters more than AUC here. A ranking multiplies the "
            "predicted probability by a money value, so a model that orders "
            "accounts well but predicts the wrong level produces the wrong "
            "expected value. The decile plots in reports/calibration.png are the "
            "check that matters; ECE is the single-number summary."),
    }
    REPORT.write_text(json.dumps(rep, indent=2, default=str))
    log(f"scores -> {OUT_SCORES} ({len(scores):,} accounts)")
    log(f"report -> {REPORT}")
    log(f"plots  -> {CALIB_PNG.name}, {HAZARD_PNG.name}")


if __name__ == "__main__":
    main()
