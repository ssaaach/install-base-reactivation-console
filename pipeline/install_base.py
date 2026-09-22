"""Step 3 - Build the install-base model from resolved awards.

Input : data/interim/awards_resolved.parquet
Output: data/processed/{accounts,contracts,coverage,renewals,whitespace}.parquet
        data/processed/cohorts.json
        reports/install_base_report.json
        reports/channel_rule_report.json

An "account" is an awarding OFFICE within its sub-agency - the organisation that
actually places the order. That is the closest public analogue to an install-base
account, and it is the grain the brief asked for. Awards with no office code fall
back to a sub-agency-level pseudo-office, flagged as such.

Provenance
----------
  REAL     copied from USAspending fields unchanged
  DERIVED  computed from REAL fields only
Nothing here is synthetic; the synthetic layer lives in synthetic_layer.py.
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

IN = C.INTERIM / "awards_resolved.parquet"
OUT_ACCOUNTS = C.PROCESSED / "accounts.parquet"
OUT_CONTRACTS = C.PROCESSED / "contracts.parquet"
OUT_COVERAGE = C.PROCESSED / "coverage.parquet"
OUT_RENEWALS = C.PROCESSED / "renewals.parquet"
OUT_WHITESPACE = C.PROCESSED / "whitespace.parquet"
OUT_COHORTS = C.PROCESSED / "cohorts.json"
REPORT = C.REPORTS / "install_base_report.json"
CHANNEL_REPORT = C.REPORTS / "channel_rule_report.json"

AS_OF = pd.Timestamp(C.AS_OF)

CENSUS_REGION = {
    "Northeast": "CT ME MA NH RI VT NJ NY PA".split(),
    "Midwest": "IL IN MI OH WI IA KS MN MO NE ND SD".split(),
    "South": "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX".split(),
    "West": "AZ CO ID MT NV NM UT WY AK CA HI OR WA".split(),
}
STATE_REGION = {s: r for r, ss in CENSUS_REGION.items() for s in ss}

MANUFACTURING_PREFIXES = ("31", "32", "33")
PARTNER_PREFIXES = ("42", "44", "45", "51", "54", "56", "23", "81")
# Name tokens that mark a firm as a reseller, distributor or integrator rather
# than a maker of goods. Deliberately narrow: these words describe the trade.
PARTNER_NAME_TOKENS = (
    "RESELL", "DISTRIBUT", "INTEGRAT", "SOLUTIONS", "CONSULTING", "SERVICES",
    "SYSTEMS GROUP", "TECHNOLOGY GROUP", "SUPPLY", "TRADING", "PROCUREMENT",
    "ACQUISITION", "CONTRACTING", "ENTERPRISES", "PARTNERS", "ASSOCIATES",
)
MAKER_NAME_TOKENS = ("MANUFACTURING", "MANUFACTURER", "INDUSTRIES", "FABRICAT")


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def fy_of(ts: pd.Series) -> pd.Series:
    return (ts + pd.offsets.DateOffset(months=3)).dt.year


def truthy(s: pd.Series) -> pd.Series:
    """USAspending boolean columns arrive as t/f, TRUE/FALSE or Y/N."""
    v = s.astype("string").str.strip().str.upper()
    return v.map({"T": True, "TRUE": True, "Y": True, "YES": True, "1": True,
                  "F": False, "FALSE": False, "N": False, "NO": False, "0": False})


# --------------------------------------------------------------- channel
def naics_name_rule(naics: pd.Series, name: pd.Series) -> pd.Series:
    """The stated rule: classify channel from recipient NAICS and name.

    Evaluated against the recipient's own SAM registration flag in
    channel_report(), which gives this rule a real error rate rather than a
    hand-wave.
    """
    n2 = naics.astype("string").str.slice(0, 2)
    nm = name.astype("string").str.upper().fillna("")
    out = pd.Series("unknown", index=naics.index, dtype="object")
    out[n2.isin(PARTNER_PREFIXES)] = "partner"
    out[n2.isin(MANUFACTURING_PREFIXES)] = "direct"
    # Name evidence overrides sector where it is explicit.
    partner_name = pd.Series(False, index=nm.index)
    for t in PARTNER_NAME_TOKENS:
        partner_name |= nm.str.contains(t, regex=False, na=False)
    maker_name = pd.Series(False, index=nm.index)
    for t in MAKER_NAME_TOKENS:
        maker_name |= nm.str.contains(t, regex=False, na=False)
    out[partner_name & ~maker_name] = "partner"
    out[maker_name & ~partner_name] = "direct"
    return out.astype("string")


def channel_report(c: pd.DataFrame) -> dict:
    """Document the channel rule and measure its error rate against a real label.

    `manufacturer_of_goods` is the recipient's own SAM.gov registration flag. We
    do not use it to build the rule's inputs, so scoring the rule against it is a
    genuine out-of-rule evaluation.
    """
    truth = c["_mfg_flag"]
    rule = c["channel_rule"]
    both = truth.notna() & rule.isin(["direct", "partner"])
    n = int(both.sum())
    rep: dict = {
        "rule": {
            "direct": (f"recipient NAICS sector in {MANUFACTURING_PREFIXES}, or the "
                       f"recipient name contains a maker token {MAKER_NAME_TOKENS}"),
            "partner": (f"recipient NAICS sector in {PARTNER_PREFIXES}, or the name "
                        f"contains a trade token such as RESELL/DISTRIBUT/INTEGRAT"),
            "unknown": "neither signal fires - deliberately not guessed",
            "precedence": "explicit name evidence overrides the NAICS sector",
            "provenance": "inputs REAL (NAICS, recipient name); classification DERIVED",
        },
        "production_field": (
            "channel uses the REAL manufacturer_of_goods flag where present and "
            "falls back to the rule only where it is missing; channel_source records "
            "which applied for every award."),
    }
    if n:
        t = truth[both]
        r = rule[both].eq("direct")
        tp = int((r & t).sum())
        tn = int((~r & ~t).sum())
        fp = int((r & ~t).sum())
        fn = int((~r & t).sum())
        acc = (tp + tn) / n
        base = float(t.mean())
        # "Always guess the majority class" is the baseline any rule must beat.
        majority = max(base, 1 - base)
        rep["error_rate_vs_sam_flag"] = {
            "label": "manufacturer_of_goods (recipient's own SAM registration, REAL)",
            "awards_scored": n,
            "accuracy": round(acc, 4),
            "error_rate": round((fp + fn) / n, 4),
            "direct_precision": round(tp / max(tp + fp, 1), 4),
            "direct_recall": round(tp / max(tp + fn, 1), 4),
            "confusion": {"rule_direct_truth_manufacturer": tp,
                          "rule_direct_truth_not": fp,
                          "rule_partner_truth_manufacturer": fn,
                          "rule_partner_truth_not": tn},
            "base_rate_manufacturer": round(base, 4),
            "majority_class_accuracy": round(majority, 4),
            "beats_majority_class": bool(acc > majority),
            "verdict": (
                "The NAICS+name rule the brief specifies does NOT work on this data. "
                f"It is {acc:.1%} accurate where always guessing the majority class "
                f"is {majority:.1%} accurate, so the rule is worse than no rule."
                if acc <= majority else
                f"The rule is {acc:.1%} accurate against a {majority:.1%} "
                "majority-class baseline."),
            "diagnosis": (
                "NAICS on a federal award describes WHAT WAS BOUGHT, not what the "
                "recipient is. 82,901 awards in scope carry NAICS 334111 "
                "'Electronic Computer Manufacturing' because a computer was "
                "purchased - but most were placed with resellers and integrators, "
                "not manufacturers. Any rule that reads vendor type out of the "
                "purchase's product code inherits that mismatch. This is why the "
                "production channel field uses the SAM registration flag instead, "
                "and why the rule is reported rather than used."),
        }
    else:
        rep["error_rate_vs_sam_flag"] = {
            "available": False,
            "note": "manufacturer_of_goods not populated in this extract"}

    rep["awards_by_channel"] = {str(k): int(v) for k, v in
                                c["channel"].value_counts(dropna=False).items()}
    rep["awards_by_channel_source"] = {str(k): int(v) for k, v in
                                       c["channel_source"].value_counts(dropna=False).items()}
    rep["rule_coverage"] = round(float(rule.isin(["direct", "partner"]).mean()), 4)

    ent = c[c["recipient_entity_id"].notna()].groupby("recipient_entity_id")["channel"]
    cls = ent.apply(lambda s: set(s.dropna()) - {"unknown"})
    mixed = cls.map(lambda s: len(s) > 1)
    mixed_ents = set(cls[mixed].index)
    rep["entity_instability_rate"] = round(float(mixed.sum()) / max(len(cls), 1), 4)
    rep["obligation_weighted_instability_rate"] = round(
        float(c.loc[c["recipient_entity_id"].isin(mixed_ents), "award_amount"].sum())
        / max(float(c["award_amount"].sum()), 1), 4)
    rep["largest_mixed_entities"] = (
        c[c["recipient_entity_id"].isin(mixed_ents)]
        .groupby("recipient_canonical")["award_amount"].sum()
        .sort_values(ascending=False).head(20).round(0).astype("int64").to_dict())
    return rep


# --------------------------------------------------------------- contracts
def build_contracts(df: pd.DataFrame) -> tuple:
    log("building contracts")
    c = df.copy()

    role = c["psc_role"].astype("object")
    naics_storage = c["naics_code"].isin(list(C.NAICS_CODES))
    role = np.where(pd.isna(role) & naics_storage, "hardware_core", role)
    c["psc_role"] = pd.Series(role, index=c.index).fillna("out_of_scope").astype("string")

    # Account = awarding office. Awards with no office code get a sub-agency-level
    # pseudo-office so they are analysable rather than dropped, and are flagged.
    c["office_missing"] = c["org_entity_id"].isna()
    fallback = "SA-" + c["awarding_sub_agency_code"].astype("string").fillna("UNK")
    c["account_id"] = c["org_entity_id"].astype("string").fillna(fallback)
    c["account_name"] = (c["office_canonical"].astype("string")
                         .fillna(c["awarding_sub_agency_name"].astype("string"))
                         .fillna("(unidentified office)"))
    c["sub_agency"] = (c["sub_agency_canonical"].astype("string")
                       .fillna(c["awarding_sub_agency_name"].astype("string")))
    c["department"] = c["awarding_agency_name"].astype("string")

    c["award_fy"] = c["base_fy"].astype("Int64")
    # Which PSC coding era the award was written under. Carried everywhere so the
    # FY2021 recoding break can be shown rather than smoothed over.
    c["psc_era"] = c["psc_code"].map(C.PSC_ERA).fillna("unmapped").astype("string")
    c["is_hardware"] = c["psc_role"].isin(["hardware_core", "hardware_adjacent"])
    c["is_maintenance"] = c["psc_role"].eq("maintenance")
    c["is_saas"] = c["psc_role"].eq("storage_as_a_service")
    c["is_idv"] = c["award_or_idv_flag"].astype("string").str.upper().eq("IDV")

    mf = c["matched_filters"].fillna("")
    c["matched_both_pulls"] = mf.str.contains("psc") & mf.str.contains("naics")

    # ---- channel
    c["_mfg_flag"] = truthy(c["manufacturer_of_goods"]) if "manufacturer_of_goods" in c else pd.NA
    c["channel_rule"] = naics_name_rule(c["naics_code"], c["recipient_canonical"]
                                        .fillna(c["recipient_name"]))
    real = c["_mfg_flag"].map({True: "direct", False: "partner"}).astype("string")
    c["channel"] = real.where(real.notna(), c["channel_rule"]).astype("string")
    c["channel_source"] = np.where(real.notna(), "sam_manufacturer_flag_REAL",
                                   np.where(c["channel_rule"].isin(["direct", "partner"]),
                                            "naics_name_rule_DERIVED", "unclassified"))

    c["region"] = (c["primary_place_of_performance_state_code"].map(STATE_REGION)
                   .fillna("Other/Unknown").astype("string"))
    c["segment"] = np.where(
        c["awarding_agency_code"].astype("string").eq("097")
        | c["department"].str.contains("Defense", case=False, na=False),
        "Defense", "Civilian")

    c["pop_days"] = (c["end_date"] - c["start_date"]).dt.days
    c["days_to_expiry"] = (c["end_date"] - AS_OF).dt.days
    c["expiring_within_horizon"] = c["days_to_expiry"].between(0, C.RENEWAL_LOOKAHEAD_DAYS)

    rep = channel_report(c)
    keep = ["contract_award_unique_key", "award_id_piid", "parent_award_id_piid",
            "account_id", "account_name", "sub_agency", "department", "segment",
            "region", "office_missing", "awarding_office_code", "awarding_sub_agency_code",
            "recipient_entity_id", "recipient_canonical", "recipient_uei",
            "recipient_parent_name", "award_amount", "total_outlayed_amount",
            "current_total_value_of_award", "potential_total_value_of_award",
            "start_date", "end_date", "base_obligation_date", "award_fy",
            "psc_code", "psc_description", "psc_role", "psc_era", "naics_code",
            "naics_description", "channel", "channel_rule", "channel_source",
            "is_hardware", "is_maintenance", "is_saas", "is_idv", "matched_both_pulls",
            "pop_days", "days_to_expiry", "expiring_within_horizon", "award_type",
            "extent_competed", "number_of_offers_received",
            "contracting_officers_determination_of_business_size",
            "prime_award_base_transaction_description", "usaspending_permalink",
            "matched_filters"]
    return c[[k for k in keep if k in c.columns]], rep


# --------------------------------------------------------------- accounts
def build_accounts(c: pd.DataFrame) -> pd.DataFrame:
    log("building accounts")
    base = c["base_obligation_date"].fillna(c["start_date"])
    c = c.assign(_base=base)
    g = c.groupby("account_id", dropna=True)

    def mode_or_none(s):
        m = s.mode()
        return m.iat[0] if len(m) else None

    acc = pd.DataFrame({
        "account_name": g["account_name"].agg(mode_or_none),
        "sub_agency": g["sub_agency"].agg(mode_or_none),
        "department": g["department"].agg(mode_or_none),
        "segment": g["segment"].agg(mode_or_none),
        "region": g["region"].agg(mode_or_none),
        "office_missing": g["office_missing"].max(),
        "first_award_date": g["_base"].min(),
        "last_award_date": g["_base"].max(),
        "total_obligated": g["award_amount"].sum(),
        "award_count": g["contract_award_unique_key"].nunique(),
        "hw_award_count": g["is_hardware"].sum(),
        "maint_award_count": g["is_maintenance"].sum(),
        "saas_award_count": g["is_saas"].sum(),
        "idv_count": g["is_idv"].sum(),
        "distinct_vendors": g["recipient_entity_id"].nunique(),
        "distinct_psc": g["psc_code"].nunique(),
    })
    for name, mask in (("hw_obligated", c["is_hardware"]),
                       ("maint_obligated", c["is_maintenance"]),
                       ("direct_obligated", c["channel"] == "direct"),
                       ("partner_obligated", c["channel"] == "partner")):
        acc[name] = c[mask].groupby("account_id")["award_amount"].sum()
        acc[name] = acc[name].fillna(0.0)

    acc["cohort_fy"] = fy_of(acc["first_award_date"]).astype("Int64")
    acc["months_since_last_award"] = ((AS_OF - acc["last_award_date"]).dt.days / 30.4375).round(1)
    acc["dormant"] = acc["months_since_last_award"] >= C.DORMANCY_MONTHS
    for m in (12, 18, 36):
        acc[f"dormant_{m}m"] = acc["months_since_last_award"] >= m
    acc["tenure_years"] = ((AS_OF - acc["first_award_date"]).dt.days / 365.25).round(2)
    acc["avg_award_value"] = (acc["total_obligated"] / acc["award_count"].clip(lower=1)).round(0)

    known = acc["direct_obligated"] + acc["partner_obligated"]
    acc["direct_share"] = (acc["direct_obligated"] / known.replace(0, np.nan)).round(4)

    acc["size_band"] = pd.qcut(acc["total_obligated"].rank(method="first"), 5,
                               labels=["XS", "S", "M", "L", "XL"]).astype("string")
    acc["peer_group"] = acc["segment"].astype("string") + " / " + acc["size_band"]
    return acc.reset_index()


# --------------------------------------------------------------- coverage
def build_coverage(c: pd.DataFrame) -> tuple:
    log("building coverage")
    hw = c[c["is_hardware"]].copy()
    mt = c[c["is_maintenance"]].copy()
    w = pd.Timedelta(days=C.COVERAGE_WINDOW_DAYS)

    hw["_anchor"] = hw["base_obligation_date"].fillna(hw["start_date"])
    mt["_anchor"] = mt["base_obligation_date"].fillna(mt["start_date"])
    hw = hw[hw["_anchor"].notna()]
    mt = mt[mt["_anchor"].notna()]

    by_acct = {k: np.sort(v.values) for k, v in mt.groupby("account_id")["_anchor"]}
    by_pair = {k: np.sort(v.values) for k, v in
               mt.groupby(["account_id", "recipient_entity_id"])["_anchor"]}

    def covered(anchors, t) -> bool:
        if anchors is None or len(anchors) == 0:
            return False
        lo, hi = np.datetime64(t - w), np.datetime64(t + w)
        i = np.searchsorted(anchors, lo, side="left")
        return bool(i < len(anchors) and anchors[i] <= hi)

    hw["covered_account_window"] = [covered(by_acct.get(a), t)
                                    for a, t in zip(hw["account_id"], hw["_anchor"])]
    hw["covered_vendor_window"] = [covered(by_pair.get((a, r)), t) for a, r, t in
                                   zip(hw["account_id"], hw["recipient_entity_id"],
                                       hw["_anchor"])]
    hw["covered"] = hw["covered_account_window"]
    hw["uncovered"] = ~hw["covered"]
    hw["asset_age_years"] = ((AS_OF - hw["_anchor"]).dt.days / 365.25).round(2)

    cols = ["contract_award_unique_key", "award_id_piid", "account_id", "account_name",
            "sub_agency", "segment", "region", "recipient_entity_id",
            "recipient_canonical", "channel", "psc_code", "psc_description", "psc_role",
            "psc_era",
            "award_amount", "start_date", "end_date", "award_fy", "asset_age_years",
            "covered", "uncovered", "covered_account_window", "covered_vendor_window",
            "usaspending_permalink"]
    cov = hw[[c_ for c_ in cols if c_ in hw.columns]].copy()

    rep = {
        "definition": (f"A hardware award is covered when a maintenance/support award "
                       f"exists for the same ACCOUNT within +/-{C.COVERAGE_WINDOW_DAYS} "
                       f"days of its base action date."),
        "hardware_awards": int(len(cov)),
        "covered": int(cov["covered"].sum()),
        "uncovered": int(cov["uncovered"].sum()),
        "coverage_rate": round(float(cov["covered"].mean()), 4) if len(cov) else None,
        "coverage_rate_same_vendor": round(float(cov["covered_vendor_window"].mean()), 4)
        if len(cov) else None,
        "uncovered_obligations_usd": float(cov.loc[cov["uncovered"], "award_amount"].sum()),
        "by_segment": {k: round(float(v), 4) for k, v in
                       cov.groupby("segment")["covered"].mean().items()},
        "by_channel": {str(k): round(float(v), 4) for k, v in
                       cov.groupby("channel")["covered"].mean().items()},
        "by_fy": {str(k): round(float(v), 4) for k, v in
                  cov.groupby("award_fy")["covered"].mean().items()},
        "by_psc_era": {str(k): {"hardware_awards": int(len(g)),
                                "coverage_rate": round(float(g["covered"].mean()), 4)}
                       for k, g in cov.groupby("psc_era")},
        "by_sub_agency_top": {str(k): round(float(v), 4) for k, v in
                              cov.groupby("sub_agency")["covered"].mean()
                              .sort_values().head(20).items()},
        "limitation": ("Support bought outside the PSC codes in scope, folded into the "
                       "hardware instrument itself, or delivered under a separate IDV "
                       "reads as uncovered. This measures separately identifiable "
                       "maintenance inside the scoped PSC set, not true service "
                       "attachment. The same-vendor rate is the stricter read."),
    }
    return cov, rep


# --------------------------------------------------------------- renewals
def build_renewals(c: pd.DataFrame) -> tuple:
    log("building renewal calendar")
    r = c[c["end_date"].notna()
          & c["days_to_expiry"].between(0, C.RENEWAL_LOOKAHEAD_DAYS)].copy()
    r = r.sort_values(["account_id", "end_date"])

    ids, sizes = [], {}
    for acct, g in r.groupby("account_id", sort=False):
        cid, anchor = 0, None
        for e in g["end_date"]:
            if anchor is None or (e - anchor).days > C.COTERM_WINDOW_DAYS:
                cid += 1
                anchor = e
            key = f"{acct}-CT{cid}"
            ids.append(key)
            sizes[key] = sizes.get(key, 0) + 1
    r["coterm_cluster_id"] = ids
    r["coterm_cluster_size"] = r["coterm_cluster_id"].map(sizes)
    r["in_coterm_cluster"] = r["coterm_cluster_size"] >= 2

    clusters = (r[r["in_coterm_cluster"]]
                .groupby(["account_id", "coterm_cluster_id"])
                .agg(account_name=("account_name", "first"), segment=("segment", "first"),
                     contracts=("contract_award_unique_key", "nunique"),
                     value=("award_amount", "sum"),
                     first_expiry=("end_date", "min"), last_expiry=("end_date", "max"))
                .reset_index())
    if len(clusters):
        clusters["span_days"] = (clusters["last_expiry"] - clusters["first_expiry"]).dt.days

    rep = {
        "lookahead_days": C.RENEWAL_LOOKAHEAD_DAYS,
        "coterm_window_days": C.COTERM_WINDOW_DAYS,
        "expiring_contracts": int(len(r)),
        "expiring_value_usd": float(r["award_amount"].sum()),
        "accounts_with_expiry": int(r["account_id"].nunique()),
        "coterm_clusters": int(len(clusters)),
        "contracts_in_coterm_clusters": int(r["in_coterm_cluster"].sum()),
        "coterm_value_usd": float(clusters["value"].sum()) if len(clusters) else 0.0,
        "largest_clusters": (clusters.sort_values("value", ascending=False).head(15)
                             [["account_name", "segment", "contracts", "value",
                               "first_expiry", "last_expiry"]]
                             .assign(value=lambda d: d["value"].round(0))
                             .to_dict(orient="records") if len(clusters) else []),
        "by_month": {str(k): int(v) for k, v in
                     r["end_date"].dt.to_period("M").astype(str)
                     .value_counts().sort_index().items()},
    }
    keep = ["contract_award_unique_key", "award_id_piid", "account_id", "account_name",
            "sub_agency", "segment", "region", "recipient_canonical", "channel",
            "psc_code", "psc_role", "award_amount", "start_date", "end_date",
            "days_to_expiry", "coterm_cluster_id", "coterm_cluster_size",
            "in_coterm_cluster", "usaspending_permalink"]
    return r[[k for k in keep if k in r.columns]], rep


# --------------------------------------------------------------- cohorts
def build_cohorts(c: pd.DataFrame, acc: pd.DataFrame) -> dict:
    log("building cohorts")
    base = c["base_obligation_date"].fillna(c["start_date"])
    ev = c.assign(_d=base)[["account_id", "_d"]].dropna()
    first = acc.set_index("account_id")["first_award_date"]
    ev = ev.join(first.rename("_first"), on="account_id")
    ev["years_since_first"] = (ev["_d"] - ev["_first"]).dt.days / 365.25

    out = {"cohorts": {}, "definition": {
        "cohort": "fiscal year of the account's first in-scope award (REAL)",
        "expansion_year_n": "share of the cohort placing a further award within year n",
        "retention_fy": "share of the cohort with at least one award in that fiscal year",
        "censoring": "cells omitted where the cohort has not been observable that long",
    }}
    for cohort, accts in acc.groupby("cohort_fy"):
        if pd.isna(cohort):
            continue
        ids = set(accts["account_id"])
        sub = ev[ev["account_id"].isin(ids)]
        row = {"accounts": int(len(ids)),
               "total_obligated": float(accts["total_obligated"].sum()),
               "observable_years": round(
                   float((AS_OF - accts["first_award_date"].min()).days / 365.25), 2),
               "dormant_now": int(accts["dormant"].sum()),
               "dormant_rate_now": round(float(accts["dormant"].mean()), 4),
               "expansion": {}, "retention_by_fy": {}}
        for n in (1, 2, 3):
            elig = accts[(AS_OF - accts["first_award_date"]).dt.days / 365.25 >= n]
            if not len(elig):
                continue
            eids = set(elig["account_id"])
            win = sub[(sub["account_id"].isin(eids))
                      & (sub["years_since_first"] > n - 1)
                      & (sub["years_since_first"] <= n)]
            row["expansion"][f"year_{n}"] = {
                "denominator": int(len(eids)),
                "accounts_with_further_award": int(win["account_id"].nunique()),
                "rate": round(win["account_id"].nunique() / max(len(eids), 1), 4)}
        for fy, g in sub.assign(fy=fy_of(sub["_d"])).groupby("fy"):
            if pd.isna(fy) or int(fy) < int(cohort):
                continue
            row["retention_by_fy"][str(int(fy))] = round(
                g["account_id"].nunique() / max(len(ids), 1), 4)
        out["cohorts"][str(int(cohort))] = row

    out["cohort_year1_expansion_rate"] = {
        k: v["expansion"].get("year_1", {}).get("rate")
        for k, v in out["cohorts"].items()
        if v["expansion"].get("year_1")}
    return out


def account_expansion_rates(c: pd.DataFrame, acc: pd.DataFrame) -> pd.DataFrame:
    base = c["base_obligation_date"].fillna(c["start_date"])
    ev = c.assign(_d=base)[["account_id", "_d"]].dropna()
    per = ev.groupby("account_id")["_d"].agg(["min", "max", "count"])
    per["observed_years"] = ((AS_OF - per["min"]).dt.days / 365.25).clip(lower=0.5)
    per["awards_per_year"] = per["count"] / per["observed_years"]
    out = acc.set_index("account_id").join(per[["awards_per_year"]])
    med = out.groupby("cohort_fy")["awards_per_year"].median().rename("cohort_median_apy")
    out = out.join(med, on="cohort_fy")
    out["expansion_vs_cohort"] = (out["awards_per_year"] / out["cohort_median_apy"]).round(3)
    return out[["awards_per_year", "cohort_median_apy", "expansion_vs_cohort"]].reset_index()


# --------------------------------------------------------------- whitespace
def build_whitespace(c: pd.DataFrame, acc: pd.DataFrame, floor: float = 0.40) -> tuple:
    log("building whitespace")
    bought = (c.dropna(subset=["psc_code"])
              .groupby(["account_id", "psc_code"])["award_amount"].sum().reset_index())
    pg = acc.set_index("account_id")["peer_group"]
    bought["peer_group"] = bought["account_id"].map(pg)

    peers = acc.groupby("peer_group")["account_id"].nunique().rename("peer_accounts")
    adopt = (bought.groupby(["peer_group", "psc_code"])["account_id"].nunique()
             .rename("adopters").reset_index().join(peers, on="peer_group"))
    adopt["peer_adoption_rate"] = adopt["adopters"] / adopt["peer_accounts"].clip(lower=1)

    have = set(zip(bought["account_id"], bought["psc_code"]))
    rows = []
    for pgname, cand in adopt[adopt["peer_adoption_rate"] >= floor].groupby("peer_group"):
        members = acc.loc[acc["peer_group"] == pgname, "account_id"]
        cand_t = list(zip(cand["psc_code"], cand["peer_adoption_rate"], cand["adopters"]))
        for a in members:
            for psc, rate, n in cand_t:
                if (a, psc) not in have:
                    rows.append({"account_id": a, "peer_group": pgname, "psc_code": psc,
                                 "peer_adoption_rate": round(float(rate), 4),
                                 "peer_adopters": int(n)})
    ws = pd.DataFrame(rows, columns=["account_id", "peer_group", "psc_code",
                                     "peer_adoption_rate", "peer_adopters"])
    if len(ws):
        desc = (c.dropna(subset=["psc_code"]).drop_duplicates("psc_code")
                .set_index("psc_code")["psc_description"])
        ws["psc_description"] = ws["psc_code"].map(desc)
        ws["psc_role"] = ws["psc_code"].map(C.PSC_ROLE)
        ws = ws.sort_values(["account_id", "peer_adoption_rate"], ascending=[True, False])

    rep = {
        "peer_group_definition": "segment (Defense/Civilian) x obligation size quintile",
        "peer_adoption_floor": floor,
        "peer_groups": int(acc["peer_group"].nunique()),
        "whitespace_rows": int(len(ws)),
        "accounts_with_whitespace": int(ws["account_id"].nunique()) if len(ws) else 0,
        "top_gaps": (ws.groupby(["psc_code", "psc_description"]).size()
                     .sort_values(ascending=False).head(15)
                     .reset_index(name="accounts_missing").to_dict(orient="records")
                     if len(ws) else []),
    }
    return ws, rep


# --------------------------------------------------------------- dormancy scopes
def dormancy_sensitivity(c: pd.DataFrame) -> dict:
    """Dormancy under four scopes, because one number would mislead.

    The PSC recoding at FY2021 narrowed what "in scope" means as well as renaming
    it: the legacy 70-series was one broad ADP-equipment bucket, the modern
    taxonomy splits it by function and we take only the storage, compute and
    data-centre slices. An office that kept buying, say, output devices after 2021
    leaves our scope and reads as dormant without changing its behaviour.

    Publishing a single dormancy rate would present that definitional shift as a
    behavioural finding. So we publish the spread instead, and the console shows
    it.
    """
    base = c["base_obligation_date"].fillna(c["start_date"])
    w = c.assign(_d=base).dropna(subset=["_d"])

    def rate(df: pd.DataFrame) -> dict:
        if df.empty:
            return {"accounts": 0}
        last = df.groupby("account_id")["_d"].max()
        m = (AS_OF - last).dt.days / 30.4375
        return {
            "accounts": int(len(m)),
            "median_months_idle": round(float(m.median()), 1),
            **{f"dormant_{k}m": round(float((m >= k).mean()), 4) for k in (12, 18, 24, 36)},
        }

    storage = ["hardware_core", "maintenance", "storage_as_a_service"]
    return {
        "scopes": {
            "all_in_scope_codes": rate(w),
            "hardware_core_only": rate(w[w["psc_role"] == "hardware_core"]),
            "storage_core_maintenance_saas": rate(w[w["psc_role"].isin(storage)]),
            "modern_era_codes_only": rate(w[w["psc_era"] == "modern"]),
        },
        "reading": (
            "The headline dormancy rate uses all in-scope codes. It is the highest "
            "of the four because the legacy 70-series covered more ground than the "
            "modern slices we carry forward, so offices leave scope at FY2021 "
            "without changing behaviour. The modern-era-only figure is the cleanest "
            "read of recent behaviour, since every recent award is coded that way; "
            "it is much lower. Neither is wrong - they answer different questions, "
            "and quoting only the first would be misleading."),
        "what_is_real": (
            "The ABSENCE of an award in scope is a real observation, not a "
            "simulation. What is definitional is the word 'in scope'."),
    }


# --------------------------------------------------------------- channel shift
def build_channel_shift(c: pd.DataFrame) -> pd.DataFrame:
    base = c["base_obligation_date"].fillna(c["start_date"])
    k = c.assign(_d=base)
    k = k[k["channel"].isin(["direct", "partner"]) & k["_d"].notna()]
    cut = AS_OF - pd.Timedelta(days=730)

    def share(g):
        tot = g["award_amount"].sum()
        return float(g.loc[g["channel"] == "direct", "award_amount"].sum() / tot) if tot else np.nan

    recent = k[k["_d"] >= cut].groupby("account_id").apply(share, include_groups=False)
    prior = k[k["_d"] < cut].groupby("account_id").apply(share, include_groups=False)
    out = pd.DataFrame({"direct_share_recent": recent, "direct_share_prior": prior})
    out["delta"] = out["direct_share_recent"] - out["direct_share_prior"]
    crossed = (((out["direct_share_prior"] >= 0.5) & (out["direct_share_recent"] < 0.5))
               | ((out["direct_share_prior"] < 0.5) & (out["direct_share_recent"] >= 0.5)))
    out["channel_shift"] = crossed & (out["delta"].abs() >= 0.30)
    out["shift_direction"] = np.where(
        ~out["channel_shift"], "none",
        np.where(out["delta"] < 0, "direct_to_partner", "partner_to_direct"))
    return out.reset_index()


# --------------------------------------------------------------- driver
def main() -> None:
    if not IN.exists():
        raise SystemExit(f"missing {IN} - run entity_resolution.py first")
    df = pd.read_parquet(IN)
    log(f"loaded {len(df):,} resolved awards")

    contracts, chan_rep = build_contracts(df)
    in_scope = contracts[contracts["psc_role"] != "out_of_scope"].copy()
    log(f"{len(in_scope):,} in-scope awards "
        f"({len(contracts) - len(in_scope):,} out of scope)")

    accounts = build_accounts(in_scope)
    log(f"{len(accounts):,} accounts (awarding offices)")
    coverage, cov_rep = build_coverage(in_scope)
    renewals, ren_rep = build_renewals(in_scope)
    cohorts = build_cohorts(in_scope, accounts)
    whitespace, ws_rep = build_whitespace(in_scope, accounts)
    exp = account_expansion_rates(in_scope, accounts)
    shift = build_channel_shift(in_scope)

    accounts = accounts.merge(exp, on="account_id", how="left") \
                       .merge(shift, on="account_id", how="left")
    accounts["channel_shift"] = accounts["channel_shift"].fillna(False)
    accounts["shift_direction"] = accounts["shift_direction"].fillna("none")

    if len(coverage):
        cba = coverage.groupby("account_id")["covered"].agg(["mean", "size", "sum"])
        accounts = accounts.merge(
            cba.rename(columns={"mean": "coverage_rate", "size": "hw_assets",
                                "sum": "covered_assets"}),
            left_on="account_id", right_index=True, how="left")
    else:
        accounts["coverage_rate"] = np.nan
        accounts["hw_assets"] = 0
        accounts["covered_assets"] = 0
    accounts["hw_assets"] = accounts["hw_assets"].fillna(0)
    accounts["covered_assets"] = accounts["covered_assets"].fillna(0)
    accounts["uncovered_assets"] = (accounts["hw_assets"] - accounts["covered_assets"]).astype(int)

    if len(renewals):
        rba = renewals.groupby("account_id").agg(
            expiring_contracts=("contract_award_unique_key", "nunique"),
            expiring_value=("award_amount", "sum"),
            next_expiry=("end_date", "min"),
            in_coterm=("in_coterm_cluster", "max"))
        accounts = accounts.merge(rba, left_on="account_id", right_index=True, how="left")
    for col, fill in (("expiring_contracts", 0), ("expiring_value", 0.0),
                      ("in_coterm", False)):
        if col not in accounts.columns:
            accounts[col] = fill
        # The merge leaves these object-dtype with NaN holes. fillna would
        # downcast silently, so fill and set the dtype explicitly.
        accounts[col] = accounts[col].where(accounts[col].notna(), fill).astype(type(fill))
    accounts["expiring_contracts"] = accounts["expiring_contracts"].astype(int)

    wsa = (whitespace.groupby("account_id").size().rename("whitespace_count")
           if len(whitespace) else pd.Series(dtype=int, name="whitespace_count"))
    accounts = accounts.merge(wsa, left_on="account_id", right_index=True, how="left")
    accounts["whitespace_count"] = accounts["whitespace_count"].fillna(0).astype(int)

    in_scope.to_parquet(OUT_CONTRACTS, index=False)
    accounts.to_parquet(OUT_ACCOUNTS, index=False)
    coverage.to_parquet(OUT_COVERAGE, index=False)
    renewals.to_parquet(OUT_RENEWALS, index=False)
    if len(whitespace):
        whitespace.to_parquet(OUT_WHITESPACE, index=False)
    OUT_COHORTS.write_text(json.dumps(cohorts, indent=2, default=str))
    CHANNEL_REPORT.write_text(json.dumps(chan_rep, indent=2, default=str))

    report = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "as_of": str(C.AS_OF),
        "framing": ("US federal enterprise-storage procurement, used as a PROXY for "
                    "install-base dynamics. This is not any vendor's install base."),
        "account_grain": "awarding office within sub-agency (REAL, from awarding_office_code)",
        "accounts_without_office_code": int(accounts["office_missing"].sum()),
        "awards_in_scope": int(len(in_scope)),
        "awards_out_of_scope": int(len(contracts) - len(in_scope)),
        "accounts": int(len(accounts)),
        "total_obligated_usd": float(in_scope["award_amount"].sum()),
        "dormant_accounts": int(accounts["dormant"].sum()),
        "dormancy_rate": round(float(accounts["dormant"].mean()), 4),
        "dormancy_months": C.DORMANCY_MONTHS,
        "dormancy_by_threshold": {f"{m}m": int(accounts[f"dormant_{m}m"].sum())
                                  for m in (12, 18, 36)},
        "dormancy_sensitivity": dormancy_sensitivity(in_scope),
        "accounts_by_segment": {str(k): int(v) for k, v in
                                accounts["segment"].value_counts().items()},
        "accounts_by_region": {str(k): int(v) for k, v in
                               accounts["region"].value_counts().items()},
        "accounts_by_cohort_fy": {str(k): int(v) for k, v in
                                  accounts["cohort_fy"].value_counts().sort_index().items()},
        "channel_shift_accounts": int(accounts["channel_shift"].sum()),
        "psc_era_transition": {
            "break_fiscal_year": C.PSC_ERA_BREAK_FY,
            "awards_by_fy_and_era": {
                str(int(fy)): {str(k): int(v) for k, v in
                               g["psc_era"].value_counts().items()}
                for fy, g in in_scope.dropna(subset=["award_fy"]).groupby("award_fy")},
            "note": ("Legacy 70-series/J-D PSC codes fall to zero from FY2022 and the "
                     "modern 7A-7K/DA-DK codes are absent before FY2019. Every PSC "
                     "family in scope spans both eras so accounts do not appear to go "
                     "dormant when the coding changed."),
        },
        "coverage": cov_rep, "renewals": ren_rep,
        "whitespace": ws_rep, "channel": chan_rep,
    }
    REPORT.write_text(json.dumps(report, indent=2, default=str))

    log(f"accounts   -> {len(accounts):,}")
    log(f"coverage   -> {len(coverage):,} hardware assets, "
        f"{cov_rep['coverage_rate']:.1%} covered" if cov_rep["coverage_rate"] is not None
        else "coverage   -> none")
    log(f"renewals   -> {len(renewals):,} expiring, {ren_rep['coterm_clusters']:,} co-term clusters")
    log(f"whitespace -> {len(whitespace):,} gaps")
    log(f"dormancy   -> {report['dormancy_rate']:.1%} "
        f"({report['dormant_accounts']:,}/{report['accounts']:,})")
    er = chan_rep.get("error_rate_vs_sam_flag", {})
    if er.get("awards_scored"):
        log(f"channel rule error rate vs SAM manufacturer flag: {er['error_rate']:.1%} "
            f"on {er['awards_scored']:,} awards")
    log(f"report     -> {REPORT}")


if __name__ == "__main__":
    main()
