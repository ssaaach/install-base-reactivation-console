"""Step 5 - The synthetic layer. EVERY FIELD HERE IS SYNTHETIC.

Input : data/processed/accounts.parquet, coverage.parquet
        data/processed/backblaze_hazard.json   (REAL, used as a conditioning input)
Output: data/processed/telemetry_synthetic.parquet
        data/processed/support_cases_synthetic.parquet
        data/processed/health_synthetic.parquet
        reports/synthetic_report.json

Why this exists
---------------
Utilisation telemetry, support case history and a customer health score have no
public federal equivalent. They are generated so the console can demonstrate the
shape of an install-base workflow end to end. They are conditioned on REAL award
patterns so they move in plausible directions, and they are seed-fixed so the
whole pipeline is reproducible.

WHAT THIS MEANS FOR A READER
----------------------------
Nothing in these three tables is evidence about the real world. No finding in the
console may rest on them. They are excluded from the survival model's covariates
and from the expected-value ranking; see survival.py and ranking.py, which read
only REAL and DERIVED columns. Their only job is to populate account-detail views
so the interface is complete, and they carry a SYNTHETIC tag everywhere they
appear.

Conditioning (synthetic values respond to real ones)
----------------------------------------------------
  utilisation      scales with log obligations, decays with months since last
                   award, seasonal federal Q4 (Jul-Sep) bump
  support cases    Poisson, rate driven by hardware asset count AND real asset
                   age through the Backblaze empirical hazard curve
  health score     bounded composite of utilisation trend, case load, REAL
                   coverage rate and REAL dormancy
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

ACCOUNTS = C.PROCESSED / "accounts.parquet"
COVERAGE = C.PROCESSED / "coverage.parquet"
HAZARD = C.PROCESSED / "backblaze_hazard.json"

OUT_TELEMETRY = C.PROCESSED / "telemetry_synthetic.parquet"
OUT_CASES = C.PROCESSED / "support_cases_synthetic.parquet"
OUT_HEALTH = C.PROCESSED / "health_synthetic.parquet"
REPORT = C.REPORTS / "synthetic_report.json"

MONTHS = 36  # trailing months of telemetry to generate
AS_OF = pd.Timestamp(C.AS_OF)


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def load_hazard() -> tuple[callable, dict]:
    """Return (age_years -> annual failure probability, provenance dict)."""
    if HAZARD.exists():
        payload = json.loads(HAZARD.read_text())
        if payload.get("status") == "ok":
            buckets = payload["buckets"]
            lows = np.array([float(b["age_low_years"]) for b in buckets])
            afrs = np.array([float(b["afr"]) for b in buckets])

            def f(age: np.ndarray) -> np.ndarray:
                idx = np.clip(np.searchsorted(lows, age, side="right") - 1, 0, len(afrs) - 1)
                return afrs[idx]

            return f, {"hazard_source": "Backblaze Drive Stats (REAL)",
                       "quarter": payload.get("quarter"),
                       "drive_days": payload.get("total_drive_days"),
                       "failures": payload.get("total_failures")}
    # Fallback: assumed curve, registered as assumption A-07.
    def f(age: np.ndarray) -> np.ndarray:
        return np.clip(0.005 + 0.006 * np.asarray(age, dtype=float), 0.005, 0.08)

    return f, {"hazard_source": "ASSUMED (A-07) - Backblaze layer unavailable"}


def build(accounts: pd.DataFrame, coverage: pd.DataFrame, rng: np.random.Generator):
    hazard_fn, hazard_prov = load_hazard()

    # ---------- per-account real conditioning inputs
    a = accounts.copy()
    a["log_oblig"] = np.log10(a["total_obligated"].clip(lower=1_000))
    recency = a["months_since_last_award"].fillna(a["months_since_last_award"].max())
    a["_recency"] = recency

    # Mean asset age per account, from REAL award dates.
    age_by_acct = coverage.groupby("account_id")["asset_age_years"].mean()
    a["mean_asset_age"] = a["account_id"].map(age_by_acct)
    a["mean_asset_age"] = a["mean_asset_age"].fillna(a["mean_asset_age"].median())

    n = len(a)
    idx = np.arange(n)

    # ---------- monthly utilisation telemetry (SYNTHETIC)
    # Base capacity utilisation rises with spend, falls with staleness.
    base = 0.34 + 0.09 * (a["log_oblig"] - a["log_oblig"].min()) / \
        max(a["log_oblig"].max() - a["log_oblig"].min(), 1e-9) * 4.0
    base = np.clip(base.to_numpy(), 0.22, 0.88)
    decay = np.clip(a["_recency"].to_numpy() / 60.0, 0, 1) * 0.16
    level = np.clip(base - decay, 0.12, 0.95)
    # Per-account random walk drift and noise scale.
    drift = rng.normal(0.0, 0.0035, size=n)
    noise = rng.uniform(0.008, 0.030, size=n)

    months = pd.date_range(
        (AS_OF - pd.DateOffset(months=MONTHS - 1)).replace(day=1), periods=MONTHS, freq="MS")
    rows = []
    walk = np.zeros(n)
    for m_i, m in enumerate(months):
        walk = walk + drift + rng.normal(0, noise)
        # US federal buying season: usage climbs into the Sep fiscal year end.
        seasonal = 0.018 * np.sin((m.month - 7) / 12.0 * 2 * np.pi)
        util = np.clip(level + walk + seasonal, 0.05, 0.99)
        rows.append(pd.DataFrame({
            "account_id": a["account_id"].to_numpy(),
            "month": m,
            "capacity_utilisation": np.round(util, 4),
        }))
    telemetry = pd.concat(rows, ignore_index=True)
    telemetry["provenance"] = "SYNTHETIC"

    # utilisation trend over the trailing 12 months, per account
    last12 = telemetry[telemetry["month"] >= months[-12]]
    trend = (last12.sort_values("month").groupby("account_id")["capacity_utilisation"]
             .agg(lambda s: float(s.iloc[-1] - s.iloc[0])))
    latest_util = (telemetry[telemetry["month"] == months[-1]]
                   .set_index("account_id")["capacity_utilisation"])

    # ---------- support cases (SYNTHETIC, rate conditioned on REAL hazard + assets)
    assets = a["hw_assets"].fillna(0).to_numpy()
    ages = a["mean_asset_age"].to_numpy()
    afr = hazard_fn(ages)
    # Expected hardware-driven cases per year, plus a floor of non-hardware cases.
    lam = assets * afr * 3.0 + 0.6 + 0.9 * (a["log_oblig"].to_numpy() - 3.0).clip(0)
    lam = np.clip(lam, 0.2, 80.0)
    case_counts = rng.poisson(lam)

    sev_p = np.array([0.08, 0.22, 0.42, 0.28])  # S1..S4
    cases = []
    for i, k in zip(idx, case_counts):
        if k == 0:
            continue
        acct = a["account_id"].iat[i]
        opened = AS_OF - pd.to_timedelta(rng.integers(1, 365, size=k), unit="D")
        sev = rng.choice([1, 2, 3, 4], size=k, p=sev_p)
        ttr = np.round(rng.gamma(shape=2.0, scale=np.where(sev <= 2, 18.0, 7.0)), 1)
        cases.append(pd.DataFrame({
            "account_id": acct, "opened_date": opened, "severity": sev,
            "time_to_resolve_hours": ttr,
            "category": rng.choice(
                ["hardware_fault", "capacity", "firmware", "performance", "how_to"],
                size=k, p=[0.31, 0.21, 0.17, 0.16, 0.15]),
        }))
    support = (pd.concat(cases, ignore_index=True) if cases
               else pd.DataFrame(columns=["account_id", "opened_date", "severity",
                                          "time_to_resolve_hours", "category"]))
    support["provenance"] = "SYNTHETIC"

    case_load = support.groupby("account_id").size() if len(support) else pd.Series(dtype=int)
    sev12 = (support[support["severity"] <= 2].groupby("account_id").size()
             if len(support) else pd.Series(dtype=int))

    # ---------- health score (SYNTHETIC composite; two inputs are REAL)
    h = pd.DataFrame({"account_id": a["account_id"]})
    h["utilisation_latest"] = h["account_id"].map(latest_util)
    h["utilisation_trend_12m"] = h["account_id"].map(trend).fillna(0.0)
    h["open_cases_12m"] = h["account_id"].map(case_load).fillna(0).astype(int)
    h["sev1_sev2_cases_12m"] = h["account_id"].map(sev12).fillna(0).astype(int)
    h["coverage_rate_real"] = a["coverage_rate"].to_numpy()
    h["dormant_real"] = a["dormant"].to_numpy()

    util_c = ((h["utilisation_latest"] - 0.5).clip(-0.4, 0.4) / 0.4) * 12
    trend_c = (h["utilisation_trend_12m"].clip(-0.15, 0.15) / 0.15) * 14
    load = h["open_cases_12m"] / h["open_cases_12m"].clip(lower=1).median()
    load_c = -np.clip(load, 0, 4) * 6
    sev_c = -np.clip(h["sev1_sev2_cases_12m"], 0, 8) * 1.6
    cov_c = (h["coverage_rate_real"].fillna(0) - 0.5) * 26
    dorm_c = np.where(h["dormant_real"], -16, 4)

    score = 62 + util_c + trend_c + load_c + sev_c + cov_c + dorm_c + rng.normal(0, 4.0, size=n)
    h["health_score"] = np.clip(score, 0, 100).round(1)
    h["health_band"] = pd.cut(h["health_score"], [-0.1, 40, 60, 80, 100.1],
                              labels=["at_risk", "watch", "stable", "strong"]).astype("string")
    h["provenance"] = "SYNTHETIC"
    h["note"] = "composite of SYNTHETIC telemetry/cases and REAL coverage/dormancy"

    return telemetry, support, h, hazard_prov


def main() -> None:
    if not ACCOUNTS.exists():
        raise SystemExit(f"missing {ACCOUNTS} - run install_base.py first")
    accounts = pd.read_parquet(ACCOUNTS)
    coverage = pd.read_parquet(COVERAGE) if COVERAGE.exists() else pd.DataFrame(
        columns=["account_id", "asset_age_years"])
    log(f"conditioning synthetic layer on {len(accounts):,} real accounts")

    rng = np.random.default_rng(C.RANDOM_SEED)
    telemetry, support, health, hazard_prov = build(accounts, coverage, rng)

    telemetry.to_parquet(OUT_TELEMETRY, index=False)
    support.to_parquet(OUT_CASES, index=False)
    health.to_parquet(OUT_HEALTH, index=False)

    rep = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "provenance": "SYNTHETIC",
        "seed": C.RANDOM_SEED,
        "reproducible": True,
        "warning": ("Every field in these three tables is generated. They are not "
                    "evidence about the real world and no console finding rests on "
                    "them. They are excluded from the survival model and from the "
                    "expected-value ranking."),
        "tables": {
            "telemetry_synthetic": {"rows": int(len(telemetry)),
                                    "accounts": int(telemetry['account_id'].nunique()),
                                    "months": MONTHS},
            "support_cases_synthetic": {"rows": int(len(support)),
                                        "accounts": int(support['account_id'].nunique())
                                        if len(support) else 0},
            "health_synthetic": {"rows": int(len(health))},
        },
        "conditioning": {
            "utilisation": ("level scales with log10 total obligations (REAL), decays "
                            "with months since last award (REAL), federal Q4 seasonality"),
            "support_cases": ("Poisson; rate = hardware asset count (REAL) x annual "
                              "failure rate at the account's mean asset age (REAL age, "
                              "REAL Backblaze hazard curve) + spend-scaled floor"),
            "health_score": ("composite of SYNTHETIC utilisation level and trend and "
                             "case load, plus REAL coverage rate and REAL dormancy"),
            **hazard_prov,
        },
        "distributions": {
            "utilisation_mean": round(float(telemetry["capacity_utilisation"].mean()), 4),
            "utilisation_p10": round(float(telemetry["capacity_utilisation"].quantile(.1)), 4),
            "utilisation_p90": round(float(telemetry["capacity_utilisation"].quantile(.9)), 4),
            "cases_per_account_mean": round(float(len(support) / max(len(accounts), 1)), 2),
            "health_mean": round(float(health["health_score"].mean()), 2),
            "health_band_counts": {str(k): int(v) for k, v in
                                   health["health_band"].value_counts().items()},
        },
    }
    REPORT.write_text(json.dumps(rep, indent=2, default=str))
    log(f"telemetry -> {OUT_TELEMETRY} ({len(telemetry):,} rows) [SYNTHETIC]")
    log(f"cases     -> {OUT_CASES} ({len(support):,} rows) [SYNTHETIC]")
    log(f"health    -> {OUT_HEALTH} ({len(health):,} rows) [SYNTHETIC]")
    log(f"report    -> {REPORT}")


if __name__ == "__main__":
    main()
