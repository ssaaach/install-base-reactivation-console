"""Step 9 - Export one compact JSON bundle for the console.

Input : everything in data/processed and reports/
Output: app/data/app_data.json

The console is a static page, so this is the only contract between the pipeline
and the interface. Aggregates are computed over ALL accounts; only the detailed
account rows are capped, and the cap is reported in the bundle so the page can
say so rather than quietly showing a subset.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assumptions as A  # noqa: E402
import config as C  # noqa: E402
import provenance as PROV  # noqa: E402

P = C.PROCESSED
R = C.REPORTS
OUT = C.APP_DATA / "app_data.json"

MAX_ACCOUNT_ROWS = 1500
MAX_EVIDENCE_ROWS = 400


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def jload(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else (default if default is not None else {})


def pq(name: str) -> pd.DataFrame:
    p = P / name
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def clean(o):
    """JSON-safe: no NaN, no numpy scalars, no Timestamps."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else round(o, 6)
    if isinstance(o, (pd.Timestamp, _dt.date, _dt.datetime)):
        return str(o)[:10]
    if o is pd.NaT or o is None:
        return None
    if hasattr(o, "item"):
        try:
            return clean(o.item())
        except Exception:
            return str(o)
    if isinstance(o, (int, str, bool)):
        return o
    return str(o)


def records(df: pd.DataFrame, cols: list, limit: int | None = None) -> list:
    if df.empty:
        return []
    use = [c for c in cols if c in df.columns]
    d = df[use]
    if limit:
        d = d.head(limit)
    return clean(d.to_dict(orient="records"))


