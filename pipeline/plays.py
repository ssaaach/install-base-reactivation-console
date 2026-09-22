"""Step 6 - Reason codes and play assignment.

Input : data/processed/{accounts,contracts,coverage,renewals,whitespace}.parquet
        data/processed/cohorts.json, backblaze_hazard.json
        data/processed/{health,support_cases}_synthetic.parquet  (tagged, optional)
Output: data/processed/plays.parquet          one row per (account, reason code)
        data/processed/account_actions.parquet one row per account
        reports/plays_report.json

Rules this module keeps
-----------------------
* Every account resolves to exactly one recommended action, or an explicit
  NO_ACTION. There is no silent middle.
* Every reason code carries the evidence rows that triggered it - real award
  identifiers, not a score.
* Each reason code declares its provenance. The one SYNTHETIC-driven code is
  ranked last and can never be an account's only reason for action.
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

P = C.PROCESSED
OUT_PLAYS = P / "plays.parquet"
OUT_ACTIONS = P / "account_actions.parquet"
REPORT = C.REPORTS / "plays_report.json"
AS_OF = pd.Timestamp(C.AS_OF)

# Priority order decides the recommended action when several codes fire.
# Provenance is the strongest evidence class the code rests on.
PLAYBOOK = [
    # code,                 provenance,  action
    ("COTERM_WINDOW",       "REAL",      "Consolidate co-terminating contracts into one renewal"),
    ("RENEWAL_DUE",         "REAL",      "Renewal outreach before period of performance ends"),
    ("UNCOVERED",           "REAL",      "Attach maintenance to uncovered hardware"),
    ("LAPSED_SUPPORT",      "REAL",      "Reinstate lapsed support coverage"),
    ("REFRESH_RISK",        "DERIVED",   "Technology refresh proposal for ageing assets"),
    ("DORMANT_REACTIVATION", "REAL",     "Reactivation outreach after prolonged silence"),
    ("WHITESPACE",          "REAL",      "Introduce a category the account's peers already buy"),
    ("CHANNEL_SHIFT",       "REAL",      "Channel alignment review"),
    ("COHORT_LAG",          "DERIVED",   "Cohort-lag diagnostic: why is expansion below peers"),
    ("EXPANSION_SIGNAL",    "DERIVED",   "Expansion proposal while buying is accelerating"),
    ("VENDOR_CONCENTRATION", "REAL",     "Competitive review of a single-vendor account"),
    ("SUPPORT_RECOVERY",    "SYNTHETIC", "Support recovery plan"),
]
PRIORITY = {c: i for i, (c, _p, _a) in enumerate(PLAYBOOK)}
PROVENANCE = {c: p for c, p, _a in PLAYBOOK}
ACTION = {c: a for c, _p, a in PLAYBOOK}


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def refresh_age_threshold() -> tuple[float, str]:
    """Derive the refresh-risk age from the REAL Backblaze hazard curve.

    The threshold is the youngest age bucket whose annualised failure rate is at
    least twice the lowest observed rate. That replaces a hand-picked number with
    a curve-derived one; if the curve is unavailable we fall back to assumption
    A-07 and say so.
    """
    f = P / "backblaze_hazard.json"
    if f.exists():
        d = json.loads(f.read_text())
        if d.get("status") == "ok":
            b = [x for x in d["buckets"] if x["drive_days"] > 50_000]
            if b:
                base = min(x["afr"] for x in b)
                hits = [x for x in b if x["afr"] >= 2 * base]
                if hits:
                    age = float(min(x["age_low_years"] for x in hits))
                    return age, (f"Backblaze Drive Stats {d.get('quarter')}: AFR reaches "
                                 f"{2 * base:.2%} (2x the {base:.2%} floor) at {age:.0f}y "
                                 f"across {d['total_drive_days']:,} drive-days")
    return 5.0, "ASSUMED (A-07): Backblaze hazard curve unavailable"


def add(rows: list, account_id, code: str, detail: str, evidence: list, value=None):
    rows.append({
        "account_id": account_id,
        "reason_code": code,
        "provenance": PROVENANCE[code],
        "detail": detail,
        "evidence_ids": ";".join(str(e) for e in evidence[:12]),
        "evidence_count": len(evidence),
        "trigger_value": value,
        "priority": PRIORITY[code],
    })


def build(acc, con, cov, ren, ws, cohorts, health, cases):
    rows: list = []
    refresh_age, refresh_basis = refresh_age_threshold()
    log(f"refresh-risk age threshold: {refresh_age:.1f}y  ({refresh_basis})")

    acc_idx = acc.set_index("account_id")
    base_date = con["base_obligation_date"].fillna(con["start_date"])
    con = con.assign(_base=base_date)

    # ---- COTERM_WINDOW / RENEWAL_DUE  (REAL)
    if len(ren):
        for a, g in ren.groupby("account_id"):
            ct = g[g["in_coterm_cluster"]]
            if len(ct):
                big = ct.groupby("coterm_cluster_id")["award_amount"].sum().idxmax()
                sub = ct[ct["coterm_cluster_id"] == big]
                add(rows, a, "COTERM_WINDOW",
                    f"{len(sub)} contracts worth ${sub['award_amount'].sum():,.0f} end "
                    f"between {sub['end_date'].min():%Y-%m-%d} and "
                    f"{sub['end_date'].max():%Y-%m-%d} "
                    f"(within {C.COTERM_WINDOW_DAYS} days of one another)",
                    sub["award_id_piid"].tolist(), float(sub["award_amount"].sum()))
            nxt = g.loc[g["end_date"].idxmin()]
            add(rows, a, "RENEWAL_DUE",
                f"{len(g)} contract(s) worth ${g['award_amount'].sum():,.0f} expire within "
                f"{C.RENEWAL_LOOKAHEAD_DAYS} days; next is {nxt['award_id_piid']} on "
                f"{nxt['end_date']:%Y-%m-%d} ({int(nxt['days_to_expiry'])} days)",
                g["award_id_piid"].tolist(), float(g["award_amount"].sum()))

    # ---- UNCOVERED  (REAL)
    if len(cov):
        unc = cov[cov["uncovered"]]
        for a, g in unc.groupby("account_id"):
            add(rows, a, "UNCOVERED",
                f"{len(g)} hardware award(s) worth ${g['award_amount'].sum():,.0f} have no "
                f"maintenance award within +/-{C.COVERAGE_WINDOW_DAYS} days",
                g["award_id_piid"].tolist(), float(g["award_amount"].sum()))

        # ---- REFRESH_RISK  (DERIVED from REAL age x REAL hazard curve)
        old = cov[cov["asset_age_years"] >= refresh_age]
        for a, g in old.groupby("account_id"):
            add(rows, a, "REFRESH_RISK",
                f"{len(g)} hardware award(s) worth ${g['award_amount'].sum():,.0f} are "
                f"{g['asset_age_years'].min():.1f}-{g['asset_age_years'].max():.1f}y old, "
                f"past the {refresh_age:.0f}y elevated-failure threshold. {refresh_basis}",
                g["award_id_piid"].tolist(), float(g["asset_age_years"].max()))

    # ---- LAPSED_SUPPORT  (REAL): bought maintenance before, none in 24 months
    mt = con[con["is_maintenance"]]
    if len(mt):
        last_mt = mt.groupby("account_id")["_base"].max()
        months = (AS_OF - last_mt).dt.days / 30.4375
        for a, m in months[months >= C.DORMANCY_MONTHS].items():
            if a not in acc_idx.index:
                continue
            g = mt[mt["account_id"] == a]
            add(rows, a, "LAPSED_SUPPORT",
                f"last maintenance award was {m:.0f} months ago "
                f"({last_mt[a]:%Y-%m-%d}); {len(g)} historic support award(s) on record",
                g.sort_values("_base", ascending=False)["award_id_piid"].tolist(), float(m))

    # ---- DORMANT_REACTIVATION  (REAL)
    for a, r in acc_idx[acc_idx["dormant"]].iterrows():
        g = con[con["account_id"] == a].sort_values("_base", ascending=False)
        add(rows, a, "DORMANT_REACTIVATION",
            f"no new award in {r['months_since_last_award']:.0f} months "
            f"(last {r['last_award_date']:%Y-%m-%d}); "
            f"${r['total_obligated']:,.0f} obligated across {int(r['award_count'])} awards",
            g["award_id_piid"].head(8).tolist(), float(r["months_since_last_award"]))

    # ---- WHITESPACE  (REAL)
    if ws is not None and len(ws):
        for a, g in ws.groupby("account_id"):
            top = g.nlargest(3, "peer_adoption_rate")
            cats = ", ".join(f"{r.psc_code} ({r.peer_adoption_rate:.0%} of peers)"
                             for r in top.itertuples())
            add(rows, a, "WHITESPACE",
                f"{len(g)} category(ies) bought by peers in "
                f"{g['peer_group'].iat[0]} but never by this account: {cats}",
                top["psc_code"].tolist(), float(top["peer_adoption_rate"].max()))

    # ---- CHANNEL_SHIFT  (REAL)
    sh = acc_idx[acc_idx["channel_shift"].fillna(False)]
    for a, r in sh.iterrows():
        g = con[(con["account_id"] == a) & con["channel"].isin(["direct", "partner"])]
        add(rows, a, "CHANNEL_SHIFT",
            f"purchasing moved {r['shift_direction'].replace('_', ' ')}: direct share went "
            f"from {r['direct_share_prior']:.0%} to {r['direct_share_recent']:.0%} "
            f"of obligations in the last 24 months",
            g.sort_values("_base", ascending=False)["award_id_piid"].head(8).tolist(),
            float(r["delta"]))

    # ---- COHORT_LAG  (DERIVED)
    lag = acc_idx[(acc_idx["expansion_vs_cohort"] < 0.5)
                  & acc_idx["cohort_median_apy"].notna()
                  & (acc_idx["award_count"] >= 2)]
    for a, r in lag.iterrows():
        add(rows, a, "COHORT_LAG",
            f"places {r['awards_per_year']:.2f} awards/year against a FY{int(r['cohort_fy'])} "
            f"cohort median of {r['cohort_median_apy']:.2f} "
            f"({r['expansion_vs_cohort']:.0%} of its cohort)",
            con[con["account_id"] == a]["award_id_piid"].head(6).tolist(),
            float(r["expansion_vs_cohort"]))

    # ---- EXPANSION_SIGNAL  (DERIVED): buying rate accelerating vs own history
    cut = AS_OF - pd.Timedelta(days=730)
    recent = con[con["_base"] >= cut].groupby("account_id").size() / 2.0
    prior_span = ((cut - con.groupby("account_id")["_base"].min()).dt.days / 365.25).clip(lower=0.5)
    prior = con[con["_base"] < cut].groupby("account_id").size() / prior_span
    cmp_ = pd.DataFrame({"recent": recent, "prior": prior}).dropna()
    acc_ = cmp_[(cmp_["recent"] >= 2) & (cmp_["recent"] >= 1.5 * cmp_["prior"])]
    for a, r in acc_.iterrows():
        if a not in acc_idx.index:
            continue
        add(rows, a, "EXPANSION_SIGNAL",
            f"{r['recent']:.1f} awards/year in the last 24 months against "
            f"{r['prior']:.1f}/year before that",
            con[(con["account_id"] == a) & (con["_base"] >= cut)]["award_id_piid"].head(8).tolist(),
            float(r["recent"] / max(r["prior"], 1e-9)))

    # ---- VENDOR_CONCENTRATION  (REAL)
    vend = (con.groupby(["account_id", "recipient_entity_id"])["award_amount"].sum()
            .reset_index())
    tot = vend.groupby("account_id")["award_amount"].sum()
    top = vend.loc[vend.groupby("account_id")["award_amount"].idxmax()].set_index("account_id")
    conc = (top["award_amount"] / tot).rename("share")
    for a, s in conc[(conc >= 0.90)].items():
        if a not in acc_idx.index or acc_idx.at[a, "award_count"] < 3:
            continue
        ent = top.at[a, "recipient_entity_id"]
        nm = con.loc[con["recipient_entity_id"] == ent, "recipient_canonical"]
        add(rows, a, "VENDOR_CONCENTRATION",
            f"{s:.0%} of ${tot[a]:,.0f} obligated to a single vendor "
            f"({nm.iat[0] if len(nm) else ent})",
            con[(con["account_id"] == a) & (con["recipient_entity_id"] == ent)]["award_id_piid"]
            .head(8).tolist(), float(s))

    # ---- SUPPORT_RECOVERY  (SYNTHETIC - flagged, never a sole reason)
    if health is not None and len(health):
        h = health.set_index("account_id")
        risky = h[(h["health_band"] == "at_risk") & (h["sev1_sev2_cases_12m"] >= 3)]
        for a, r in risky.iterrows():
            add(rows, a, "SUPPORT_RECOVERY",
                f"SYNTHETIC: health score {r['health_score']:.0f} with "
                f"{int(r['sev1_sev2_cases_12m'])} S1/S2 cases in 12 months. "
                f"Generated data - not evidence about the real account.",
                [], float(r["health_score"]))

    plays = pd.DataFrame(rows)
    return plays, refresh_age, refresh_basis


def assign_actions(acc: pd.DataFrame, plays: pd.DataFrame) -> pd.DataFrame:
    """One recommended action per account, or an explicit NO_ACTION."""
    out = acc[["account_id", "account_name", "segment", "region", "size_band",
               "total_obligated", "award_count", "dormant", "cohort_fy"]].copy()
    if len(plays):
        # A SYNTHETIC-provenance code can never be the recommended action on its own.
        real = plays[plays["provenance"] != "SYNTHETIC"]
        best = (real.sort_values("priority").groupby("account_id").first()
                if len(real) else pd.DataFrame())
        codes = (plays.sort_values("priority").groupby("account_id")["reason_code"]
                 .apply(lambda s: ";".join(s)))
        counts = plays.groupby("account_id").size().rename("reason_count")
        ev = plays.groupby("account_id")["evidence_count"].sum().rename("evidence_rows")
        out = out.merge(best[["reason_code", "detail", "provenance"]].rename(
            columns={"reason_code": "primary_reason", "detail": "primary_evidence",
                     "provenance": "primary_provenance"}),
            left_on="account_id", right_index=True, how="left")
        out = out.merge(codes.rename("all_reason_codes"), left_on="account_id",
                        right_index=True, how="left")
        out = out.merge(counts, left_on="account_id", right_index=True, how="left")
        out = out.merge(ev, left_on="account_id", right_index=True, how="left")
    else:
        for c in ("primary_reason", "primary_evidence", "primary_provenance",
                  "all_reason_codes"):
            out[c] = pd.NA
        out["reason_count"] = 0
        out["evidence_rows"] = 0

    out["reason_count"] = out["reason_count"].fillna(0).astype(int)
    out["evidence_rows"] = out["evidence_rows"].fillna(0).astype(int)
    no_action = out["primary_reason"].isna()
    out.loc[no_action, "primary_reason"] = "NO_ACTION"
    out.loc[no_action, "primary_provenance"] = "REAL"
    out.loc[no_action, "primary_evidence"] = (
        "No reason code fired on real evidence: not dormant, nothing expiring inside "
        "the horizon, hardware covered, no peer-group gap, no channel shift.")
    out["all_reason_codes"] = out["all_reason_codes"].fillna("NO_ACTION")
    out["recommended_action"] = out["primary_reason"].map(ACTION).fillna(
        "No action this cycle")
    return out


def main() -> None:
    need = ["accounts.parquet", "contracts.parquet"]
    for n in need:
        if not (P / n).exists():
            raise SystemExit(f"missing {P / n} - run install_base.py first")
    acc = pd.read_parquet(P / "accounts.parquet")
    con = pd.read_parquet(P / "contracts.parquet")
    cov = pd.read_parquet(P / "coverage.parquet") if (P / "coverage.parquet").exists() else pd.DataFrame()
    ren = pd.read_parquet(P / "renewals.parquet") if (P / "renewals.parquet").exists() else pd.DataFrame()
    ws = pd.read_parquet(P / "whitespace.parquet") if (P / "whitespace.parquet").exists() else None
    cohorts = json.loads((P / "cohorts.json").read_text()) if (P / "cohorts.json").exists() else {}
    health = pd.read_parquet(P / "health_synthetic.parquet") \
        if (P / "health_synthetic.parquet").exists() else None
    cases = pd.read_parquet(P / "support_cases_synthetic.parquet") \
        if (P / "support_cases_synthetic.parquet").exists() else None

    log(f"assigning plays across {len(acc):,} accounts")
    plays, refresh_age, refresh_basis = build(acc, con, cov, ren, ws, cohorts, health, cases)
    actions = assign_actions(acc, plays)

    plays.to_parquet(OUT_PLAYS, index=False)
    actions.to_parquet(OUT_ACTIONS, index=False)

    by_code = plays["reason_code"].value_counts().to_dict() if len(plays) else {}
    rep = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "accounts": int(len(acc)),
        "play_rows": int(len(plays)),
        "accounts_with_any_reason": int(plays["account_id"].nunique()) if len(plays) else 0,
        "accounts_no_action": int((actions["primary_reason"] == "NO_ACTION").sum()),
        "refresh_risk_threshold_years": refresh_age,
        "refresh_risk_basis": refresh_basis,
        "playbook": [{"reason_code": c, "provenance": p, "action": a,
                      "priority": i, "accounts": int(by_code.get(c, 0))}
                     for i, (c, p, a) in enumerate(PLAYBOOK)],
        "reason_counts": {str(k): int(v) for k, v in by_code.items()},
        # A code that fires on nearly every account cannot prioritise anything,
        # however real its trigger. Selectivity is reported so a reader can see
        # which codes actually discriminate.
        "selectivity": {
            str(c): {
                "accounts": int(by_code.get(c, 0)),
                "share_of_accounts": round(by_code.get(c, 0) / max(len(acc), 1), 4),
                "low_information": bool(by_code.get(c, 0) / max(len(acc), 1) > 0.80),
            } for c, _p, _a in PLAYBOOK if by_code.get(c, 0)
        },
        "selectivity_note": (
            "REFRESH_RISK fires on almost every account because the hazard-derived "
            "age threshold is low and the award window is eleven years, so most "
            "assets are older than it. The trigger is real but it does not "
            "discriminate; treat a near-universal code as context, not as a "
            "ranking signal. The expected-value ranking does not weight codes."),
        "provenance_counts": ({str(k): int(v) for k, v in
                               plays["provenance"].value_counts().items()} if len(plays) else {}),
        "recommended_action_counts": {str(k): int(v) for k, v in
                                      actions["recommended_action"].value_counts().items()},
        "rule": ("A SYNTHETIC-provenance reason code never becomes an account's "
                 "recommended action; it can only appear alongside real ones."),
    }
    REPORT.write_text(json.dumps(rep, indent=2, default=str))
    log(f"plays   -> {OUT_PLAYS} ({len(plays):,} rows)")
    log(f"actions -> {OUT_ACTIONS} ({len(actions):,} accounts, "
        f"{rep['accounts_no_action']:,} NO_ACTION)")
    for c, n in sorted(by_code.items(), key=lambda kv: -kv[1]):
        log(f"    {c:<22} {n:>7,}")


if __name__ == "__main__":
    main()
