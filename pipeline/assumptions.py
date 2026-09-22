"""The assumption register - single source of truth for every commercial number.

Nothing in here is a finding. Every entry is an assumption, and the console
renders it as one. Where an assumption has a public anchor we cite it; where it
has none we say so rather than dressing it up.

Read by ranking.py, plays.py and export_app_data.py so the register cannot drift
out of sync with the numbers actually used.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

BENCHMARKS = C.PROCESSED / "sec_benchmarks.json"


def _benchmark_margin() -> tuple[float, str]:
    """Anchor the margin assumption to a real filer's reported gross margin."""
    if BENCHMARKS.exists():
        d = json.loads(BENCHMARKS.read_text())
        m = d.get("annual_gross_margin") or []
        if m:
            return float(m[0]["gross_margin"]), (
                f"{d['company']} (CIK {d['cik']}, SIC {d['sic']}) reported a "
                f"{m[0]['gross_margin']:.1%} GAAP gross margin for the fiscal year "
                f"ending {m[0]['fiscal_year_end']}, SEC accession {m[0]['accession']}, "
                f"retrieved {d['retrieved_at'][:10]}.")
    return 0.65, "No benchmark available; value is unanchored."


def register() -> list:
    margin, margin_anchor = _benchmark_margin()
    ndr_note = ""
    if BENCHMARKS.exists():
        d = json.loads(BENCHMARKS.read_text())
        n = (d.get("narrative") or {}).get("subscription_ndr")
        if n:
            ndr_note = (" Subscription net dollar retention reported at "
                        + ", ".join(f"{v:.0%} (FY{k})" for k, v in sorted(n.items()))
                        + " bounds plausible expansion.")

    return [
        {
            "id": "A-01",
            "name": "Gross margin on an incremental award",
            "value": round(margin, 4),
            "unit": "fraction of award value",
            "kind": "ASSUMPTION",
            "basis": "Anchored to a public enterprise-storage filer's reported gross margin.",
            "source": margin_anchor,
            "why_it_is_an_assumption": (
                "A vendor's blended corporate gross margin is not the margin any "
                "particular federal award would carry. Federal pricing, channel "
                "discounts and contract vehicle fees all move it. The benchmark "
                "establishes an order of magnitude, nothing more."),
            "sensitivity": "Ranking is recomputed across 0.30-0.80; see reports/ev_sensitivity.json.",
        },
        {
            "id": "A-02",
            "name": "Engagement cost per account per cycle",
            "value": 12000.0,
            "unit": "USD",
            "kind": "ASSUMPTION",
            "basis": "Fully loaded cost of one seller-led engagement cycle.",
            "source": "No public source. This is a planning figure, not a measurement.",
            "why_it_is_an_assumption": (
                "Nothing in public procurement data reveals the cost of pursuing an "
                "account. Any reader should substitute their own number; the ranking "
                "reads it from this register."),
            "sensitivity": "Changes the EV floor, so it decides which accounts fall below zero.",
        },
        {
            "id": "A-03",
            "name": "Opportunity horizon",
            "value": C.HORIZON_DAYS,
            "unit": "days",
            "kind": "ASSUMPTION",
            "basis": "One year, matching the federal budget cycle.",
            "source": "Chosen to align with the fiscal year; not derived from the data.",
            "why_it_is_an_assumption": (
                "The horizon sets what 'will they buy' means. A shorter horizon "
                "raises precision and shrinks the addressable set."),
            "sensitivity": "The survival model is refitted per horizon; 365d is the reported case.",
        },
        {
            "id": "A-04",
            "name": "Expected award value",
            "value": None,
            "unit": "USD per account",
            "kind": "DERIVED",
            "basis": ("Median of the account's most recent awards (REAL), which is "
                      "robust to the very large outlier awards in federal data."),
            "source": "Computed from USAspending award amounts. Not assumed.",
            "why_it_is_an_assumption": "It is not - this one is derived from real records.",
            "sensitivity": "Median vs mean changes the ranking; median is used deliberately.",
        },
        {
            "id": "A-05",
            "name": "Coverage window",
            "value": C.COVERAGE_WINDOW_DAYS,
            "unit": "days",
            "kind": "ASSUMPTION",
            "basis": "A maintenance award within this distance counts as covering the hardware.",
            "source": "Chosen. No public rule defines service attachment windows.",
            "why_it_is_an_assumption": (
                "Widening the window raises the coverage rate mechanically. The "
                "reported rate is only meaningful alongside the window."),
            "sensitivity": "Coverage is also reported same-vendor-only as a stricter read.",
        },
        {
            "id": "A-06",
            "name": "Dormancy threshold",
            "value": C.DORMANCY_MONTHS,
            "unit": "months",
            "kind": "ASSUMPTION",
            "basis": "No new award in 24 months marks an account dormant.",
            "source": ("Threshold chosen; the underlying absence of awards is REAL and "
                       "not simulated."),
            "why_it_is_an_assumption": (
                "The cut point is a choice. The observation that no award exists is not."),
            "sensitivity": "Dormancy counts are reported at 12/18/24/36 months in the console.",
        },
        {
            "id": "A-07",
            "name": "Refresh-risk asset age",
            "value": None,
            "unit": "years",
            "kind": "DERIVED",
            "basis": ("The youngest drive-age bucket whose annualised failure rate is "
                      "twice the observed floor, taken from Backblaze Drive Stats."),
            "source": ("Backblaze Drive Stats quarterly data, ~27.8M drive-days. "
                       "Replaces the hand-picked age threshold v1 used."),
            "why_it_is_an_assumption": (
                "It is derived, but applying consumer/datacentre drive hazards to "
                "federal storage assets is itself an assumption about similarity."),
            "sensitivity": "Falls back to a flat 5-year threshold if the curve is unavailable.",
        },
        {
            "id": "A-08",
            "name": "Peer adoption floor for whitespace",
            "value": 0.40,
            "unit": "fraction of peer accounts",
            "kind": "ASSUMPTION",
            "basis": "A category counts as whitespace once 40% of peers already buy it.",
            "source": "Chosen. Lower floors generate more, weaker, gaps.",
            "why_it_is_an_assumption": "The floor decides how many gaps exist.",
            "sensitivity": "Whitespace volume scales roughly inversely with the floor.",
        },
        {
            "id": "A-09",
            "name": "Expansion plausibility bounds for the synthetic layer",
            "value": None,
            "unit": "ratio",
            "kind": "ASSUMPTION",
            "basis": "Synthetic utilisation and health are kept inside commercially sane bounds.",
            "source": ("Anchored to public filings for order of magnitude." + ndr_note),
            "why_it_is_an_assumption": (
                "The synthetic layer is generated. These bounds only stop it drifting "
                "somewhere implausible; they do not make it evidence."),
            "sensitivity": "Synthetic fields are excluded from the model and the ranking entirely.",
        },
    ]


def as_dict() -> dict:
    return {a["id"]: a for a in register()}


def get(aid: str):
    return as_dict()[aid]["value"]


MARGIN = "A-01"
ENGAGEMENT_COST = "A-02"
HORIZON = "A-03"


if __name__ == "__main__":
    import textwrap
    for a in register():
        print(f"\n{a['id']}  {a['name']}  =  {a['value']}  [{a['kind']}]")
        print(textwrap.indent(textwrap.fill(f"basis: {a['basis']}", 96), "    "))
        print(textwrap.indent(textwrap.fill(f"source: {a['source']}", 96), "    "))