def main() -> None:
    accounts = pq("accounts.parquet")
    contracts = pq("contracts.parquet")
    coverage = pq("coverage.parquet")
    renewals = pq("renewals.parquet")
    whitespace = pq("whitespace.parquet")
    vendor_net = pq("vendor_network.parquet")
    sub_flows = pq("subaward_flows.parquet")
    plays = pq("plays.parquet")
    actions = pq("account_actions.parquet")
    ranking = pq("ev_ranking.parquet")
    health = pq("health_synthetic.parquet")
    telemetry = pq("telemetry_synthetic.parquet")

    ib = jload(R / "install_base_report.json")
    mr = jload(R / "match_report.json")
    ing = jload(R / "ingest_report.json")
    sv = jload(R / "survival_report.json")
    ev = jload(R / "ev_sensitivity.json")
    pl = jload(R / "plays_report.json")
    ch = jload(R / "channel_rule_report.json")
    syn = jload(R / "synthetic_report.json")
    sa = jload(R / "subaward_report.json")
    coh = jload(P / "cohorts.json")
    bb = jload(P / "backblaze_hazard.json")
    sec = jload(P / "sec_benchmarks.json")
    man = jload(C.MANIFEST)

    log(f"accounts={len(accounts):,} contracts={len(contracts):,} "
        f"ranking={len(ranking):,} plays={len(plays):,}")

    # ---------------- the ranked account table the console leads with
    tbl = ranking if len(ranking) else accounts
    if len(ranking) and len(health):
        tbl = tbl.merge(health[["account_id", "health_score", "health_band"]],
                        on="account_id", how="left")
    acct_cols = [
        "ev_rank", "account_id", "account_name", "sub_agency", "department",
        "segment", "region", "size_band", "cohort_fy", "total_obligated",
        "award_count", "dormant", "months_since_last_award", "coverage_rate",
        "uncovered_assets", "expiring_contracts", "expiring_value",
        "whitespace_count", "channel_shift", "p_purchase", "p_purchase_365d_model",
        "p_purchase_365d_rules", "expected_award_value", "expected_value",
        "gross_opportunity", "ev_positive", "primary_reason", "recommended_action",
        "all_reason_codes", "reason_count", "evidence_rows", "primary_evidence",
        "primary_provenance", "health_score", "health_band",
    ]
    account_rows = records(tbl, acct_cols, MAX_ACCOUNT_ROWS)

    # ---------------- evidence: the rows that triggered each play
    ev_rows = []
    if len(plays):
        top_ids = set(tbl.head(MAX_ACCOUNT_ROWS)["account_id"]) if len(tbl) else set()
        sel = plays[plays["account_id"].isin(top_ids)] if top_ids else plays
        ev_rows = records(
            sel.sort_values(["account_id", "priority"]),
            ["account_id", "reason_code", "provenance", "detail", "evidence_ids",
             "evidence_count", "trigger_value"], 12000)

    # ---------------- aggregates over ALL accounts, not just the shown rows
    def vc(df, col, n=25):
        return clean(df[col].value_counts().head(n).to_dict()) if col in df.columns else {}

    agg = {}
    if len(accounts):
        agg = {
            "by_segment": vc(accounts, "segment"),
            "by_region": vc(accounts, "region"),
            "by_size_band": vc(accounts, "size_band"),
            "by_cohort_fy": clean(accounts["cohort_fy"].value_counts().sort_index().to_dict()),
            "top_departments": clean(
                accounts.groupby("department")["total_obligated"].sum()
                .sort_values(ascending=False).head(15).to_dict()),
            "dormancy_by_threshold": clean({
                f"{m}m": int(accounts[f"dormant_{m}m"].sum())
                for m in (12, 18, 36) if f"dormant_{m}m" in accounts.columns}),
            "dormancy_sensitivity": clean(ib.get("dormancy_sensitivity", {})),
        }

    # obligations by fiscal year and PSC era - the structural break, drawn
    era_series = []
    if len(contracts) and "psc_era" in contracts.columns:
        g = (contracts.dropna(subset=["award_fy"])
             .groupby(["award_fy", "psc_era"])
             .agg(awards=("contract_award_unique_key", "nunique"),
                  obligated=("award_amount", "sum")).reset_index())
        era_series = clean(g.to_dict(orient="records"))

    role_series = []
    if len(contracts):
        g = (contracts.dropna(subset=["award_fy"])
             .groupby(["award_fy", "psc_role"])
             .agg(awards=("contract_award_unique_key", "nunique"),
                  obligated=("award_amount", "sum")).reset_index())
        role_series = clean(g.to_dict(orient="records"))

    # ---------------- coverage page
    cov_page = {"summary": clean(ib.get("coverage", {}))}
    if len(coverage):
        cov_page["by_sub_agency"] = clean(
            coverage.groupby("sub_agency")
            .agg(hardware_awards=("contract_award_unique_key", "nunique"),
                 coverage_rate=("covered", "mean"),
                 uncovered_value=("award_amount", lambda s: float(s.sum())))
            .sort_values("hardware_awards", ascending=False).head(30)
            .reset_index().to_dict(orient="records"))
        unc = coverage[coverage["uncovered"]].nlargest(MAX_EVIDENCE_ROWS, "award_amount")
        cov_page["uncovered_top"] = records(unc, [
            "award_id_piid", "account_name", "sub_agency", "segment", "region",
            "recipient_canonical", "channel", "psc_code", "psc_description", "psc_era",
            "award_amount", "start_date", "end_date", "asset_age_years",
            "usaspending_permalink"])

    # ---------------- renewals page
    ren_page = {"summary": clean(ib.get("renewals", {}))}
    if len(renewals):
        ren_page["rows"] = records(
            renewals.nlargest(MAX_EVIDENCE_ROWS, "award_amount"),
            ["award_id_piid", "account_name", "sub_agency", "segment",
             "recipient_canonical", "channel", "psc_code", "award_amount",
             "end_date", "days_to_expiry", "coterm_cluster_id", "coterm_cluster_size",
             "in_coterm_cluster", "usaspending_permalink"])

    # ---------------- whitespace
    ws_page = {"summary": clean(ib.get("whitespace", {}))}
    if len(whitespace):
        ws_page["rows"] = records(
            whitespace.nlargest(MAX_EVIDENCE_ROWS, "peer_adoption_rate"),
            ["account_id", "peer_group", "psc_code", "psc_description",
             "peer_adoption_rate", "peer_adopters"])

    bundle = {
        "meta": {
            "title": "Install Base Reactivation Console",
            "version": "2.0",
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "as_of": str(C.AS_OF),
            "framing": (
                "This dataset is US federal enterprise-storage procurement, used as a "
                "PROXY for install-base dynamics. It is NOT any vendor's install base "
                "and must never be described as one."),
            "disclaimer": (
                "Accounts here are federal awarding offices, not customers of any "
                "company. Every commercial number is an assumption, listed in the "
                "assumption register."),
            "account_grain": ib.get("account_grain", "awarding office within sub-agency"),
            "train_cutoff": str(C.TRAIN_CUTOFF),
            "horizon_days": C.HORIZON_DAYS,
            "account_rows_shown": len(account_rows),
            "account_rows_total": int(len(tbl)),
            "sources": [
                {"name": "USAspending.gov", "kind": "REAL",
                 "url": "https://api.usaspending.gov",
                 "detail": "prime contract awards, /api/v2/download/awards/",
                 "licence": "US Government public domain (17 U.S.C. 105)",
                 "retrieved": man.get("generated_at", "")[:10]},
                {"name": "Backblaze Drive Stats", "kind": "REAL",
                 "url": "https://www.backblaze.com/cloud-storage/resources/hard-drive-test-data",
                 "detail": (f"{bb.get('total_drive_days', 0):,} drive-days, "
                            f"{bb.get('total_failures', 0):,} failures, "
                            f"quarter {bb.get('quarter', 'n/a')}"),
                 "licence": "published by Backblaze with attribution",
                 "retrieved": str(bb.get("retrieved_at", ""))[:10]},
                {"name": sec.get("company", "SEC EDGAR filer"), "kind": "REAL (benchmark only)",
                 "url": "https://www.sec.gov/edgar",
                 "detail": (f"CIK {sec.get('cik', '')}, SIC {sec.get('sic', '')}; "
                            "gross margin, subscription ARR growth, net dollar retention"),
                 "licence": "SEC EDGAR public records",
                 "retrieved": str(sec.get("retrieved_at", ""))[:10]},
            ],
            "manifest_totals": clean(man.get("totals", {})),
            "scope": clean(man.get("scope", {})),
        },
        "kpis": {
            "accounts": int(ib.get("accounts", len(accounts))),
            "awards_in_scope": int(ib.get("awards_in_scope", len(contracts))),
            "total_obligated_usd": float(ib.get("total_obligated_usd", 0) or 0),
            "dormancy_rate": ib.get("dormancy_rate"),
            "dormant_accounts": ib.get("dormant_accounts"),
            "coverage_rate": (ib.get("coverage") or {}).get("coverage_rate"),
            "uncovered_assets": (ib.get("coverage") or {}).get("uncovered"),
            "uncovered_obligations_usd": (ib.get("coverage") or {}).get("uncovered_obligations_usd"),
            "expiring_contracts": (ib.get("renewals") or {}).get("expiring_contracts"),
            "expiring_value_usd": (ib.get("renewals") or {}).get("expiring_value_usd"),
            "coterm_clusters": (ib.get("renewals") or {}).get("coterm_clusters"),
            "accounts_no_action": pl.get("accounts_no_action"),
            "positive_ev_accounts": ev.get("positive_ev_accounts"),
            "distinct_vendors": int(mr.get("recipients", {}).get("entities_after", 0)),
        },
        "accounts": account_rows,
        "account_columns": [c for c in acct_cols if len(tbl) and c in tbl.columns],
        "aggregates": agg,
        "evidence": ev_rows,
        "plays": {
            "playbook": clean(pl.get("playbook", [])),
            "reason_counts": clean(pl.get("reason_counts", {})),
            "provenance_counts": clean(pl.get("provenance_counts", {})),
            "action_counts": clean(pl.get("recommended_action_counts", {})),
            "accounts_no_action": pl.get("accounts_no_action"),
            "selectivity": clean(pl.get("selectivity", {})),
            "selectivity_note": pl.get("selectivity_note"),
            "refresh_risk_threshold_years": pl.get("refresh_risk_threshold_years"),
            "refresh_risk_basis": pl.get("refresh_risk_basis"),
            "rule": pl.get("rule"),
        },
        "coverage": cov_page,
        "renewals": ren_page,
        "whitespace": ws_page,
        "cohorts": clean(coh),
        "series": {"by_fy_and_era": era_series, "by_fy_and_role": role_series},
        "data_quality": {
            "match_report": clean({
                k: v for k, v in mr.get("recipients", {}).items()
                if k not in ("merges", "ambiguous_requiring_review", "unresolved",
                             "same_parent_candidates", "name_uei_conflicts")}),
            "merges_sample": clean(mr.get("recipients", {}).get("merges", [])[:60]),
            "ambiguous_sample": clean(
                mr.get("recipients", {}).get("ambiguous_requiring_review", [])[:60]),
            "unresolved_sample": clean(mr.get("recipients", {}).get("unresolved", [])[:60]),
            "same_parent_sample": clean(
                mr.get("recipients", {}).get("same_parent_candidates", [])[:60]),
            "organisations": clean({
                k: v for k, v in mr.get("organisations", {}).items()
                if k not in ("office_name_variants", "sub_agency_name_variants")}),
            "office_name_variants": clean(
                mr.get("organisations", {}).get("office_name_variants", [])[:40]),
            "validation": clean(mr.get("validation", {})),
            "method": clean(mr.get("method", {})),
            "missingness": clean(ing.get("missingness", {})),
            "pull_overlap_rate": ing.get("pull_overlap_rate"),
            "raw_rows": ing.get("raw_rows"),
            "distinct_awards": ing.get("distinct_awards"),
            "psc_era_transition": clean(ib.get("psc_era_transition", {})),
            "channel_rule": clean(ch),
        },
        "model": {
            "survival": clean({k: v for k, v in sv.items() if k != "coefficients"}),
            "coefficients": clean(sv.get("coefficients", [])),
            "ev_sensitivity": clean({k: v for k, v in ev.items()
                                     if k != "assumption_register"}),
        },
        "provenance": {
            "tags": {
                "REAL": "sourced from a named public dataset, unmodified",
                "DERIVED": "computed from REAL fields only",
                "SYNTHETIC": "generated, because no public equivalent exists",
                "ASSUMPTION": "a stated commercial input, not a measurement",
            },
            "counts": PROV.counts(),
            "fields": PROV.table(),
        },
        "assumptions": clean(A.register()),
        "synthetic": {
            "report": clean(syn),
            "warning": syn.get("warning", ""),
            "health_distribution": (clean(health["health_band"].value_counts().to_dict())
                                    if len(health) else {}),
            "telemetry_sample": (clean(
                telemetry[telemetry["account_id"].isin(
                    telemetry["account_id"].drop_duplicates().head(40))]
                .to_dict(orient="records")) if len(telemetry) else []),
        },
        "hazard": clean(bb),
        "benchmarks": clean(sec),
        "vendors": {
            "subaward_report": clean({k: v for k, v in sa.items() if k != "top_flows"}),
            "top_flows": records(
                vendor_net.nlargest(120, "subaward_total"),
                ["prime_awardee_name", "subawardee_name", "subaward_total",
                 "subawards", "subawardee_is_mfg", "first_seen", "last_seen"]),
            "flows_sample": records(
                sub_flows.nlargest(120, "subaward_total"),
                ["prime_piid", "prime_name", "prime_is_mfg", "awarding_office",
                 "prime_obligated", "subaward_total", "passthrough_ratio",
                 "distinct_subawardees", "mfg_share_of_labelled_subawards",
                 "flow_channel"]),
            "passthrough_hist": (clean(
                pd.cut(sub_flows["passthrough_ratio"].dropna(),
                       bins=[0, .25, .5, .75, 1.0, 1.5, 3.0], right=False)
                .value_counts().sort_index()
                .rename(lambda i: f"{i.left:g}-{i.right:g}").to_dict())
                if len(sub_flows) else {}),
        },
    }

    OUT.write_text(json.dumps(bundle, separators=(",", ":")))
    size = OUT.stat().st_size
    log(f"app data -> {OUT} ({size / 1e6:.2f} MB)")
    if size > 14e6:
        log("WARNING bundle is close to the 16 MB artifact limit; reduce MAX_ACCOUNT_ROWS")
    log(f"  {len(account_rows):,} account rows of {len(tbl):,}; "
        f"{len(ev_rows):,} evidence rows; {len(PROV.table())} provenance fields")


if __name__ == "__main__":
    main()
