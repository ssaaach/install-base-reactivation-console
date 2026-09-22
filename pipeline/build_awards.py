"""Step 1b - Flatten the raw USAspending download cache into one award table.

Input : data/raw/*.zip  (written by fetch_usaspending.py)
Output: data/interim/awards_raw.parquet
        data/interim/subawards_raw.parquet
        reports/ingest_report.json  - missing-field rates by year, dedup accounting

The PSC pull and the NAICS pull overlap, so awards are deduplicated on
contract_award_unique_key, keeping the most recently modified copy and retaining
which filters each award matched.

Every column here is REAL: it is USAspending's value, unmodified. Derived columns
are added later, in install_base.py, and are tagged DERIVED there.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

OUT = C.INTERIM / "awards_raw.parquet"
OUT_SUB = C.INTERIM / "subawards_raw.parquet"
REPORT = C.REPORTS / "ingest_report.json"

KEY = "contract_award_unique_key"

# The 286-column download is mostly FAR-clause detail we do not model. These are
# the columns the install-base model, entity resolution and the plays actually use.
PRIME_COLS = [
    KEY, "award_id_piid", "parent_award_id_piid", "parent_award_agency_name",
    "total_obligated_amount", "total_outlayed_amount",
    "current_total_value_of_award", "potential_total_value_of_award",
    "award_base_action_date", "award_base_action_date_fiscal_year",
    "award_latest_action_date", "period_of_performance_start_date",
    "period_of_performance_current_end_date", "period_of_performance_potential_end_date",
    "awarding_agency_code", "awarding_agency_name",
    "awarding_sub_agency_code", "awarding_sub_agency_name",
    "awarding_office_code", "awarding_office_name",
    "funding_agency_name", "funding_sub_agency_name",
    "recipient_uei", "recipient_name", "recipient_name_raw",
    "recipient_doing_business_as_name",
    "recipient_parent_uei", "recipient_parent_name", "recipient_parent_name_raw",
    "recipient_state_code", "recipient_city_name",
    "primary_place_of_performance_state_code", "primary_place_of_performance_city_name",
    "award_or_idv_flag", "award_type_code", "award_type", "idv_type",
    "type_of_contract_pricing", "prime_award_base_transaction_description",
    "product_or_service_code", "product_or_service_code_description",
    "naics_code", "naics_description",
    "extent_competed", "number_of_offers_received", "type_of_set_aside",
    "contracting_officers_determination_of_business_size",
    "manufacturer_of_goods", "for_profit_organization", "nonprofit_organization",
    "number_of_actions", "usaspending_permalink", "last_modified_date",
]

SUB_COLS = [
    "prime_award_unique_key", "prime_award_piid", "subaward_number",
    "subaward_amount", "subaward_action_date",
    "prime_awardee_uei", "prime_awardee_name",
    "subawardee_uei", "subawardee_name", "subawardee_parent_name",
    "prime_award_awarding_sub_agency_name", "prime_award_awarding_office_name",
    "prime_award_product_or_service_code", "prime_award_naics_code",
    "subaward_description",
]

DATE_COLS = ["award_base_action_date", "award_latest_action_date",
             "period_of_performance_start_date",
             "period_of_performance_current_end_date",
             "period_of_performance_potential_end_date", "last_modified_date"]
NUM_COLS = ["total_obligated_amount", "total_outlayed_amount",
            "current_total_value_of_award", "potential_total_value_of_award",
            "number_of_actions", "number_of_offers_received"]


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def read_member(z: zipfile.ZipFile, name: str, wanted: list) -> pd.DataFrame:
    raw = z.read(name)
    head = pd.read_csv(io.BytesIO(raw), nrows=0, low_memory=False)
    use = [c for c in wanted if c in head.columns]
    missing = [c for c in wanted if c not in head.columns]
    if missing:
        log(f"    note: {len(missing)} expected column(s) absent: {missing[:6]}")
    return pd.read_csv(io.BytesIO(raw), usecols=use, low_memory=False,
                       dtype={"recipient_uei": "string", "recipient_parent_uei": "string",
                              "naics_code": "string", "product_or_service_code": "string",
                              "awarding_sub_agency_code": "string",
                              "awarding_office_code": "string"})


def load() -> tuple:
    zips = sorted(C.RAW.glob("*.zip"))
    if not zips:
        raise SystemExit("no raw downloads found - run fetch_usaspending.py first")
    manifest = json.loads(C.MANIFEST.read_text()) if C.MANIFEST.exists() else {}
    log(f"reading {len(zips)} downloads")

    primes, subs, parts = [], [], []
    for zp in zips:
        kind = manifest.get("downloads", {}).get(zp.name, {}).get("kind") \
            or zp.name.split("_")[0]
        with zipfile.ZipFile(zp) as z:
            for n in z.namelist():
                if not n.lower().endswith(".csv"):
                    continue
                if "PrimeAwardSummaries" in n:
                    df = read_member(z, n, PRIME_COLS)
                    df["_filter_kind"] = kind
                    primes.append(df)
                    parts.append({"zip": zp.name, "member": n, "kind": kind,
                                  "rows": int(len(df))})
                    log(f"  {zp.name} :: prime {len(df):,} rows")
                elif "Subawards" in n:
                    df = read_member(z, n, SUB_COLS)
                    subs.append(df)
                    log(f"  {zp.name} :: sub   {len(df):,} rows")
    prime = pd.concat(primes, ignore_index=True) if primes else pd.DataFrame()
    sub = pd.concat(subs, ignore_index=True) if subs else pd.DataFrame()
    return prime, sub, parts, manifest


def dedupe(df: pd.DataFrame, stats: dict) -> pd.DataFrame:
    before = len(df)
    # Which pulls found this award. An award matching both the PSC filter and the
    # NAICS filter is storage hardware by two independent definitions.
    matched = (df.groupby(KEY)["_filter_kind"]
               .agg(lambda s: ";".join(sorted(set(s)))).rename("matched_filters"))
    d = df.copy()
    d["_lm"] = pd.to_datetime(d["last_modified_date"], errors="coerce", format="mixed")
    d = (d.sort_values("_lm", na_position="first")
           .drop_duplicates(subset=[KEY], keep="last")
           .drop(columns=["_lm", "_filter_kind"]))
    d = d.merge(matched, left_on=KEY, right_index=True, how="left")
    log(f"deduped {before:,} -> {len(d):,} distinct awards "
        f"({1 - len(d) / max(before, 1):.1%} were overlap between the PSC and NAICS pulls)")
    stats["raw_rows"] = int(before)
    stats["distinct_awards"] = int(len(d))
    stats["pull_overlap_rate"] = round(1 - len(d) / max(before, 1), 4)
    stats["matched_filter_counts"] = {
        str(k): int(v) for k, v in d["matched_filters"].value_counts().items()}
    return d


def coerce(df: pd.DataFrame) -> pd.DataFrame:
    for c in DATE_COLS:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", format="mixed")
    for c in NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("recipient_uei", "recipient_parent_uei", "recipient_name",
              "recipient_name_raw", "recipient_parent_name",
              "awarding_sub_agency_name", "awarding_office_name",
              "product_or_service_code", "naics_code", "manufacturer_of_goods"):
        if c in df.columns:
            df[c] = df[c].astype("string").str.strip()

    # Canonical names the rest of the pipeline uses.
    df["award_amount"] = df["total_obligated_amount"]
    df["base_obligation_date"] = df["award_base_action_date"]
    df["start_date"] = df["period_of_performance_start_date"]
    df["end_date"] = df["period_of_performance_current_end_date"]
    df["psc_code"] = df["product_or_service_code"]
    df["psc_description"] = df["product_or_service_code_description"]
    df["psc_role"] = df["psc_code"].map(C.PSC_ROLE).astype("string")
    df["base_fy"] = pd.to_numeric(df["award_base_action_date_fiscal_year"],
                                  errors="coerce").astype("Int64")
    return df


def missingness(df: pd.DataFrame) -> dict:
    cols = ["recipient_uei", "recipient_parent_uei", "recipient_name",
            "awarding_sub_agency_code", "awarding_office_code", "awarding_office_name",
            "award_amount", "start_date", "end_date", "base_obligation_date",
            "prime_award_base_transaction_description", "naics_code", "psc_code",
            "primary_place_of_performance_state_code", "manufacturer_of_goods",
            "extent_competed", "number_of_offers_received", "total_outlayed_amount"]
    cols = [c for c in cols if c in df.columns]
    by_fy = {}
    for fy, g in df.dropna(subset=["base_fy"]).groupby("base_fy"):
        by_fy[int(fy)] = {"awards": int(len(g)),
                          **{c: round(float(g[c].isna().mean()), 4) for c in cols}}
    return {"by_base_fy": dict(sorted(by_fy.items())),
            "overall": {c: round(float(df[c].isna().mean()), 4) for c in cols}}


def main() -> None:
    prime, sub, parts, manifest = load()
    stats = {"downloads": parts, "manifest_totals": manifest.get("totals", {})}
    prime = dedupe(prime, stats)
    prime = coerce(prime)

    stats["missingness"] = missingness(prime)
    stats["psc_role_counts"] = {str(k): int(v) for k, v in
                                prime["psc_role"].value_counts(dropna=False).items()}
    stats["psc_code_counts"] = {str(k): int(v) for k, v in
                                prime["psc_code"].value_counts().head(30).items()}
    stats["award_or_idv_counts"] = {str(k): int(v) for k, v in
                                    prime["award_or_idv_flag"].value_counts(dropna=False).items()}
    stats["manufacturer_flag_counts"] = {str(k): int(v) for k, v in
                                         prime["manufacturer_of_goods"].value_counts(dropna=False).items()}
    stats["date_range"] = {
        "base_action_min": str(prime["base_obligation_date"].min()),
        "base_action_max": str(prime["base_obligation_date"].max()),
        "pop_end_max": str(prime["end_date"].max()),
    }
    stats["total_obligated_usd"] = float(prime["award_amount"].sum())
    stats["distinct_offices"] = int(prime["awarding_office_code"].nunique())
    stats["distinct_sub_agencies"] = int(prime["awarding_sub_agency_code"].nunique())
    stats["distinct_recipient_uei"] = int(prime["recipient_uei"].nunique())
    stats["subaward_rows"] = int(len(sub))
    stats["generated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    stats["note"] = ("Awards are counted once, in the fiscal year they were signed "
                     "(date_type=date_signed). Overlap above is between the PSC pull "
                     "and the NAICS pull, not an entity-resolution duplicate rate; "
                     "for that see reports/match_report.json.")

    prime.to_parquet(OUT, index=False)
    if len(sub):
        sub.to_parquet(OUT_SUB, index=False)
    REPORT.write_text(json.dumps(stats, indent=2, default=str))

    log(f"awards    -> {OUT}  ({len(prime):,} rows, {OUT.stat().st_size / 1e6:.1f} MB)")
    if len(sub):
        log(f"subawards -> {OUT_SUB} ({len(sub):,} rows)")
    log(f"report    -> {REPORT}")
    log(f"distinct: {stats['distinct_offices']:,} awarding offices, "
        f"{stats['distinct_sub_agencies']:,} sub-agencies, "
        f"{stats['distinct_recipient_uei']:,} recipient UEIs")
    log(f"UEI missing: {stats['missingness']['overall'].get('recipient_uei', 0):.1%}  |  "
        f"office missing: {stats['missingness']['overall'].get('awarding_office_code', 0):.1%}")


if __name__ == "__main__":
    main()
