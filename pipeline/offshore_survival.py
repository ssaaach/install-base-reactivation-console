"""Step 11b - Offshore structure removal: survival model, time-based validation.

Input : data/processed/offshore_lifetimes.parquet
Output: data/processed/offshore_scores.parquet
        reports/offshore_survival_report.json
        reports/offshore_calibration.png

THE EVENT INVERTS. The federal model predicts a repurchase - an outcome you want
to cause. This predicts REMOVAL - an outcome you want to anticipate. The play
language inverts with it: not "reactivate a dormant buyer" but "this portfolio
is entering late life", and the commercial motion is decommissioning work, life
extension, integrity management, brownfield refurbishment.

THE TIME SCALE IS AGE, not calendar time, so age is the duration and cannot also
be a covariate. That makes AGE ALONE the natural rules baseline here, exactly as
recency alone is the baseline in the federal domain: rank the oldest structures
first and see whether anything beats it.

LEFT TRUNCATION is carried into the fit itself through entry_col, not simulated
by dropping rows. A structure installed in 1960 and still standing when the
observation window opened in 1975 contributes "survived from 15 to 40 years",
not "survived 40 years from scratch".

WHAT IS NOT ALLOWED TO BE A FEATURE

  abandon_flag          marks a structure already scheduled for removal, so it
                        predicts the label from an announcement of the label.
                        Used only as a validation check (trap 2).
  operator              Mms Company Num is the CURRENT operator; a 1985
                        structure may have had four. Attributing a lifetime to
                        today's holder is survivorship bias (trap 1).
  current-state fields  production / oil / gas flags, rig, crane and bed counts,
                        slot_drill_count, completion counts. Masters carries
                        TODAY's operational state, so using them to predict a
                        removal that happened in 2009 is an anachronism, not a
                        prediction. Listed in the report under
                        excluded_unobservable.

What is left is the design of the structure, fixed when it was built: how deep
it stands, how far out, how big, what type.

Honesty rule, same as the federal domain: if the model does not beat age alone
on both discrimination and calibration, the report says so and the ranking keeps
the baseline.

Run:
    python pipeline/offshore_survival.py
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

from lifelines import CoxPHFitter  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

LIFE = C.PROCESSED / "offshore_lifetimes.parquet"
OUT = C.PROCESSED / "offshore_scores.parquet"
REPORT = C.REPORTS / "offshore_survival_report.json"
CALIB = C.REPORTS / "offshore_calibration.png"

AS_OF = pd.Timestamp(C.AS_OF)
CUTOFF = pd.Timestamp(C.TRAIN_CUTOFF)
H = C.HORIZON_DAYS
DAYS_YEAR = 365.25

FEATURES = [
    "log_water_depth", "log_distance_to_shore", "deck_count", "slot_count",
    "is_caisson", "is_fixed", "is_major", "complex_structure_count",
]

EXCLUDED_UNOBSERVABLE = {
    "abandon_flag": "label leakage - marks a structure already scheduled for removal",
    "mms_company_num": "current operator only; a structure may have had several (trap 1)",
    "production_flag": "Masters carries today's operational state, not the state at risk time",
    "oil_prod_flag": "same - current state",
    "gas_prod_flag": "same - current state",
    "rig_count": "same - current equipment",
    "crane_count": "same - current equipment",
    "bed_count": "same - current equipment",
    "slot_drill_count": "cumulative to today, not known at the moment of prediction",
    "satellite_completion_count": "current state",
    "underwater_completion_count": "current state",
    "removal_method": "recorded by the removal application - exists only once removed",
}


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def features(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["log_water_depth"] = np.log1p(d["water_depth_cx"].fillna(
        d["water_depth_cx"].median()))
    d["log_distance_to_shore"] = np.log1p(d["distance_to_shore_cx"].fillna(
        d["distance_to_shore_cx"].median()))
    d["deck_count"] = d["deck_count"].fillna(d["deck_count"].median())
    d["slot_count"] = d["slot_count"].fillna(0)
    t = d["structure_type_code"].fillna("")
    d["is_caisson"] = (t == "CAIS").astype(int)
    d["is_fixed"] = (t == "FIXED").astype(int)
    d["is_major"] = (d["major_structure_flag"] == "Y").astype(int)
    d["complex_structure_count"] = d["complex_structure_count"].fillna(1)
    return d


def auc(score: np.ndarray, label: np.ndarray) -> float:
    """Mann-Whitney AUC. No sklearn dependency in this pipeline."""
    pos, neg = score[label == 1], score[label == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order), float)
    ranks[order] = np.arange(1, len(order) + 1)
    # average ranks over ties
    vals = np.concatenate([pos, neg])
    df = pd.DataFrame({"v": vals, "r": ranks})
    ranks = df.groupby("v")["r"].transform("mean").values
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) /
                 (len(pos) * len(neg)))


def calibration(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list:
    q = pd.qcut(pd.Series(p).rank(method="first"), bins, labels=False)
    out = []
    for b in range(bins):
        m = q == b
        if m.sum() == 0:
            continue
        out.append({"decile": int(b) + 1, "n": int(m.sum()),
                    "predicted": round(float(np.mean(p[m])), 4),
                    "observed": round(float(np.mean(y[m])), 4)})
    return out


def age_direction(d: pd.DataFrame) -> tuple:
    """Which way does age point, judged only on windows that close before the cutoff.

    Choosing the direction on the test window would be leakage, so this walks
    365-day windows ending at the cutoff and reports what age did in each. The
    answer is stable: age is inverted in every one of them.
    """
    ev = []
    for back in (5, 4, 3, 2, 1):
        cut = CUTOFF - pd.Timedelta(days=365 * back)
        hz = cut + pd.Timedelta(days=H)
        w = d[(d["install_date"] <= cut) &
              (d["removal_date"].isna() | (d["removal_date"] > cut))]
        if len(w) < 50:
            continue
        age = (cut - w["install_date"]).dt.days.values / DAYS_YEAR
        act = ((w["removal_date"].notna()) & (w["removal_date"] > cut) &
               (w["removal_date"] <= hz)).astype(int).values
        if act.sum() == 0:
            continue
        a = auc(age, act)
        ev.append({"window_start": str(cut.date()), "n": int(len(w)),
                   "events": int(act.sum()), "auc_age_ascending": round(a, 4),
                   "inverted": bool(a < 0.5)})
    inverted = sum(e["inverted"] for e in ev) > len(ev) / 2 if ev else False
    return ("youngest_first" if inverted else "oldest_first"), ev


def main() -> None:
    d = pd.read_parquet(LIFE)
    d = features(d)
    log(f"lifetimes {len(d):,}  events {int(d.event.sum()):,}")

    cutoff_age = (CUTOFF - d["install_date"]).dt.days / DAYS_YEAR

    # ---------------------------------------------------------------- train
    # Censor AT the cutoff so the fit cannot see a single day beyond it.
    tr = d.copy()
    tr["t_exit"] = np.minimum(tr["exit_age_years"], cutoff_age)
    tr["t_entry"] = tr["entry_age_years"]
    tr["t_event"] = ((tr["event"] == 1) & (tr["removal_date"] <= CUTOFF)).astype(int)
    tr = tr[(cutoff_age > 0) & (tr["t_exit"] > tr["t_entry"])].copy()
    log(f"train rows {len(tr):,}  events {int(tr.t_event.sum()):,}  "
        f"left-truncated {int((tr.t_entry > 0).sum()):,}")

    cols = FEATURES + ["t_entry", "t_exit", "t_event"]
    cph = CoxPHFitter(penalizer=0.1)
    cph.fit(tr[cols], duration_col="t_exit", event_col="t_event",
            entry_col="t_entry", robust=True)

    # ----------------------------------------------------------------- test
    # Structures at risk when the window opens, scored on what happened next.
    te = d[(d["install_date"] <= CUTOFF) &
           (d["removal_date"].isna() | (d["removal_date"] > CUTOFF))].copy()
    te["age_at_cutoff"] = (CUTOFF - te["install_date"]).dt.days / DAYS_YEAR
    horizon = CUTOFF + pd.Timedelta(days=H)
    te["actual"] = ((te["removal_date"].notna()) &
                    (te["removal_date"] > CUTOFF) &
                    (te["removal_date"] <= horizon)).astype(int)

    # P(removed within H | survived to age_at_cutoff): a ratio of survival at
    # two ages on the same fitted curve, not an unconditional probability.
    ages = np.sort(np.unique(np.concatenate([
        te["age_at_cutoff"].values, (te["age_at_cutoff"] + H / DAYS_YEAR).values])))
    sf = cph.predict_survival_function(te[FEATURES], times=ages)
    s_now, s_then = [], []
    for i, (_, r) in enumerate(te.iterrows()):
        a0, a1 = r["age_at_cutoff"], r["age_at_cutoff"] + H / DAYS_YEAR
        col = sf.iloc[:, i]
        s_now.append(np.interp(a0, sf.index.values, col.values))
        s_then.append(np.interp(a1, sf.index.values, col.values))
    s_now, s_then = np.array(s_now), np.array(s_then)
    te["p_removal"] = np.clip(1.0 - np.divide(s_then, np.maximum(s_now, 1e-9)), 0, 1)

    y = te["actual"].values
    auc_model = auc(te["p_removal"].values, y)
    auc_age_asc = auc(te["age_at_cutoff"].values, y)

    # THE BASELINE MUST NOT BE A STRAWMAN. Age alone is INVERTED in this domain:
    # the structures removed next year are younger than the ones left standing,
    # so ranking oldest-first scores below chance and "beating" it would mean
    # nothing. The honest baseline is age in whichever direction actually
    # predicts - but that direction is chosen on windows that END BEFORE the
    # cutoff, never on the test window, or the choice is itself leakage.
    direction, evidence = age_direction(d)
    base_score = te["age_at_cutoff"].values * (1.0 if direction == "oldest_first"
                                               else -1.0)
    auc_base = auc(base_score, y)
    log(f"test rows {len(te):,}  events {int(y.sum()):,}  "
        f"AUC model {auc_model:.3f}  AUC age-alone(asc) {auc_age_asc:.3f}  "
        f"AUC baseline({direction}) {auc_base:.3f}")

    cal = calibration(te["p_removal"].values, y)
    base_rate = float(y.mean())
    mean_pred = float(te["p_removal"].mean())
    cal_err = abs(mean_pred - base_rate)

    # The model has to win on BOTH, or the baseline keeps the ranking.
    beat_disc = bool(auc_model > auc_base)
    beat_cal = bool(cal_err <= 0.05)
    use_model = bool(beat_disc and beat_cal)
    # Even the winner here is close to a coin toss. Flag it so nothing
    # downstream can present this ranking as if it discriminated well.
    weak = bool(max(auc_model, auc_base) < 0.65)

    # ------------------------------------------- trap 2 check, never a feature
    # A model that cannot rank already-flagged structures highly is broken.
    flag = (te["abandon_flag_cx"] == "Y").values
    abandon_check = {
        "flagged_in_test": int(flag.sum()),
        "mean_score_flagged": round(float(te["p_removal"].values[flag].mean()), 4)
        if flag.any() else None,
        "mean_score_unflagged": round(float(te["p_removal"].values[~flag].mean()), 4)
        if (~flag).any() else None,
        "auc_against_flag": round(auc(te["p_removal"].values, flag.astype(int)), 4)
        if flag.any() and (~flag).any() else None,
        "note": ("abandon_flag is never a feature - it announces the label. It is "
                 "used here only to check that the model ranks structures already "
                 "scheduled for removal above the rest."),
    }

    # ------------------------------------------------------------- scoring
    live = d[d["event"] == 0].copy()
    live["age_years_now"] = (AS_OF - live["install_date"]).dt.days / DAYS_YEAR
    ages2 = np.sort(np.unique(np.concatenate([
        live["age_years_now"].values, (live["age_years_now"] + H / DAYS_YEAR).values])))
    sf2 = cph.predict_survival_function(live[FEATURES], times=ages2)
    p = []
    for i, (_, r) in enumerate(live.iterrows()):
        a0, a1 = r["age_years_now"], r["age_years_now"] + H / DAYS_YEAR
        col = sf2.iloc[:, i]
        s0 = np.interp(a0, sf2.index.values, col.values)
        s1 = np.interp(a1, sf2.index.values, col.values)
        p.append(1.0 - s1 / max(s0, 1e-9))
    live["p_removal_365d"] = np.clip(p, 0, 1)
    live["score_source"] = "cox" if use_model else f"age_baseline:{direction}"
    if use_model:
        live["rank_score"] = live["p_removal_365d"]
    else:
        # Keep the baseline, and rank by it in the direction that was learned
        # before the cutoff - not by age ascending, which is worse than chance.
        live["rank_score"] = live["age_years_now"] * (
            1.0 if direction == "oldest_first" else -1.0)
    live["rank"] = live["rank_score"].rank(ascending=False, method="first").astype(int)

    keep = ["structure_key", "complex_id", "structure_number", "structure_name",
            "structure_type_code", "area_code", "block_number", "install_date",
            "install_year", "install_imputed", "age_years_now", "water_depth_cx",
            "depth_stratum", "deepwater", "complex_structure_count",
            "complex_is_shared", "abandon_flag_cx", "p_removal_365d",
            "rank_score", "rank", "score_source"]
    live[keep].to_parquet(OUT, index=False)

    # ---------------------------------------------------------- calibration plot
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot([0, max(c["predicted"] for c in cal) * 1.1],
            [0, max(c["predicted"] for c in cal) * 1.1], "--", color="#6E7872", lw=1)
    ax.plot([c["predicted"] for c in cal], [c["observed"] for c in cal],
            "o-", color="#4788C4")
    ax.set_xlabel(f"predicted P(removal within {H}d)")
    ax.set_ylabel("observed rate")
    ax.set_title(f"Offshore removal calibration by decile\ntest window "
                 f"{CUTOFF.date()} -> {horizon.date()}", fontsize=10)
    fig.tight_layout()
    fig.savefig(CALIB, dpi=120)
    plt.close(fig)

    coef = cph.summary[["coef", "exp(coef)", "se(coef)", "p"]].round(4)

    rep = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "domain": "offshore",
        "event": "structure removal",
        "time_scale": "age since install (years)",
        "unit_of_analysis": C.OFFSHORE_UNIT,
        "as_of": str(C.AS_OF),
        "train_cutoff": str(C.TRAIN_CUTOFF),
        "horizon_days": H,
        "left_truncation": {
            "obs_start": str(C.OFFSHORE_OBS_START),
            "carried_into_fit": True,
            "method": "lifelines entry_col; rows enter at the age already reached",
            "train_rows_left_truncated": int((tr["t_entry"] > 0).sum()),
        },
        "train": {"rows": int(len(tr)), "events": int(tr["t_event"].sum())},
        "test": {"rows": int(len(te)), "events": int(y.sum()),
                 "base_rate": round(base_rate, 4),
                 "window": [str(CUTOFF.date()), str(horizon.date())]},
        "discrimination": {
            "auc_model": round(auc_model, 4),
            "auc_baseline": round(auc_base, 4),
            "baseline_direction": direction,
            "auc_age_ascending": round(auc_age_asc, 4),
            "age_is_inverted": bool(auc_age_asc < 0.5),
            "direction_chosen_on": "windows closing before the train cutoff, never the test window",
            "direction_evidence": evidence,
            "model_beats_baseline": beat_disc,
            "discrimination_is_weak": weak,
            "note": ("Age alone is INVERTED here: the structures removed in the next "
                     "year are YOUNGER than those left standing, in every pre-cutoff "
                     "window tested, so ranking oldest-first scores below chance. The "
                     "baseline is therefore age in the direction that actually "
                     "predicts. A likely mechanism, stated as a hypothesis and not a "
                     "measurement: removal tracks the idle-iron compliance clock, "
                     "which starts when production ceases, and structures still "
                     "standing at forty years are the ones that proved economic to "
                     "keep. Age is a poor proxy for either."),
        },
        "calibration": {"mean_predicted": round(mean_pred, 4),
                        "observed_rate": round(base_rate, 4),
                        "abs_error": round(cal_err, 4),
                        "within_5pp": beat_cal, "by_decile": cal},
        "decision": {
            "use_model": use_model,
            "ranking_uses": "cox" if use_model else f"age_baseline:{direction}",
            "rule": ("The model must beat the baseline on discrimination AND land "
                     "within 5 percentage points on calibration. If it does not, "
                     "the ranking keeps the baseline and this report says so."),
            "discrimination_is_weak": weak,
            "weak_note": ("Whichever wins, the better of the two is still close to a "
                          "coin toss on this test window. This ranking orders a "
                          "worklist; it does not identify which structures will come "
                          "out, and should not be presented as if it did."),
        },
        # Why age points backwards here, with the numbers that show it.
        "survivorship": {
            "median_lifetime_of_removed_years": round(float(
                d.loc[d["event"] == 1, "exit_age_years"].median()), 1),
            "median_age_of_standing_years": round(float(
                (AS_OF - d.loc[d["event"] == 0, "install_date"]).dt.days.median()
                / DAYS_YEAR), 1),
            "removed_within_10_years": int(
                ((d["event"] == 1) & (d["exit_age_years"] < 10)).sum()),
            "removed_total": int((d["event"] == 1).sum()),
            "note": ("The standing base is a SURVIVOR population, and that is what "
                     "inverts age. Structures that were removed came out at a median "
                     "of about 19 years and a quarter of them inside 10, while the "
                     "structures still standing are a median of about 44 years old. "
                     "Anything fragile or uneconomic has already gone, so what is left "
                     "at forty years has demonstrated durability that a ten-year-old "
                     "structure has not yet had the chance to demonstrate. Age is "
                     "therefore not a usable ranking signal in either direction on "
                     "this population, which is what the weak AUC is saying."),
        },
        "features": FEATURES,
        "excluded_unobservable": EXCLUDED_UNOBSERVABLE,
        "operator_used": C.OFFSHORE_USE_OPERATOR,
        "abandon_flag_check": abandon_check,
        "coefficients": json.loads(coef.to_json(orient="index")),
        "storms": {
            "policy": C.OFFSHORE_STORM_POLICY,
            "note": ("Storm losses are inside these events and cannot be separated "
                     "from planned decommissioning in this data - the removal method "
                     "field describes severance technique, not cause. Part of the "
                     "hazard this model fits is therefore weather, and part of it is "
                     "the idle-iron compliance clock, not commerce."),
        },
        "caveats": [
            "Install dates are 01-JAN placeholders for most pre-1990 structures, so "
            "age - the time scale itself - carries year-level measurement error.",
            "Removal records lag: 2024 shows 39 removals against 155 in 2023 and the "
            "as-of year is part-complete, so the test window under-counts events and "
            "observed rates in it are a floor, not a settled number.",
            "Deepwater and shallow-water structures are pooled in the fit with depth "
            "as a covariate; they are reported separately because their removal "
            "economics differ by orders of magnitude.",
        ],
        "scored_live_structures": int(len(live)),
    }
    REPORT.write_text(json.dumps(rep, indent=2))

    log(f"scores  -> {OUT}  ({len(live):,} standing structures)")
    log(f"report  -> {REPORT}")
    log(f"decision: {'COX MODEL' if use_model else 'AGE BASELINE KEPT'} "
        f"(auc {auc_model:.3f} vs {auc_base:.3f}, cal err {cal_err:.3f})")


if __name__ == "__main__":
    main()
