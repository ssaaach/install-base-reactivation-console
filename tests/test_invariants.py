"""The claims this project makes about itself, enforced.

Every check here corresponds to a promise in the README. If a promise stops being
true, this fails rather than the README quietly becoming a lie.

Runs with pytest if it is installed, and standalone if it is not:

    python tests/test_invariants.py
    pytest tests/ -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import assumptions as A  # noqa: E402
import config as C  # noqa: E402
import provenance as PROV  # noqa: E402

P = C.PROCESSED
R = C.REPORTS

SYNTHETIC_TABLES = ["telemetry_synthetic", "support_cases_synthetic", "health_synthetic"]
SYNTHETIC_COLUMNS = {"capacity_utilisation", "health_score", "health_band",
                     "open_cases_12m", "sev1_sev2_cases_12m", "severity",
                     "time_to_resolve_hours", "utilisation_latest",
                     "utilisation_trend_12m"}


def jload(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def pq(name: str) -> pd.DataFrame:
    f = P / name
    return pd.read_parquet(f) if f.exists() else pd.DataFrame()


# ---------------------------------------------------------- provenance claims
def test_every_provenance_field_has_a_tag_and_source():
    for row in PROV.table():
        assert row["tag"], f"{row['table']}.{row['field']} has no provenance tag"
        assert row["source"], f"{row['table']}.{row['field']} has no source"
        assert any(t in row["tag"] for t in PROV.TAGS), \
            f"{row['field']} carries an unknown tag {row['tag']!r}"


def test_provenance_covers_all_four_tags():
    counts = PROV.counts()
    for tag in PROV.TAGS:
        assert counts.get(tag, 0) > 0, f"no field is tagged {tag}"


def test_synthetic_tables_are_all_tagged_synthetic():
    tagged = {r["table"] for r in PROV.table() if r["tag"] == "SYNTHETIC"}
    for t in SYNTHETIC_TABLES:
        assert t in tagged, f"{t} is not tagged SYNTHETIC in the provenance table"


# ---------------------------------------------------- synthetic never modelled
def test_survival_model_uses_no_synthetic_feature():
    import survival
    for f in survival.FEATURES:
        assert f not in SYNTHETIC_COLUMNS, f"survival model uses synthetic feature {f}"
    rep = jload(R / "survival_report.json")
    if rep:
        for f in rep.get("features", []):
            assert f not in SYNTHETIC_COLUMNS, f"reported feature {f} is synthetic"


def test_ranking_uses_no_synthetic_input():
    rep = jload(R / "ev_sensitivity.json")
    if not rep:
        return
    terms = rep.get("terms", {})
    blob = json.dumps(terms).lower()
    for bad in ("health_score", "capacity_utilisation", "support_case", "synthetic"):
        assert bad not in blob, f"expected-value terms mention {bad}"


def test_synthetic_report_declares_itself():
    rep = jload(R / "synthetic_report.json")
    if not rep:
        return
    assert rep.get("provenance") == "SYNTHETIC"
    assert rep.get("seed") == C.RANDOM_SEED, "synthetic layer is not seed-pinned"
    assert "not evidence" in rep.get("warning", "").lower()


# ------------------------------------------------------------- play integrity
def test_synthetic_reason_code_is_never_a_recommended_action():
    import plays
    synthetic_codes = {c for c, prov, _a in plays.PLAYBOOK if prov == "SYNTHETIC"}
    actions = pq("account_actions.parquet")
    if actions.empty:
        return
    chosen = set(actions["primary_reason"].dropna().unique())
    overlap = chosen & synthetic_codes
    assert not overlap, f"SYNTHETIC code(s) became a recommended action: {overlap}"


def test_every_account_resolves_to_one_action_or_explicit_no_action():
    actions = pq("account_actions.parquet")
    if actions.empty:
        return
    assert actions["account_id"].is_unique, "an account has more than one action row"
    assert actions["primary_reason"].notna().all(), "an account has no primary reason"
    assert actions["recommended_action"].notna().all(), "an account has no action"
    no_action = actions[actions["primary_reason"] == "NO_ACTION"]
    assert no_action["primary_evidence"].notna().all(), \
        "a NO_ACTION account does not say why"


def test_every_play_row_carries_provenance_and_detail():
    plays_df = pq("plays.parquet")
    if plays_df.empty:
        return
    assert plays_df["provenance"].notna().all()
    assert plays_df["detail"].notna().all()
    assert (plays_df["detail"].str.len() > 10).all(), "a play has a stub explanation"
    real = plays_df[plays_df["provenance"] != "SYNTHETIC"]
    assert (real["evidence_count"] > 0).all(), \
        "a REAL/DERIVED play fired with no evidence rows"


# ------------------------------------------------------ entity resolution rules
def test_two_distinct_ueis_are_never_merged():
    aw = C.INTERIM / "awards_resolved.parquet"
    if not aw.exists():
        return
    df = pd.read_parquet(aw, columns=["recipient_uei", "recipient_entity_id"])
    df = df.dropna(subset=["recipient_uei", "recipient_entity_id"])
    df = df[df["recipient_uei"].astype(str).str.strip() != ""]
    per_entity = df.groupby("recipient_entity_id")["recipient_uei"].nunique()
    bad = per_entity[per_entity > 1]
    assert bad.empty, f"{len(bad)} entity ids cover more than one UEI: {list(bad.index[:5])}"


def test_match_report_states_its_own_limits():
    rep = jload(R / "match_report.json")
    if not rep:
        return
    rec = rep["recipients"]
    assert rec["merge_count"] == len(rec["merges"]) or rec["merge_count"] >= len(rec["merges"])
    assert "validation" in rep, "match report does not grade itself"
    v = rep["validation"]
    if v.get("available"):
        for k in ("same_parent_precision", "same_parent_recall"):
            assert 0.0 <= v[k] <= 1.0


# --------------------------------------------------------- no time leakage
def test_validation_is_time_based_and_names_its_exclusions():
    rep = jload(R / "survival_report.json")
    if not rep:
        return
    val = rep["validation"]
    assert "no random splits" in val["scheme"].lower()
    assert val["train_cutoff"] == str(C.TRAIN_CUTOFF)
    start, end = val["evaluation_window"]
    assert start > val["train_cutoff"], "evaluation window starts before the cutoff"
    assert end > start
    assert val["excluded_unobservable"], "no unobservable features are documented"


def test_training_is_censored_at_the_cutoff():
    rep = jload(R / "survival_report.json")
    if not rep:
        return
    cen = rep["censoring"]
    assert cen["train_censored_at"] == str(C.TRAIN_CUTOFF)
    assert cen["events"] + cen["right_censored"] == cen["train_gaps"]


def test_model_only_used_if_it_beat_the_baseline():
    rep = jload(R / "survival_report.json")
    if not rep:
        return
    beats = rep["model_beats_baseline"]
    uses = rep["ranking_uses"]
    assert uses == ("survival_model" if beats else "rules_baseline"), \
        "the ranking ignores the honesty rule"
    res = rep["results"]
    if beats:
        assert res["survival_model"]["auc"] > res["rules_baseline"]["auc"]
        assert res["survival_model"]["brier"] < res["rules_baseline"]["brier"]


# --------------------------------------------------------- assumption register
def test_every_assumption_declares_a_source_and_why():
    for a in A.register():
        assert a["id"] and a["name"]
        assert a["kind"] in {"ASSUMPTION", "DERIVED"}
        assert a["source"], f"{a['id']} has no source"
        assert a["why_it_is_an_assumption"], f"{a['id']} does not say why"
        assert a["sensitivity"], f"{a['id']} has no sensitivity note"


def test_commercial_numbers_live_only_in_the_register():
    """Margin and cost used by the ranking must come from the register."""
    rep = jload(R / "ev_sensitivity.json")
    if not rep:
        return
    reg = A.as_dict()
    assert rep["terms"]["gross_margin"]["value"] == reg["A-01"]["value"]
    assert rep["terms"]["engagement_cost"]["value"] == reg["A-02"]["value"]
    assert rep["terms"]["gross_margin"]["provenance"] == "ASSUMPTION"
    assert rep["terms"]["engagement_cost"]["provenance"] == "ASSUMPTION"


def test_ranking_admits_margin_cannot_reorder():
    rep = jload(R / "ev_sensitivity.json")
    if not rep:
        return
    assert rep.get("why_margin_cannot_reorder"), \
        "the report does not disclose that margin cannot reorder the ranking"
    assert rep.get("structural_sensitivity"), \
        "no sensitivity is reported for the choices that CAN reorder"


# ------------------------------------------------------------- scope integrity
def test_every_psc_family_spans_both_coding_eras():
    """The FY2021 recoding trap: a one-era family would fake dormancy."""
    for family, codes in C.PSC_FAMILIES.items():
        eras = {era for _c, (era, _d) in codes.items()}
        if family == "storage_as_a_service":
            continue  # modern-only by definition: it did not exist before
        assert eras == {"legacy", "modern"}, \
            f"PSC family {family} covers only {eras}; it would break across FY2021"


def test_dormancy_is_reported_under_several_scopes():
    rep = jload(R / "install_base_report.json")
    if not rep:
        return
    ds = rep.get("dormancy_sensitivity", {})
    assert ds.get("scopes"), "dormancy is published as a single number"
    assert len(ds["scopes"]) >= 3, "too few dormancy scopes to show the spread"


def test_channel_rule_is_scored_and_not_silently_used():
    rep = jload(R / "channel_rule_report.json")
    if not rep:
        return
    err = rep.get("error_rate_vs_sam_flag", {})
    if err.get("awards_scored"):
        assert "majority_class_accuracy" in err, "no baseline to judge the rule against"
        assert "verdict" in err and err["verdict"]
        if not err["beats_majority_class"]:
            assert "not" in err["verdict"].lower() or "worse" in err["verdict"].lower(), \
                "a failing rule is not described as failing"
    # production must prefer the real flag
    src = rep.get("awards_by_channel_source", {})
    if src:
        real = sum(v for k, v in src.items() if "REAL" in k)
        assert real >= sum(src.values()) * 0.5, \
            "most awards are channel-classified by the rule rather than the real flag"


# ------------------------------------------------------------- output sanity
def test_rates_are_actually_rates():
    rep = jload(R / "install_base_report.json")
    if not rep:
        return
    for path in (("dormancy_rate",), ("coverage", "coverage_rate")):
        v = rep
        for k in path:
            v = (v or {}).get(k)
        if v is not None:
            assert 0.0 <= v <= 1.0, f"{'.'.join(path)} = {v} is not a rate"


def test_app_bundle_is_clean_and_within_limits():
    f = C.APP_DATA / "app_data.json"
    if not f.exists():
        return
    raw = f.read_text(encoding="utf-8")
    assert "NaN" not in raw, "bundle contains bare NaN, which JSON.parse rejects"
    assert "Infinity" not in raw, "bundle contains Infinity"
    data = json.loads(raw)
    assert data["meta"]["framing"], "the bundle does not carry the framing statement"
    assert "not any vendor" in data["meta"]["framing"].lower()
    assert data["provenance"]["fields"], "the bundle carries no provenance table"
    built = ROOT / "app" / "dist" / "index.html"
    if built.exists():
        assert built.stat().st_size < 16_000_000, "published page exceeds the 16MB limit"


def test_account_rows_shown_are_declared():
    f = C.APP_DATA / "app_data.json"
    if not f.exists():
        return
    d = json.loads(f.read_text(encoding="utf-8"))
    meta = d["meta"]
    assert meta["account_rows_shown"] == len(d["accounts"])
    assert meta["account_rows_total"] >= meta["account_rows_shown"], \
        "the bundle claims to show more rows than exist"


def test_subaward_hypothesis_result_is_stated_either_way():
    rep = jload(R / "subaward_report.json")
    if not rep or rep.get("status") == "skipped":
        return
    sc = rep.get("scoring_vs_sam_flag", {})
    if sc.get("available"):
        assert "hypothesis" in sc
        assert "finding" in sc, "the subaward result is not interpreted"
        if not sc["beats_majority_class"]:
            assert "why_we_do_not_just_invert_it" in sc, \
                "a below-baseline signal is not explained"


# ------------------------------------------------------- offshore domain (v3)
# The offshore domain observes the asset and never observes what was paid for
# it; federal procurement observes the spend and infers the asset. These guard
# the separation, and the three traps that silently produce a confident wrong
# answer if they are ever quietly dropped.
def test_the_two_domains_are_never_ranked_against_each_other():
    """The one test that keeps the separation honest."""
    off = pq("offshore_scores.parquet")
    fed = pq("account_scores.parquet")
    if off.empty or fed.empty:
        return
    # no shared key, and no offshore row carrying a federal account identifier
    assert "account_id" not in off.columns, \
        "an offshore row carries a federal account id - the domains are merging"
    assert "structure_key" not in fed.columns, \
        "a federal account row carries an offshore structure key"
    off_keys = set(off["structure_key"].astype(str))
    fed_keys = set(fed[fed.columns[0]].astype(str))
    assert not (off_keys & fed_keys), \
        "a key appears in both domains' score tables"
    # and the ranks are per-domain, each starting at 1
    assert int(off["rank"].min()) == 1, "offshore ranking is not self-contained"


def test_abandon_flag_is_never_a_model_feature():
    """Trap 2: it announces the label, so using it predicts the label from itself."""
    rep = jload(R / "offshore_survival_report.json")
    if not rep:
        return
    feats = [f.lower() for f in rep.get("features", [])]
    for banned in ("abandon", "abandon_flag", "abandon_flag_cx"):
        assert not any(banned in f for f in feats), \
            f"{banned} is being used as a model feature"
    assert "abandon_flag" in rep.get("excluded_unobservable", {}), \
        "abandon_flag is not declared as excluded"
    assert rep.get("abandon_flag_check"), \
        "abandon_flag is excluded but never used as the validation check either"


def test_left_truncation_is_actually_applied():
    """Trap 6: pre-window structures enter at the age they reached, not at zero."""
    rep = jload(R / "offshore_profile.json")
    life = pq("offshore_lifetimes.parquet")
    if not rep or life.empty:
        return
    lt = rep.get("left_truncation", {})
    assert lt.get("applied") is True, "left truncation is not applied"
    assert lt.get("left_truncated_rows", 0) > 0, \
        "no row is left-truncated, which cannot be true for a 1940s-2020s base"
    assert (life["entry_age_years"] >= 0).all(), "negative entry age"
    assert (life["exit_age_years"] > life["entry_age_years"]).all(), \
        "a structure exits at or before it enters"
    # and it is carried into the fit, not just into the table
    sv = jload(R / "offshore_survival_report.json")
    if sv:
        assert sv["left_truncation"]["carried_into_fit"] is True, \
            "truncation is in the table but not in the model"
        assert sv["left_truncation"]["train_rows_left_truncated"] > 0


def test_storm_removals_are_handled_explicitly_not_silently():
    """Trap 4: this data cannot separate storms, so it must say so, not imply it did."""
    for name in ("offshore_profile.json", "offshore_survival_report.json"):
        rep = jload(R / name)
        if not rep:
            continue
        storms = rep.get("storms", {})
        assert storms, f"{name} says nothing about storm losses"
        assert storms.get("policy"), f"{name} has no storm policy"
        assert storms.get("note"), f"{name} states no storm caveat"
        if storms.get("separable") is False:
            assert "cannot" in storms["note"].lower(), \
                "storms are declared inseparable without saying so plainly"


def test_offshore_never_claims_a_price_it_cannot_observe():
    """The domain observes the asset, never what was paid for it."""
    off = pq("offshore_scores.parquet")
    if off.empty:
        return
    for c in off.columns:
        assert not any(w in c.lower() for w in
                       ("obligated", "award_amount", "price", "revenue", "margin")), \
            f"offshore_scores carries {c}, but this domain observes no money at all"


def test_offshore_model_only_used_if_it_beat_a_real_baseline():
    """The baseline must not be a strawman: age alone is INVERTED in this domain."""
    rep = jload(R / "offshore_survival_report.json")
    if not rep:
        return
    disc = rep["discrimination"]
    if disc.get("age_is_inverted"):
        assert disc["baseline_direction"] == "youngest_first", \
            "age is inverted but the baseline still ranks oldest first"
        assert disc["auc_baseline"] > 0.5, \
            "the baseline was left pointing the wrong way, so beating it means nothing"
        assert disc.get("direction_evidence"), \
            "the baseline's direction is asserted without the windows that establish it"
    if not rep["decision"]["use_model"]:
        assert disc["auc_model"] <= disc["auc_baseline"], \
            "the model was dropped even though it won"
        assert "age_baseline" in rep["decision"]["ranking_uses"], \
            "the model lost but the ranking does not say it kept the baseline"


def test_offshore_declares_its_install_date_measurement_error():
    """69% of install dates are 01-JAN placeholders - the time scale itself is fuzzy."""
    rep = jload(R / "offshore_profile.json")
    if not rep:
        return
    q = rep.get("install_date_quality", {})
    assert q.get("imputed_rate", 0) > 0, "imputed install dates are not measured"
    assert q.get("by_decade"), "the imputation is not broken down by decade"
    assert q.get("note"), "the measurement error is not stated"


def test_complex_attributes_are_marked_as_shared_not_measured():
    """Masters is per-complex, Structures is per-structure: the join is 1:many."""
    rep = jload(R / "offshore_profile.json")
    stru = pq("offshore_structures.parquet")
    if not rep or stru.empty:
        return
    shared = rep.get("shared_covariates", {}).get("columns", [])
    assert shared, "complex-level covariates are not declared as shared"
    for c in shared:
        assert c.endswith("_cx"), f"{c} is a shared covariate but is not marked _cx"
    assert "complex_is_shared" in stru.columns, \
        "nothing records which structures share a complex"
    assert int(stru["complex_is_shared"].sum()) > 0


# ------------------------------------------------------------------- runner
def _run_standalone() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"  FAIL  {name}\n          {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print("\nfailures:")
        for n, e in failed:
            print(f"  - {n}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    print(f"invariant checks for {ROOT.name}\n")
    sys.exit(_run_standalone())
