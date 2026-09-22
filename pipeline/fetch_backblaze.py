"""Source 2 - Backblaze Drive Stats: fit an empirical failure-hazard curve by drive age.

Source : https://www.backblaze.com/cloud-storage/resources/hard-drive-test-data
Licence: Backblaze publishes Drive Stats under a permissive licence requiring
         attribution and no misrepresentation. Cited in README.

Why this exists
---------------
The refresh-risk play would otherwise rest on a hand-picked age threshold
("assets over 5 years old are at risk"). That is an assumption wearing the
costume of a finding. Instead we fit a real hazard function h(age) from
~25 million drive-days of published observations and apply it to asset age.

What it computes
----------------
For each drive-age bucket (from SMART attribute 9, power-on hours):
    drive_days   - observed days at risk in that bucket
    failures     - observed failures in that bucket
    hazard       - failures / drive_days  (daily hazard)
    afr          - annualised failure rate, 1 - (1 - hazard)^365

The quarterly archive is ~960 MB, so it is downloaded to a scratch directory and
NOT committed. Only the aggregated hazard table (a few kB) lands in the repo.
Re-running is idempotent: the aggregate is cached.

Usage
-----
    python pipeline/fetch_backblaze.py                 # default quarter
    python pipeline/fetch_backblaze.py --quarter Q1_2025 --scratch DIR
    python pipeline/fetch_backblaze.py --skip          # record a clean skip
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

BASE_URL = "https://f001.backblazeb2.com/file/Backblaze-Hard-Drive-Data"
DEFAULT_QUARTER = "Q1_2025"
USECOLS = ["date", "serial_number", "model", "capacity_bytes", "failure", "smart_9_raw"]

OUT_CSV = C.PROCESSED / "backblaze_hazard.csv"
OUT_JSON = C.PROCESSED / "backblaze_hazard.json"

# Age buckets in years. Drive age comes from power-on hours, so these are
# power-on years, which for always-on datacentre drives is close to wall time.
AGE_EDGES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 99]


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def scratch_dir(override: str | None) -> Path:
    if override:
        p = Path(override)
    else:
        env = os.environ.get("CLAUDE_SCRATCHPAD") or os.environ.get("TEMP") or "."
        p = Path(env) / "backblaze"
    p.mkdir(parents=True, exist_ok=True)
    return p


def download(quarter: str, dest: Path) -> Path:
    url = f"{BASE_URL}/data_{quarter}.zip"
    target = dest / f"data_{quarter}.zip"
    if target.exists() and target.stat().st_size > 100_000_000:
        log(f"archive already present: {target} ({target.stat().st_size / 1e6:.0f} MB)")
        return target
    log(f"downloading {url}")
    with requests.get(url, stream=True, timeout=1200,
                      headers={"User-Agent": C.USER_AGENT}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        seen = 0
        with open(target, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
                seen += len(chunk)
                if total and seen % (100 << 20) < (1 << 20):
                    log(f"  {seen / 1e6:.0f} / {total / 1e6:.0f} MB")
    log(f"downloaded {target.stat().st_size / 1e6:.0f} MB")
    return target


def aggregate(zip_path: Path) -> pd.DataFrame:
    """Stream every daily CSV in the archive, aggregating drive-days and failures."""
    frames = []
    with zipfile.ZipFile(zip_path) as z:
        members = [n for n in z.namelist()
                   if n.lower().endswith(".csv") and "__MACOSX" not in n]
        members.sort()
        log(f"{len(members)} daily snapshots in archive")
        for i, name in enumerate(members, 1):
            with z.open(name) as fh:
                try:
                    df = pd.read_csv(io.BytesIO(fh.read()), usecols=USECOLS,
                                     low_memory=False)
                except ValueError as e:
                    log(f"  skip {name}: {e}")
                    continue
            df = df[df["smart_9_raw"].notna()]
            # SMART 9 is power-on hours -> years.
            age_years = df["smart_9_raw"].astype("float64") / 24.0 / 365.25
            df = df.assign(
                age_bucket=pd.cut(age_years, bins=AGE_EDGES, right=False),
                failure=pd.to_numeric(df["failure"], errors="coerce").fillna(0).astype("int8"),
            )
            g = df.groupby("age_bucket", observed=True).agg(
                drive_days=("failure", "size"), failures=("failure", "sum")
            )
            frames.append(g)
            if i % 15 == 0 or i == len(members):
                log(f"  processed {i}/{len(members)} snapshots")
    if not frames:
        raise RuntimeError("no daily snapshots could be parsed")
    out = (pd.concat(frames).groupby(level=0, observed=True).sum().reset_index())
    out["age_bucket"] = out["age_bucket"].astype(str)
    out["age_low_years"] = [iv.left for iv in
                            pd.concat(frames).groupby(level=0, observed=True).sum().index]
    out["hazard_daily"] = out["failures"] / out["drive_days"].clip(lower=1)
    out["afr"] = 1.0 - (1.0 - out["hazard_daily"]) ** 365.25
    return out.sort_values("age_low_years").reset_index(drop=True)


def write_skip(reason: str) -> None:
    payload = {
        "status": "skipped",
        "reason": reason,
        "source": "Backblaze Drive Stats",
        "url": "https://www.backblaze.com/cloud-storage/resources/hard-drive-test-data",
        "effect": ("REFRESH_RISK falls back to the assumed age threshold in "
                   "ASSUMPTIONS.md (A-07) instead of an empirical hazard curve."),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2))
    log(f"clean skip recorded -> {OUT_JSON}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Backblaze Drive Stats hazard curve")
    ap.add_argument("--quarter", default=DEFAULT_QUARTER, help="e.g. Q1_2025")
    ap.add_argument("--scratch", default=None, help="scratch directory for the ~960 MB archive")
    ap.add_argument("--skip", action="store_true", help="record a clean skip and exit")
    ap.add_argument("--keep-archive", action="store_true", help="do not delete the archive")
    a = ap.parse_args()

    if a.skip:
        write_skip("not run in this environment")
        return

    if OUT_CSV.exists():
        log(f"hazard table already built: {OUT_CSV}")
        return

    try:
        zp = download(a.quarter, scratch_dir(a.scratch))
        df = aggregate(zp)
    except Exception as e:
        log(f"FAILED: {e!r}")
        write_skip(f"fetch or parse failed: {e!r}")
        return

    df.to_csv(OUT_CSV, index=False)
    payload = {
        "status": "ok",
        "source": "Backblaze Drive Stats",
        "url": "https://www.backblaze.com/cloud-storage/resources/hard-drive-test-data",
        "quarter": a.quarter,
        "retrieved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "provenance": "REAL",
        "note": ("Drive age derived from SMART attribute 9 (power-on hours). "
                 "Hazard is failures per drive-day observed in the quarter; AFR is "
                 "the annualised equivalent. Applied to federal storage asset age as "
                 "a DERIVED refresh-risk weight, not as a vendor-specific claim."),
        "total_drive_days": int(df["drive_days"].sum()),
        "total_failures": int(df["failures"].sum()),
        "buckets": df.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str))
    log(f"hazard table -> {OUT_CSV}")
    log(f"{df['drive_days'].sum():,} drive-days, {df['failures'].sum():,} failures")
    print(df.to_string(index=False))

    if not a.keep_archive:
        try:
            zp.unlink()
            log("scratch archive deleted")
        except OSError:
            pass


if __name__ == "__main__":
    main()
