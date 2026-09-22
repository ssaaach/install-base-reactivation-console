"""Step 5b - The prime-to-subaward graph, as a third view of channel.

Input : data/interim/subawards_raw.parquet, awards_raw.parquet
Output: data/processed/subaward_flows.parquet
        data/processed/vendor_network.parquet
        reports/subaward_report.json

Why this exists
---------------
The channel rule the brief specifies - classify from recipient NAICS and name -
fails on this data (51.9% accurate against a 77.4% majority-class baseline; see
reports/channel_rule_report.json). The diagnosis is that NAICS describes what was
bought, not what the vendor is.

Subawards are an independent angle on the same question, and one that carries
real information: if a prime passes most of an award's value to a subcontractor,
and that subcontractor is a registered manufacturer, the prime was intermediating
rather than making. That is the definition of partner channel, read off behaviour
instead of off a product code.

THE COVERAGE LIMIT, STATED UP FRONT
------------------------------------
Subaward reporting is threshold-gated (FFATA), so only a small minority of prime
awards ever report one. In this extract that is 1,251 prime awards out of
270,924 - about 0.5%. The flow-through signal therefore CANNOT be a production
classifier; there is nothing to classify for 99.5% of the data.

What it can do, and what this module uses it for, is corroborate: on the awards
where it does exist, we score it against the recipient's own SAM registration
flag exactly as the NAICS rule was scored.

THE RESULT: THE HYPOTHESIS IS REJECTED
---------------------------------------
The flow-through signal scores 27.7% against a 67.8% majority-class baseline on
the 962 scorable awards. Being that far BELOW the baseline means it carries
information pointing the opposite way to the hypothesis.

The explanation is readable once seen: the firms that subcontract to
manufacturers are themselves large manufacturers and integrators with supply
chains. A small reseller has nothing to subcontract, and sits below the FFATA
reporting threshold so never appears here at all. Subcontracting to a
manufacturer marks a big prime, not a middleman.

We do NOT invert the rule to claim ~72%. The direction would have been chosen
after seeing the labels, on 962 awards, with no held-out test - that is fitting
to the evaluation set, not a validated rule. So this is reported as a rejected
hypothesis, the production channel field keeps using the SAM flag, and the wider
conclusion stands: two independent attempts to infer channel, one from product
codes and one from subcontracting behaviour, both fail. That is the argument for
using the registration flag rather than a clever derivation.

The module still earns its place: subaward_flows and vendor_network are real,
useful tables (who passes work to whom, and how much), and they are what the
console's vendor-network view is built on.
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

IN_SUB = C.INTERIM / "subawards_raw.parquet"
IN_AWARDS = C.INTERIM / "awards_raw.parquet"
OUT_FLOWS = C.PROCESSED / "subaward_flows.parquet"
OUT_NETWORK = C.PROCESSED / "vendor_network.parquet"
REPORT = C.REPORTS / "subaward_report.json"

# Pass-through above this share of the prime's obligated value marks the prime as
# having intermediated most of the work. Deobligations and modifications make the
# raw ratio wild, so it is clipped before use.
PASSTHROUGH_PARTNER_FLOOR = 0.50
PASSTHROUGH_CLIP = 3.0
MFG_SHARE_FLOOR = 0.50


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def truthy(s: pd.Series) -> pd.Series:
    v = s.astype("string").str.strip().str.upper()
    return v.map({"T": True, "TRUE": True, "Y": True, "YES": True, "1": True,
                  "F": False, "FALSE": False, "N": False, "NO": False, "0": False})


def main() -> None:
    if not IN_SUB.exists():
        log(f"no subaward file at {IN_SUB}; nothing to do")
        REPORT.write_text(json.dumps({"status": "skipped",
                                      "reason": "no subaward data"}, indent=2))
        return

    sub = pd.read_parquet(IN_SUB)
    aw = pd.read_parquet(IN_AWARDS, columns=[
        "contract_award_unique_key", "recipient_uei", "recipient_name",
        "manufacturer_of_goods", "total_obligated_amount", "psc_code",
        "awarding_office_name", "base_fy"])
    aw["mfg"] = truthy(aw["manufacturer_of_goods"])
    log(f"{len(sub):,} subaward rows, {len(aw):,} prime awards")

    # ---- a UEI -> manufacturer label, learned from the prime side only
    uei_mfg = (aw.dropna(subset=["recipient_uei"])
               .groupby("recipient_uei")["mfg"]
               .agg(lambda x: x.dropna().mode().iat[0] if len(x.dropna()) else np.nan))

    j = sub.merge(aw[["contract_award_unique_key", "total_obligated_amount", "mfg",
                      "recipient_uei", "psc_code", "base_fy"]],
                  left_on="prime_award_unique_key",
                  right_on="contract_award_unique_key", how="inner")
    log(f"{len(j):,} subaward rows join to primes in scope "
        f"({len(j) / max(len(sub), 1):.1%})")

    j["subawardee_is_mfg"] = j["subawardee_uei"].map(uei_mfg)
    labelled = j["subawardee_is_mfg"].notna()
    log(f"subawardee manufacturer label available on {labelled.mean():.1%} of rows "
        f"({j.loc[labelled, 'subawardee_uei'].nunique():,} distinct subawardees)")

    # ---- per prime award: how much flowed out, and to whom
    flows = j.groupby("prime_award_unique_key").agg(
        prime_piid=("prime_award_piid", "first"),
        prime_uei=("recipient_uei", "first"),
        prime_name=("prime_awardee_name", "first"),
        prime_is_mfg=("mfg", "first"),
        prime_obligated=("total_obligated_amount", "first"),
        awarding_office=("prime_award_awarding_office_name", "first"),
        psc_code=("psc_code", "first"),
        base_fy=("base_fy", "first"),
        subaward_total=("subaward_amount", "sum"),
        subaward_count=("subaward_number", "nunique"),
        distinct_subawardees=("subawardee_uei", "nunique"),
    ).reset_index()

    mfg_flow = (j[j["subawardee_is_mfg"] == True]  # noqa: E712
                .groupby("prime_award_unique_key")["subaward_amount"].sum()
                .rename("subaward_to_manufacturers"))
    known_flow = (j[labelled].groupby("prime_award_unique_key")["subaward_amount"]
                  .sum().rename("subaward_to_labelled"))
    flows = (flows.merge(mfg_flow, on="prime_award_unique_key", how="left")
                  .merge(known_flow, on="prime_award_unique_key", how="left"))
    flows["subaward_to_manufacturers"] = flows["subaward_to_manufacturers"].fillna(0.0)
    flows["subaward_to_labelled"] = flows["subaward_to_labelled"].fillna(0.0)

    flows["passthrough_ratio"] = (flows["subaward_total"]
                                  / flows["prime_obligated"].replace(0, np.nan))
    flows["passthrough_ratio"] = flows["passthrough_ratio"].clip(0, PASSTHROUGH_CLIP)
    flows["mfg_share_of_labelled_subawards"] = (
        flows["subaward_to_manufacturers"] / flows["subaward_to_labelled"].replace(0, np.nan))

    # ---- the flow-through channel signal
    #
    # A prime that passed out most of the award's value AND sent most of the
    # labelled portion to registered manufacturers was intermediating: partner.
    # A prime that kept the value, or shipped it to non-manufacturers, reads as
    # direct. Anything without enough evidence stays unknown rather than guessed.
    has_evidence = (flows["passthrough_ratio"].notna()
                    & flows["mfg_share_of_labelled_subawards"].notna())
    partner = (has_evidence
               & (flows["passthrough_ratio"] >= PASSTHROUGH_PARTNER_FLOOR)
               & (flows["mfg_share_of_labelled_subawards"] >= MFG_SHARE_FLOOR))
    direct = (has_evidence & ~partner)
    flows["flow_channel"] = np.where(partner, "partner",
                                     np.where(direct, "direct", "unknown"))

    # ---- score it the same way the NAICS rule was scored
    scorable = flows["prime_is_mfg"].notna() & flows["flow_channel"].isin(["direct", "partner"])
    n = int(scorable.sum())
    scoring: dict = {"available": bool(n)}
    if n:
        t = flows.loc[scorable, "prime_is_mfg"].astype(bool)
        r = flows.loc[scorable, "flow_channel"].eq("direct")
        tp = int((r & t).sum()); tn = int((~r & ~t).sum())
        fp = int((r & ~t).sum()); fn = int((~r & t).sum())
        acc = (tp + tn) / n
        base = float(t.mean())
        majority = max(base, 1 - base)
        scoring = {
            "available": True,
            "label": "manufacturer_of_goods on the PRIME (SAM registration, REAL)",
            "hypothesis": (
                "A prime that passes most of an award's value to a registered "
                "manufacturer was intermediating, so it should read as PARTNER."),
            "hypothesis_supported": bool(acc > majority),
            "awards_scored": n,
            "accuracy": round(acc, 4),
            "error_rate": round((fp + fn) / n, 4),
            "direct_precision": round(tp / max(tp + fp, 1), 4),
            "direct_recall": round(tp / max(tp + fn, 1), 4),
            "confusion": {"flow_direct_truth_manufacturer": tp,
                          "flow_direct_truth_not": fp,
                          "flow_partner_truth_manufacturer": fn,
                          "flow_partner_truth_not": tn},
            "base_rate_manufacturer": round(base, 4),
            "majority_class_accuracy": round(majority, 4),
            "beats_majority_class": bool(acc > majority),
            "accuracy_if_inverted": round(1 - acc, 4),
            "finding": (
                "The hypothesis is REJECTED, and informatively so. At "
                f"{acc:.1%} the signal sits well below the {majority:.1%} "
                "majority-class baseline, which means it carries information "
                "running OPPOSITE to the direction assumed. The readable "
                "explanation: the firms that subcontract to manufacturers are "
                "themselves large manufacturers and integrators with supply "
                "chains. A small reseller has nothing to subcontract and, being "
                "below the FFATA reporting threshold, never appears here at all. "
                "Subcontracting to a manufacturer marks a big prime, not a "
                "middleman."),
            "why_we_do_not_just_invert_it": (
                f"Flipping the rule would report {1 - acc:.1%} accuracy, but the "
                "direction would have been chosen after seeing the labels, on 962 "
                "awards, with no held-out test. That is fitting to the evaluation "
                "set, not a validated rule. The signal is reported as rejected and "
                "the production channel field continues to use the SAM "
                "registration flag."),
        }

    # ---- vendor network: who subcontracts to whom, and how concentrated it is
    net = (j.groupby(["prime_awardee_name", "subawardee_name"])
           .agg(subaward_total=("subaward_amount", "sum"),
                subawards=("subaward_number", "nunique"),
                first_seen=("subaward_action_date", "min"),
                last_seen=("subaward_action_date", "max"),
                subawardee_is_mfg=("subawardee_is_mfg", "first"))
           .reset_index()
           .sort_values("subaward_total", ascending=False))

    flows.to_parquet(OUT_FLOWS, index=False)
    net.to_parquet(OUT_NETWORK, index=False)

    # Compare against the NAICS rule on the SAME awards, else the comparison is
    # between different populations and means nothing.
    naics_cmp = {}
    ch_path = C.REPORTS / "channel_rule_report.json"
    if ch_path.exists() and n:
        ch = json.loads(ch_path.read_text()).get("error_rate_vs_sam_flag", {})
        naics_cmp = {
            "naics_rule_accuracy_all_awards": ch.get("accuracy"),
            "naics_rule_majority_baseline": ch.get("majority_class_accuracy"),
            "note": ("The NAICS rule is scored over all 269,557 awards and the "
                     "flow-through signal over the 1,251 with subaward reporting, "
                     "so these two accuracies describe different populations. The "
                     "meaningful comparison is each against its OWN majority-class "
                     "baseline, which is why both are reported."),
        }

    rep = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "provenance": "REAL (USAspending subaward reporting) + DERIVED signal",
        "coverage": {
            "subaward_rows": int(len(sub)),
            "rows_joining_primes_in_scope": int(len(j)),
            "join_rate": round(len(j) / max(len(sub), 1), 4),
            "prime_awards_with_subawards": int(len(flows)),
            "prime_awards_total": int(len(aw)),
            "share_of_primes_with_subawards": round(len(flows) / max(len(aw), 1), 5),
            "distinct_subawardees": int(j["subawardee_uei"].nunique()),
            "subawardees_labelled": int(j.loc[labelled, "subawardee_uei"].nunique()),
            "subawardee_label_rate": round(float(labelled.mean()), 4),
            "limit": ("Subaward reporting is threshold-gated, so only about 0.5% of "
                      "prime awards report one. This signal corroborates; it cannot "
                      "classify the other 99.5% and is not used to."),
        },
        "passthrough": {
            "median": round(float(flows["passthrough_ratio"].median()), 4),
            "p25": round(float(flows["passthrough_ratio"].quantile(.25)), 4),
            "p75": round(float(flows["passthrough_ratio"].quantile(.75)), 4),
            "clipped_at": PASSTHROUGH_CLIP,
            "note": ("Raw ratios run from negative to several hundred because prime "
                     "obligated amounts include deobligations and modifications. "
                     "Clipped to [0, 3] before use."),
        },
        "flow_channel_counts": {str(k): int(v) for k, v in
                                flows["flow_channel"].value_counts().items()},
        "scoring_vs_sam_flag": scoring,
        "comparison_with_naics_rule": naics_cmp,
        "rule": {
            "partner": (f"passed through >= {PASSTHROUGH_PARTNER_FLOOR:.0%} of the "
                        f"prime's obligated value AND sent >= {MFG_SHARE_FLOOR:.0%} of "
                        f"the labelled subaward value to registered manufacturers"),
            "direct": "has the evidence but does not meet the partner conditions",
            "unknown": "no pass-through ratio or no labelled subawardee - not guessed",
        },
        "top_flows": net.head(25).assign(
            subaward_total=lambda d: d["subaward_total"].round(0)).to_dict(orient="records"),
    }
    REPORT.write_text(json.dumps(rep, indent=2, default=str))

    log(f"flows   -> {OUT_FLOWS} ({len(flows):,} prime awards with subawards)")
    log(f"network -> {OUT_NETWORK} ({len(net):,} prime->sub pairs)")
    if scoring.get("available"):
        log(f"flow-through channel signal: {scoring['accuracy']:.1%} accurate on "
            f"{scoring['awards_scored']:,} awards "
            f"(majority baseline {scoring['majority_class_accuracy']:.1%}) -> "
            f"{'BEATS' if scoring['beats_majority_class'] else 'does NOT beat'} it")
    log(f"coverage: {len(flows):,}/{len(aw):,} primes "
        f"({len(flows) / max(len(aw), 1):.2%}) - corroborates, cannot classify")


if __name__ == "__main__":
    main()
