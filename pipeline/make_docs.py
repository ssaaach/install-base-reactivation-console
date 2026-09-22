"""Generate the documents that must not drift from the code.

Writes:
    ASSUMPTIONS.md        rendered from pipeline/assumptions.py
    docs/PROVENANCE.md    rendered from pipeline/provenance.py
    README.md             the provenance table and headline figures are injected
                          between <!-- BEGIN X --> / <!-- END X --> markers, so
                          prose stays hand-written and numbers stay generated

Run it after the pipeline, so the injected figures reflect the current run.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assumptions as A  # noqa: E402
import config as C  # noqa: E402
import provenance as PROV  # noqa: E402

ROOT = C.ROOT
README = ROOT / "README.md"
ASSUMPTIONS_MD = ROOT / "ASSUMPTIONS.md"
PROVENANCE_MD = ROOT / "docs" / "PROVENANCE.md"


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def jload(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def money(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    for unit, div in (("bn", 1e9), ("m", 1e6), ("k", 1e3)):
        if abs(v) >= div:
            return f"${v / div:,.1f}{unit}"
    return f"${v:,.0f}"


def pct(v, nd=1) -> str:
    return "n/a" if v is None else f"{float(v) * 100:.{nd}f}%"


def fmt_value(v) -> str:
    """Readable value for the register table: no scientific notation."""
    if v is None:
        return "derived at runtime"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.4g}"
    return str(v)


def num(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "n/a"


# ---------------------------------------------------------------- assumptions
def assumptions_md() -> str:
    L = ["# Assumption register",
         "",
         "Every commercial number the console uses lives here. None of these is a "
         "finding. Where an assumption has a public anchor it is cited; where it has "
         "none, that is stated rather than disguised.",
         "",
         "Generated from `pipeline/assumptions.py` - edit there, not here.",
         "",
         "| ID | Assumption | Value | Kind |",
         "|---|---|---|---|"]
    for a in A.register():
        L.append(f"| `{a['id']}` | {a['name']} | {fmt_value(a['value'])} {a['unit']} "
                 f"| **{a['kind']}** |")
    L += ["", "---", ""]
    for a in A.register():
        L += [f"## {a['id']} - {a['name']}", "",
              f"**Value:** {fmt_value(a['value'])} {a['unit']}  ",
              f"**Kind:** {a['kind']}", "",
              f"**Basis.** {a['basis']}", "",
              f"**Source.** {a['source']}", "",
              f"**Why this is an assumption.** {a['why_it_is_an_assumption']}", "",
              f"**Sensitivity.** {a['sensitivity']}", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- provenance
def provenance_md() -> str:
    counts = PROV.counts()
    return "\n".join([
        "# Field-level provenance",
        "",
        "Generated from `pipeline/provenance.py`. Every field in the final model "
        "carries one tag.",
        "",
        "| Tag | Meaning | Fields |",
        "|---|---|---:|",
        f"| **REAL** | sourced from a named public dataset, unmodified | {counts['REAL']} |",
        f"| **DERIVED** | computed from REAL fields only | {counts['DERIVED']} |",
        f"| **SYNTHETIC** | generated, because no public equivalent exists | {counts['SYNTHETIC']} |",
        f"| **ASSUMPTION** | a stated commercial input, not a measurement | {counts['ASSUMPTION']} |",
        "",
        PROV.to_markdown(),
        "",
    ])


# ---------------------------------------------------------------- readme bits
def figures_block() -> str:
    ib = jload(C.REPORTS / "install_base_report.json")
    mr = jload(C.REPORTS / "match_report.json")
    ing = jload(C.REPORTS / "ingest_report.json")
    sv = jload(C.REPORTS / "survival_report.json")
    evr = jload(C.REPORTS / "ev_sensitivity.json")
    pl = jload(C.REPORTS / "plays_report.json")
    ch = jload(C.REPORTS / "channel_rule_report.json")
    bb = jload(C.PROCESSED / "backblaze_hazard.json")
    man = jload(C.MANIFEST)

    cov = ib.get("coverage", {})
    ren = ib.get("renewals", {})
    rec = mr.get("recipients", {})
    val = mr.get("validation", {})
    org = mr.get("organisations", {})
    err = ch.get("error_rate_vs_sam_flag", {})
    res = sv.get("results", {})

    L = ["| Measure | Value |", "|---|---:|",
         f"| Prime award rows downloaded | {num(man.get('totals', {}).get('prime_award_rows'))} |",
         f"| Distinct awards after dedup | {num(ing.get('distinct_awards'))} |",
         f"| Accounts (awarding offices) | {num(ib.get('accounts'))} |",
         f"| Total obligated in scope | {money(ib.get('total_obligated_usd'))} |",
         f"| Resolved vendor entities | {num(rec.get('entities_after'))} |",
         f"| Dormancy rate ({C.DORMANCY_MONTHS}m, REAL) | {pct(ib.get('dormancy_rate'))} |",
         f"| Hardware coverage rate | {pct(cov.get('coverage_rate'))} |",
         f"| Uncovered hardware awards | {num(cov.get('uncovered'))} |",
         f"| Uncovered obligations | {money(cov.get('uncovered_obligations_usd'))} |",
         f"| Contracts expiring within {C.RENEWAL_LOOKAHEAD_DAYS}d | {num(ren.get('expiring_contracts'))} |",
         f"| Co-termination clusters | {num(ren.get('coterm_clusters'))} |",
         f"| Accounts with an explicit NO_ACTION | {num(pl.get('accounts_no_action'))} |",
         f"| Accounts with positive expected value | {num(evr.get('positive_ev_accounts'))} |"]
    if err.get("awards_scored"):
        L.append(f"| Channel rule error rate vs SAM flag | {pct(err.get('error_rate'))} |")
    if val.get("available"):
        L.append(f"| Same-parent detection precision | {pct(val.get('same_parent_precision'))} |")
        L.append(f"| Same-parent detection recall | {pct(val.get('same_parent_recall'))} |")
    if org:
        L.append(f"| Awarding offices resolved | {num(org.get('office_entities_after'))} |")
    if res:
        m, b = res.get("survival_model", {}), res.get("rules_baseline", {})
        L.append(f"| Survival model AUC / Brier | "
                 f"{m.get('auc', float('nan')):.3f} / {m.get('brier', float('nan')):.4f} |")
        L.append(f"| Rules baseline AUC / Brier | "
                 f"{b.get('auc', float('nan')):.3f} / {b.get('brier', float('nan')):.4f} |")
        L.append(f"| Ranking uses | **{sv.get('ranking_uses', 'n/a')}** |")
    if bb.get("status") == "ok":
        L.append(f"| Backblaze drive-days behind the hazard curve | "
                 f"{num(bb.get('total_drive_days'))} |")
    L.append("")
    L.append(f"_Generated {_dt.datetime.now(_dt.timezone.utc):%Y-%m-%d %H:%M} UTC "
             f"by `pipeline/make_docs.py`. As-of date {C.AS_OF}._")
    return "\n".join(L)


def inject(text: str, marker: str, body: str) -> str:
    begin, end = f"<!-- BEGIN {marker} -->", f"<!-- END {marker} -->"
    pat = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
    block = f"{begin}\n{body}\n{end}"
    if pat.search(text):
        return pat.sub(lambda _m: block, text)
    return text + "\n\n" + block + "\n"


def main() -> None:
    ASSUMPTIONS_MD.write_text(assumptions_md(), encoding="utf-8")
    log(f"assumptions -> {ASSUMPTIONS_MD}")

    PROVENANCE_MD.parent.mkdir(parents=True, exist_ok=True)
    PROVENANCE_MD.write_text(provenance_md(), encoding="utf-8")
    log(f"provenance -> {PROVENANCE_MD}")

    if README.exists():
        t = README.read_text(encoding="utf-8")
        t = inject(t, "PROVENANCE_TABLE", PROV.to_markdown())
        t = inject(t, "HEADLINE_FIGURES", figures_block())
        README.write_text(t, encoding="utf-8")
        log(f"README updated -> {README}")
    else:
        log("README.md not present yet; skipping injection")


if __name__ == "__main__":
    main()
