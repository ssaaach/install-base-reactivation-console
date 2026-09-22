"""Step 1 - Fetch prime contract awards from USAspending.gov into a disk cache.

Source : https://api.usaspending.gov  (US federal government, public domain, no key)
Licence: public domain (17 U.S.C. 105). Used within the API's terms of service.

Why the bulk download endpoint, not the search endpoint
-------------------------------------------------------
The obvious route is /api/v2/search/spending_by_award/, paging 100 records at a
time. We built that first and it does work, but at this scope it needs ~2,100
POSTs, and USAspending's edge starts refusing connections outright well before
that - every request then fails in about a second with RemoteDisconnected, from
curl as readily as from Python. Hammering a free public API until it blocks you
is both rude and unreliable.

/api/v2/download/awards/ is the API's own answer to a pull this size. It takes
the same filter object, generates a zip server-side, and returns 286 columns per
prime award instead of the 23 the search endpoint exposes. Those extra columns
are not a bonus, they are the difference between a workable model and a
compromised one:

  awarding_office_code / awarding_office_name
      lets an account be a sub-agency OFFICE, the grain the brief asked for.
      The search endpoint does not expose office at all.
  recipient_uei, recipient_parent_uei, recipient_parent_name
      gives entity resolution a real parent rollup, and an independent ground
      truth to score our own clustering against.
  recipient_name and recipient_name_raw
      USAspending's own normalised name beside the raw one: a second opinion on
      our normalisation.
  period_of_performance_current_end_date / _potential_end_date
      a renewal calendar built on the actual PoP fields.
  contracting_officers_determination_of_business_size, parent_award_id_piid
      real inputs to channel classification and contract-vehicle structure.

The zip also carries a subawards CSV, which exposes prime-to-sub relationships -
a far better channel signal than inferring from NAICS alone.

Design notes
------------
* Each (filter kind, fiscal-year chunk) is one download, cached to disk with the
  exact request body that produced it. Re-running is free; --force re-fetches.
* Generation is asynchronous: POST, then poll the status endpoint, then fetch the
  file. Polling is slow and deliberate - this is someone else's free service.
* time_period uses date_type=date_signed, so an award is counted once, in the
  fiscal year it was signed, rather than recurring in every year it was touched.

Usage
-----
    python pipeline/fetch_usaspending.py --probe     # show the plan, download nothing
    python pipeline/fetch_usaspending.py             # full pull (resumable)
    python pipeline/fetch_usaspending.py --sample 7K20 2023   # one raw search response
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

DOWNLOAD_URL = f"{C.API_BASE}/download/awards/"
STATUS_URL = f"{C.API_BASE}/download/status"

POLL_INTERVAL_S = 12
POLL_TIMEOUT_S = 5400
MANIFEST = C.MANIFEST

# Fiscal-year chunks. Smaller chunks mean smaller zips and a resumable pull.
FY_CHUNKS = [(2016, 2018), (2019, 2021), (2022, 2024), (2025, 2026)]


def log(msg: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": C.USER_AGENT, "Content-Type": "application/json"})
    return s


# ------------------------------------------------------------------ periods
def chunk_window(lo: int, hi: int) -> tuple[str, str]:
    """Fiscal years lo..hi inclusive -> (start, end), clamped to the searchable range."""
    start = max(f"{lo - 1}-10-01", C.SEARCHABLE_FLOOR)
    end = min(f"{hi}-09-30", C.AS_OF.isoformat())
    return start, end


def build_filters(kind: str, start: str, end: str) -> dict:
    period = {"start_date": start, "end_date": end}
    if getattr(C, "TIME_DATE_TYPE", None):
        period["date_type"] = C.TIME_DATE_TYPE
    f = {"award_type_codes": C.AWARD_TYPE_CODES, "time_period": [period]}
    if kind == "naics":
        f["naics_codes"] = sorted(C.NAICS_CODES)
    elif kind == "psc":
        # A list means OR within the list, which is what we want across the PSC
        # families. NAICS goes in its own request because combining the two in one
        # filter object would AND them.
        f["psc_codes"] = sorted(C.PSC_ALL)
    else:
        raise ValueError(f"unknown filter kind {kind!r}")
    return f


# ------------------------------------------------------------------ download
def request_download(s: requests.Session, filters: dict) -> dict:
    body = {"filters": filters, "columns": [], "file_format": "csv"}
    r = s.post(DOWNLOAD_URL, json=body, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"download request failed: HTTP {r.status_code} {r.text[:400]}")
    return r.json()


def check(s: requests.Session, file_name: str) -> dict:
    """One status probe. Returns the payload, or {'status': 'unknown'} on a blip."""
    try:
        r = s.get(STATUS_URL, params={"file_name": file_name}, timeout=180)
        if r.status_code == 200:
            return r.json()
        return {"status": "unknown", "http": r.status_code}
    except requests.RequestException as e:
        return {"status": "unknown", "error": type(e).__name__}


def poll_all(s: requests.Session, pending: dict) -> dict:
    """Wait on several server-side generations at once.

    Each chunk takes many minutes to build, so requesting them one after another
    would serialise an hour of work the server is perfectly happy to do in
    parallel. We submit everything first, then watch the whole set.

    Returns {name: status_payload} for those that finished.
    """
    done: dict = {}
    t0 = time.time()
    last_line = 0.0
    while pending and time.time() - t0 < POLL_TIMEOUT_S:
        for name, req in list(pending.items()):
            j = check(s, req["file_name"])
            st = j.get("status")
            if st == "finished":
                log(f"  ready: {name} ({j.get('total_rows'):,} rows x "
                    f"{j.get('total_columns')} cols, {j.get('seconds_elapsed')}s)")
                done[name] = j
                pending.pop(name)
            elif st == "failed":
                log(f"  FAILED server-side: {name} -> {j.get('message')}")
                pending.pop(name)
        if pending and time.time() - last_line > 60:
            log(f"  waiting on {len(pending)}: {', '.join(sorted(pending))} "
                f"({time.time() - t0:.0f}s elapsed)")
            last_line = time.time()
        if pending:
            time.sleep(POLL_INTERVAL_S)
    for name in pending:
        log(f"  TIMED OUT waiting for {name}")
    return done


def download(s: requests.Session, url: str, dest: Path) -> str:
    h = hashlib.sha256()
    tmp = dest.with_suffix(".part")
    with s.get(url, stream=True, timeout=1800) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        seen = 0
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
                h.update(chunk)
                seen += len(chunk)
                if total and seen % (25 << 20) < (1 << 20):
                    log(f"    {seen / 1e6:.0f} / {total / 1e6:.0f} MB")
    tmp.replace(dest)
    return h.hexdigest()


def inspect_zip(path: Path) -> dict:
    """Row and column counts per member, without loading the whole thing."""
    out = {}
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not n.lower().endswith(".csv"):
                continue
            with z.open(n) as fh:
                header = fh.readline().decode("utf-8", "replace")
                rows = sum(1 for _ in fh)
            out[n] = {"rows": rows, "columns": header.count(",") + 1}
    return out


# ------------------------------------------------------------------ driver
def plan() -> list:
    jobs = []
    for kind in ("psc", "naics"):
        for lo, hi in FY_CHUNKS:
            start, end = chunk_window(lo, hi)
            if start > end:
                continue
            jobs.append({"kind": kind, "fy_lo": lo, "fy_hi": hi,
                         "start": start, "end": end,
                         "name": f"{kind}_FY{lo}-FY{hi}.zip"})
    return jobs


def run(force: bool = False, probe_only: bool = False) -> None:
    s = session()
    jobs = plan()
    log(f"{len(jobs)} downloads planned "
        f"({len(C.PSC_ALL)} PSC codes, {len(C.NAICS_CODES)} NAICS code, "
        f"FY{C.FY_START}-FY{C.FY_END}, date_type={C.TIME_DATE_TYPE})")
    for j in jobs:
        log(f"    {j['name']:<26} {j['start']} .. {j['end']}")
    if probe_only:
        (C.RAW / "plan.json").write_text(json.dumps(jobs, indent=2))
        log("probe only; nothing downloaded")
        return

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"downloads": {}}
    manifest.setdefault("downloads", {})

    # Submit everything first, then wait on the whole set. Generation is the slow
    # part and the server runs the chunks concurrently.
    pending, by_name = {}, {}
    for i, j in enumerate(jobs, 1):
        dest = C.RAW / j["name"]
        if dest.exists() and not force:
            log(f"[{i}/{len(jobs)}] {j['name']} cached ({dest.stat().st_size / 1e6:.1f} MB)")
            continue
        filters = build_filters(j["kind"], j["start"], j["end"])
        req = request_download(s, filters)
        req["filters"] = filters
        log(f"[{i}/{len(jobs)}] queued {j['name']} -> {req['file_name']}")
        pending[j["name"]] = req
        by_name[j["name"]] = j
        time.sleep(1.0)

    if not pending:
        log("everything cached")
    else:
        log(f"waiting on {len(pending)} server-side generations")
    finished = poll_all(s, pending) if pending else {}

    for name, st in finished.items():
        j = by_name[name]
        dest = C.RAW / name
        log(f"downloading {name}")
        sha = download(s, st["file_url"], dest)
        members = inspect_zip(dest)
        manifest["downloads"][name] = {
            "kind": j["kind"],
            "fiscal_years": [j["fy_lo"], j["fy_hi"]],
            "window": [j["start"], j["end"]],
            "request": {"filters": build_filters(j["kind"], j["start"], j["end"]),
                        "columns": [], "file_format": "csv"},
            "server_file_name": st["file_name"],
            "server_reported": {k: st.get(k) for k in
                                ("total_rows", "total_columns", "total_size",
                                 "seconds_elapsed")},
            "sha256": sha,
            "bytes": dest.stat().st_size,
            "members": members,
            "retrieved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        log(f"    saved {dest.name} ({dest.stat().st_size / 1e6:.1f} MB) sha256={sha[:16]}...")
        for m, v in members.items():
            log(f"      {m}: {v['rows']:,} rows x {v['columns']} cols")
        MANIFEST.write_text(json.dumps(manifest, indent=2))

    manifest["generated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    manifest["source"] = "USAspending.gov API v2 /download/awards/ (public domain, 17 U.S.C. 105)"
    manifest["scope"] = {
        "fiscal_years": [C.FY_START, C.FY_END],
        "naics": C.NAICS_CODES,
        "psc": C.PSC_ALL,
        "psc_excluded": C.PSC_EXCLUDED,
        "award_type_codes": C.AWARD_TYPE_CODES,
        "date_type": C.TIME_DATE_TYPE,
        "date_type_note": ("date_signed: an award is counted once, in the fiscal year "
                           "it was signed. Contracts signed before FY2016 and still "
                           "running are therefore out of scope."),
    }
    prime = sum(v["rows"] for d in manifest["downloads"].values()
                for k, v in d["members"].items() if "PrimeAwardSummaries" in k)
    subs = sum(v["rows"] for d in manifest["downloads"].values()
               for k, v in d["members"].items() if "Subawards" in k)
    manifest["totals"] = {
        "downloads": len(manifest["downloads"]),
        "prime_award_rows": prime,
        "subaward_rows": subs,
        "bytes": sum(d["bytes"] for d in manifest["downloads"].values()),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    log(f"manifest -> {MANIFEST}")
    log(f"TOTAL {prime:,} prime award rows, {subs:,} subaward rows, "
        f"{manifest['totals']['bytes'] / 1e6:.1f} MB")


# ------------------------------------------------------------------ sample
def sample(code: str, fy: int) -> None:
    """One raw response from the SEARCH endpoint, printed verbatim.

    Kept because it is the clearest demonstration of what an unmodified
    USAspending record looks like, field by field, before anything we do to it.
    """
    s = session()
    start, end = max(f"{fy - 1}-10-01", C.SEARCHABLE_FLOOR), min(f"{fy}-09-30",
                                                                 C.AS_OF.isoformat())
    body = {
        "filters": {"award_type_codes": C.AWARD_TYPE_CODES,
                    "psc_codes": [code],
                    "time_period": [{"start_date": start, "end_date": end,
                                     "date_type": C.TIME_DATE_TYPE}]},
        "fields": C.AWARD_FIELDS, "page": 1, "limit": 3,
        "sort": "Award Amount", "order": "desc", "subawards": False,
    }
    print("--- REQUEST ---")
    print(json.dumps(body, indent=2))
    r = s.post(C.SEARCH_AWARD, json=body, timeout=180)
    r.raise_for_status()
    print("\n--- RAW RESPONSE (verbatim) ---")
    print(json.dumps(r.json(), indent=2)[:6000])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch USAspending prime contract awards")
    ap.add_argument("--probe", action="store_true", help="show the plan, download nothing")
    ap.add_argument("--force", action="store_true", help="re-fetch cached downloads")
    ap.add_argument("--sample", nargs=2, metavar=("PSC", "FY"),
                    help="print one raw search response for a PSC code and fiscal year")
    a = ap.parse_args()
    if a.sample:
        sample(a.sample[0], int(a.sample[1]))
    else:
        run(force=a.force, probe_only=a.probe)
