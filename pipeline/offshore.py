"""Step 6b - The offshore structure lifetime table.

Input : data/raw/bsee/...                  (fetch_bsee.py)
Output: data/processed/offshore_structures.parquet
        data/processed/offshore_lifetimes.parquet
        reports/offshore_profile.json

THE UNIT IS THE STRUCTURE. Masters is one row per COMPLEX and Structures is one
row per STRUCTURE within it, so water depth, lease and operator are complex
attributes carried onto a structure-level event, one-to-many. Every such column
is suffixed _cx and listed in `shared_covariates` in the report, so nothing
downstream can mistake a shared value for an independent measurement.

WHAT IS OBSERVED, AND WHAT IS NOT

    entry   Install Date   recorded, but 69.2% of them are exactly 01-JAN, and
                           for structures older than ~1990 that is a placeholder
                           standing in for a year, not a day
    event   Removal Date   recorded on 81.4% of structures; the other 18.6% are
                           still standing and are right-censored at config.AS_OF

That 81.4% is worth pausing on: this file is mostly a record of structures that
have already gone. The live Gulf install base inside it is the censored group.

LEFT TRUNCATION, NOT DELETION (trap 6). Removals are recorded from 1973, but
1973 and 1974 carry one and six of them against a standing base of roughly two
thousand, so the series only becomes believable from config.OFFSHORE_OBS_START.
Structures removed before that date are not observed at all and are dropped;
structures installed before it and still standing then are LEFT-TRUNCATED -
they enter the risk set at the age they had already reached. Treating those as
born at the threshold would compress the early hazard and flatter every
survival estimate built on top.

Run:
    python pipeline/offshore.py
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

RAW = C.RAW / "bsee"
OUT_STRU = C.PROCESSED / "offshore_structures.parquet"
OUT_LIFE = C.PROCESSED / "offshore_lifetimes.parquet"
REPORT = C.REPORTS / "offshore_profile.json"

AS_OF = pd.Timestamp(C.AS_OF)
OBS_START = pd.Timestamp(C.OFFSHORE_OBS_START)
DAYS_YEAR = 365.25

# Complex-level columns, carried onto structure rows one-to-many.
SHARED = ["water_depth_cx", "distance_to_shore_cx", "lease_number_cx",
          "abandon_flag_cx", "production_flag_cx", "oil_prod_flag_cx",
          "gas_prod_flag_cx", "rig_count_cx", "crane_count_cx", "bed_count_cx",
          "field_name_code_cx", "maj_cmplx_flag_cx"]


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


# ------------------------------------------------------------------ parsing
def read_fixed(path: Path, layout: dict) -> pd.DataFrame:
    """1-indexed start position and length, exactly as the layout page states."""
    rows = []
    with open(path, encoding="latin-1") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            rows.append({k: line[s - 1:s - 1 + n].strip() for k, (s, n) in layout.items()})
    return pd.DataFrame(rows)


def read_delimited(path: Path, cols: list) -> pd.DataFrame:
    with open(path, encoding="latin-1", newline="") as f:
        rows = [r for r in csv.reader(f) if r and any(x.strip() for x in r)]
    bad = [len(r) for r in rows if len(r) != len(cols)]
    if bad:
        raise SystemExit(f"{path.name}: {len(bad)} rows do not have {len(cols)} fields")
    return pd.DataFrame([[x.strip() for x in r] for r in rows], columns=cols)


BSEE_DATE = re.compile(r"^\d{2}-[A-Z]{3}-\d{4}$")


def to_date(s: pd.Series) -> pd.Series:
    """BSEE writes DD-MON-YYYY. Anything else becomes NaT rather than a guess."""
    clean = s.where(s.astype(str).str.match(BSEE_DATE), other=None)
    return pd.to_datetime(clean, format="%d-%b-%Y", errors="coerce")


def to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace("", np.nan), errors="coerce")


# ------------------------------------------------------------------ assembly
def build() -> tuple:
    stru = read_fixed(RAW / "platstrufixed" / "platstru.DAT", C.BSEE_LAYOUT_STRU)
    mast = read_fixed(RAW / "platmastfixed" / "platmast.DAT", C.BSEE_LAYOUT_MAST)
    rem = read_delimited(RAW / "platstruremdelimit" / "platstruremdelimit.txt",
                         C.BSEE_COLS_REM)
    log(f"parsed structures={len(stru):,} masters={len(mast):,} removals={len(rem):,}")

    # --- the join key is one-to-many by construction; prove it rather than assume
    if mast["complex_id"].duplicated().any():
        raise SystemExit("Masters is not unique on complex_id - the unit of "
                         "analysis assumption in config is wrong, stop here")

    stru["install_date_raw"] = stru["install_date"]
    stru["install_date"] = to_date(stru["install_date"])
    stru["removal_date"] = to_date(stru["removal_date"])
    stru["structure_key"] = stru["complex_id"] + "-" + stru["structure_number"]

    for c in ("deck_count", "slot_count", "slot_drill_count",
              "satellite_completion_count", "underwater_completion_count"):
        stru[c] = to_num(stru[c])

    # 01-JAN is the tell for an install date imputed to the year.
    stru["install_imputed"] = stru["install_date_raw"].str.startswith(
        C.OFFSHORE_IMPUTED_INSTALL_MARK).fillna(False)

    mast["water_depth"] = to_num(mast["water_depth"])
    mast["distance_to_shore"] = to_num(mast["distance_to_shore"])
    for c in ("rig_count", "crane_count", "bed_count"):
        mast[c] = to_num(mast[c])

    keep = ["complex_id", "water_depth", "distance_to_shore", "lease_number",
            "abandon_flag", "production_flag", "oil_prod_flag", "gas_prod_flag",
            "rig_count", "crane_count", "bed_count", "field_name_code",
            "maj_cmplx_flag"]
    cx = mast[keep].rename(columns={c: f"{c}_cx" for c in keep if c != "complex_id"})

    df = stru.merge(cx, on="complex_id", how="left", validate="many_to_one")
    df["complex_structure_count"] = df.groupby("complex_id")["structure_key"].transform("size")
    df["complex_is_shared"] = df["complex_structure_count"] > 1

    # depth stratum (trap 5) - never pooled silently
    df["deepwater"] = df["water_depth_cx"] > C.OFFSHORE_DEEPWATER_FT
    df["depth_stratum"] = np.where(
        df["water_depth_cx"].isna(), "unknown",
        np.where(df["deepwater"], "deepwater", "shallow"))

    # removal method, from the applications file, joined on the same key
    rem["complex_id"] = rem["complex_id"].str.strip()
    rem["structure_number"] = rem["structure_number"].str.strip()
    rem["structure_key"] = rem["complex_id"] + "-" + rem["structure_number"]
    meth = (rem.sort_values("received_date")
               .groupby("structure_key")["proposed_removal_method"]
               .last().rename("removal_method"))
    df = df.merge(meth, on="structure_key", how="left")

    return df, stru, mast, rem


# ------------------------------------------------------------------ lifetimes
def lifetimes(df: pd.DataFrame) -> tuple:
    """(entry_age, exit_age, event) per structure, left-truncated at OBS_START."""
    d = df.copy()
    drops = {}

    n0 = len(d)
    d = d[d["install_date"].notna()].copy()
    drops["no_install_date"] = n0 - len(d)

    # a removal that precedes its own installation is a data error, not a lifetime
    bad = d["removal_date"].notna() & (d["removal_date"] < d["install_date"])
    drops["removal_before_install"] = int(bad.sum())
    d = d[~bad].copy()

    # Removals dated after the as-of moment have not happened yet as far as this
    # analysis is concerned; censor rather than let the future leak in.
    future = d["removal_date"].notna() & (d["removal_date"] > AS_OF)
    drops["removal_after_as_of_censored"] = int(future.sum())
    d.loc[future, "removal_date"] = pd.NaT

    # LEFT TRUNCATION. Anything already gone before the window opened was never
    # observable; anything still standing then enters at the age it had reached.
    gone_before = d["removal_date"].notna() & (d["removal_date"] < OBS_START)
    drops["removed_before_obs_start"] = int(gone_before.sum())
    d = d[~gone_before].copy()

    d["event"] = d["removal_date"].notna().astype(int)
    exit_date = d["removal_date"].fillna(AS_OF)

    d["entry_age_years"] = ((OBS_START - d["install_date"]).dt.days / DAYS_YEAR).clip(lower=0.0)
    d["exit_age_years"] = (exit_date - d["install_date"]).dt.days / DAYS_YEAR
    d["left_truncated"] = d["entry_age_years"] > 0

    # A structure whose entire life fits inside a single day gives the model no
    # interval to work with; lifelines rejects entry >= exit outright.
    degenerate = d["exit_age_years"] <= d["entry_age_years"]
    drops["degenerate_interval"] = int(degenerate.sum())
    d = d[~degenerate].copy()

    d["install_year"] = d["install_date"].dt.year
    d["removal_year"] = d["removal_date"].dt.year
    d["age_years"] = d["exit_age_years"]
    d["standing"] = d["event"] == 0

    return d, drops


# ------------------------------------------------------------------ report
def profile(df: pd.DataFrame, life: pd.DataFrame, drops: dict,
            stru: pd.DataFrame, rem: pd.DataFrame) -> dict:
    inst = df["install_date"].dropna()
    by_dec = (inst.dt.year // 10 * 10).value_counts().sort_index()
    imp_by_dec = (df[df["install_imputed"]]["install_date"].dropna()
                  .dt.year // 10 * 10).value_counts().sort_index()

    standing = df[df["removal_date"].isna()]
    meth = rem["proposed_removal_method"].replace("", "(blank)").value_counts()
    rem_year = pd.to_datetime(rem["removal_date"], format="%m/%d/%Y",
                              errors="coerce").dt.year.value_counts().sort_index()

    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of": str(C.AS_OF),
        "domain": "offshore",
        "region": "US Outer Continental Shelf, Gulf of Mexico",
        "unit_of_analysis": C.OFFSHORE_UNIT,

        "counts": {
            "structures": int(len(stru)),
            "complexes": int(df["complex_id"].nunique()),
            "removal_applications": int(len(rem)),
            "structures_in_multi_structure_complex": int(df["complex_is_shared"].sum()),
            "complexes_holding_more_than_one": int(
                (df.groupby("complex_id").size() > 1).sum()),
        },

        # the finding that reframes the product: this file is mostly a history
        # of structures that have already gone
        "observability": {
            "removal_date_present": int(df["removal_date"].notna().sum()),
            "removal_date_absent_standing": int(len(standing)),
            "event_rate": round(float(df["removal_date"].notna().mean()), 4),
            "censoring_rate": round(float(df["removal_date"].isna().mean()), 4),
            "standing_median_install_year": int(
                standing["install_date"].dt.year.median()) if len(standing) else None,
            "standing_median_age_years": round(float(
                ((AS_OF - standing["install_date"]).dt.days / DAYS_YEAR).median()), 1)
                if len(standing) else None,
            "standing_by_type": standing["structure_type_code"].value_counts().head(8).to_dict(),
        },

        "install_date_quality": {
            "imputed_mark": C.OFFSHORE_IMPUTED_INSTALL_MARK,
            "imputed_rate": round(float(df["install_imputed"].mean()), 4),
            "policy": C.OFFSHORE_IMPUTED_INSTALL,
            "by_decade": {int(k): int(v) for k, v in by_dec.items()},
            "imputed_by_decade": {int(k): int(v) for k, v in imp_by_dec.items()},
            "note": ("01-JAN is a placeholder standing in for a year. For structures "
                     "older than about 1990 the entry time is known to the year, not "
                     "the day, which is measurement error in the entry time and is "
                     "not repaired by left truncation."),
        },

        "left_truncation": {
            "obs_start": str(C.OFFSHORE_OBS_START),
            "applied": True,
            "left_truncated_rows": int(life["left_truncated"].sum()),
            "max_entry_age_years": round(float(life["entry_age_years"].max()), 1),
            "note": ("Structures installed before obs_start enter the risk set at the "
                     "age they had already reached. Those removed before it were never "
                     "observable and are dropped."),
        },

        "storms": {
            "policy": C.OFFSHORE_STORM_POLICY,
            "separable": False,
            "removal_methods": {str(k): int(v) for k, v in meth.head(10).items()},
            "removals_by_year": {int(k): int(v) for k, v in rem_year.items()
                                 if k >= 2000},
            "note": ("Proposed Removal Method describes how a structure was severed, "
                     "not why it left service, and Submittal Type is only INITIAL or "
                     "MODIFICATION. Removals peak in 2009, 2011 and 2012 rather than "
                     "in the storm years 2004, 2005 and 2008, because the removal date "
                     "is the regulatory paperwork date and lags destruction by years. "
                     "Storm losses are therefore INSIDE these figures and cannot be "
                     "identified from this data. They are not excluded, and no result "
                     "here should be read as if they were."),
        },

        "depth_strata": {
            "deepwater_ft": C.OFFSHORE_DEEPWATER_FT,
            "counts": df["depth_stratum"].value_counts().to_dict(),
            "note": "Reported by stratum because lifetimes differ by orders of magnitude.",
        },

        "shared_covariates": {
            "columns": SHARED,
            "note": ("Complex-level attributes joined one-to-many onto structure rows. "
                     "Shared, not independently measured per structure."),
        },

        "excluded": drops,
        "lifetime_rows": int(len(life)),
        "operator_used": C.OFFSHORE_USE_OPERATOR,
        "leakage_fields_never_features": list(C.OFFSHORE_LEAKAGE_FIELDS),

        "framing": ("This is the physical offshore install base of the US Gulf of "
                    "Mexico as BSEE records it: named structures, typed, with a date "
                    "in and a date out. It observes the asset and never observes what "
                    "was paid for it, which is the mirror image of the federal "
                    "procurement domain. The two are never ranked against each other."),
    }


def main() -> None:
    df, stru, mast, rem = build()
    life, drops = lifetimes(df)

    log(f"lifetimes: {len(life):,} rows  events={int(life['event'].sum()):,} "
        f"censored={int((life['event'] == 0).sum()):,} "
        f"left-truncated={int(life['left_truncated'].sum()):,}")
    for k, v in drops.items():
        if v:
            log(f"  excluded {k}: {v:,}")

    df.to_parquet(OUT_STRU, index=False)
    life.to_parquet(OUT_LIFE, index=False)
    rep = profile(df, life, drops, stru, rem)
    REPORT.write_text(json.dumps(rep, indent=2))

    log(f"structures -> {OUT_STRU}")
    log(f"lifetimes  -> {OUT_LIFE}")
    log(f"profile    -> {REPORT}")
    o = rep["observability"]
    log(f"standing: {o['removal_date_absent_standing']:,} structures, "
        f"median age {o['standing_median_age_years']} years")


if __name__ == "__main__":
    main()
