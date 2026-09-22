"""Source 3 - SEC filing benchmarks. BENCHMARK ONLY. Never merged into account data.

Filer : Everpure, Inc. - CIK 0001474432, SIC 3572 (Computer Storage Devices)
Source: SEC EDGAR (public domain). XBRL company facts API plus the narrative
        metrics that only appear in the filing text.
Licence: EDGAR filings are public records. Accessed with a declared User-Agent
        per SEC's automated-access policy.

Why this exists
---------------
The expected-value ranking needs a gross-margin assumption, and the synthetic
layer needs plausible bounds on expansion. Rather than inventing numbers, we
anchor them to what a real enterprise-storage filer reports, and we say plainly
that it is an anchor for an assumption, not a finding about the accounts in this
dataset.

These figures describe one public company. They are NOT properties of the federal
procurement records analysed elsewhere in this project, and they are never joined
to them.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from html import unescape
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

CIK = "0001474432"
COMPANY = "Everpure, Inc."
# SEC's automated-access policy requires a declared User-Agent carrying a contact
# address, and returns 403 without one. Set SEC_CONTACT to your own address before
# running; the placeholder below is deliberately a non-routable .invalid domain so
# no real mailbox is asserted.
UA = ("install-base-reactivation-console research "
      + os.environ.get("SEC_CONTACT", "contact@install-base-console.invalid"))
OUT = C.PROCESSED / "sec_benchmarks.json"

FACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def get(url: str) -> dict:
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"},
                     timeout=120)
    r.raise_for_status()
    return r.json()


def annual_series(facts: dict, tag: str, taxonomy: str = "us-gaap") -> list:
    """Pull annual (FY, 10-K) values for one XBRL concept, newest first."""
    node = facts.get("facts", {}).get(taxonomy, {}).get(tag)
    if not node:
        return []
    out = []
    for unit, entries in node.get("units", {}).items():
        for e in entries:
            if e.get("form") == "10-K" and e.get("fp") == "FY" and "start" in e:
                out.append({"fy": e.get("fy"), "start": e["start"], "end": e["end"],
                            "val": e["val"], "unit": unit, "accn": e.get("accn"),
                            "filed": e.get("filed")})
    # De-duplicate on period, keeping the most recently filed restatement.
    best: dict = {}
    for e in out:
        k = (e["start"], e["end"])
        if k not in best or e["filed"] > best[k]["filed"]:
            best[k] = e
    return sorted(best.values(), key=lambda e: e["end"], reverse=True)


def narrative_metrics() -> dict:
    """ARR growth and Subscription NDR appear only in the filing narrative."""
    subs = get(SUBMISSIONS_URL)
    recent = subs["filings"]["recent"]
    tenk = [(a, d, doc) for a, f, d, doc in zip(
        recent["accessionNumber"], recent["form"], recent["filingDate"],
        recent["primaryDocument"]) if f == "10-K"]
    if not tenk:
        return {}
    accn, filed, doc = tenk[0]
    acc_nodash = accn.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{acc_nodash}/{doc}"
    log(f"reading 10-K narrative: {url}")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=180)
    r.raise_for_status()
    t = re.sub(r"<[^>]+>", " ", r.text)
    t = re.sub(r"[ \s]+", " ", unescape(t))

    out: dict = {"accession": accn, "filed": filed, "document_url": url}
    m = re.search(r"Subscription NDR[^.]*?was\s+(\d{2,3})%\s+and\s+(\d{2,3})%\s+for the "
                  r"fiscal years ended\s+(\d{4})\s+and\s+(\d{4})", t)
    if m:
        out["subscription_ndr"] = {m.group(3): int(m.group(1)) / 100,
                                   m.group(4): int(m.group(2)) / 100}
        out["subscription_ndr_quote"] = m.group(0)
    m = re.search(r"growth in our Subscription ARR at the end of fiscal (\d{4}) was (\d{1,3})% "
                  r"compared to growth of (\d{1,3})% in fiscal (\d{4})", t)
    if m:
        out["subscription_arr_growth"] = {m.group(1): int(m.group(2)) / 100,
                                          m.group(4): int(m.group(3)) / 100}
        out["subscription_arr_growth_quote"] = m.group(0)
    m = re.search(r"Subscription annual recurring revenue \$ ([\d,]+) \$ ([\d,]+) (\d{1,3}) %", t)
    if m:
        out["subscription_arr_usd_thousands"] = [int(m.group(1).replace(",", "")),
                                                 int(m.group(2).replace(",", ""))]
    return out


def main() -> None:
    log(f"fetching SEC XBRL facts for {COMPANY} (CIK {CIK})")
    facts = get(FACTS_URL)
    rev = annual_series(facts, "RevenueFromContractWithCustomerExcludingAssessedTax") \
        or annual_series(facts, "Revenues")
    gp = annual_series(facts, "GrossProfit")

    margins = []
    gp_by_period = {(e["start"], e["end"]): e for e in gp}
    for e in rev:
        g = gp_by_period.get((e["start"], e["end"]))
        if g and e["val"]:
            margins.append({
                "fiscal_year_end": e["end"],
                "revenue_usd": e["val"],
                "gross_profit_usd": g["val"],
                "gross_margin": round(g["val"] / e["val"], 4),
                "accession": e["accn"],
            })
    margins = margins[:6]

    narrative = narrative_metrics()

    payload = {
        "provenance": "REAL",
        "use": "BENCHMARK ONLY - calibration bounds for assumptions. Never joined to account data.",
        "company": COMPANY,
        "cik": CIK,
        "sic": "3572 Computer Storage Devices",
        "source": "SEC EDGAR",
        "retrieved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "xbrl_api": FACTS_URL,
        "annual_gross_margin": margins,
        "narrative": narrative,
        "caveat": ("These are one public company's reported results. They bound what a "
                   "commercially plausible margin or expansion rate looks like in "
                   "enterprise storage. They say nothing about the federal accounts in "
                   "this dataset and are never merged into them."),
    }
    OUT.write_text(json.dumps(payload, indent=2))
    log(f"benchmarks -> {OUT}")
    if margins:
        log(f"latest annual gross margin: {margins[0]['gross_margin']:.1%} "
            f"(FY ending {margins[0]['fiscal_year_end']})")
    if narrative.get("subscription_ndr"):
        log(f"subscription NDR: {narrative['subscription_ndr']}")
    if narrative.get("subscription_arr_growth"):
        log(f"subscription ARR growth: {narrative['subscription_arr_growth']}")


if __name__ == "__main__":
    main()
