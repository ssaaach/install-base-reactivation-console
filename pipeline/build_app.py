"""Step 9b - Assemble the published pages into app/dist/.

Two pages are written, and neither has a runtime dependency on anything:

    app/index.html      the console template, __APP_DATA__ + __ARC_JS__
    app/landing.html    the cover template, __LANDING_DATA__ + __ARC_JS__
    app/arc.js          the masthead/hero animation, shared by both
    app/data/app_data.json  the bundle from export_app_data.py

      -> app/dist/index.html      console, data inlined
      -> app/dist/landing.html    cover, headline figures inlined

The cover takes its figures from the same bundle the console reads rather
than carrying its own copy, so it cannot quote a number the pipeline has
stopped producing. It gets a ~20-key digest, not the 4.6 MB bundle: a cover
page that takes a second to paint is a broken cover page.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

APP = C.ROOT / "app"
DIST = APP / "dist"
DATA = C.APP_DATA / "app_data.json"
ARC = APP / "arc.js"

CONSOLE_TPL = APP / "index.html"
LANDING_TPL = APP / "landing.html"

PH_DATA = "__APP_DATA__"
PH_LANDING = "__LANDING_DATA__"
PH_ARC = "__ARC_JS__"


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def _needs(text: str, token: str, where: Path) -> None:
    if token not in text:
        raise SystemExit(f"{where} has no {token} placeholder")


def landing_digest(bundle: dict) -> dict:
    """The figures the cover quotes, pulled from the console's own bundle."""
    k = bundle.get("kpis", {}) or {}
    m = bundle.get("meta", {}) or {}
    fy = (m.get("scope", {}) or {}).get("fiscal_years") or []
    totals = m.get("manifest_totals", {}) or {}
    span = f"FY{fy[0]}–FY{fy[-1]}" if len(fy) >= 2 else None
    return {
        "as_of": m.get("as_of"),
        "awards": k.get("awards_in_scope"),
        "accounts": k.get("accounts"),
        "obligated": k.get("total_obligated_usd"),
        "expiring": k.get("expiring_contracts"),
        "coterm": k.get("coterm_clusters"),
        "positive_ev": k.get("positive_ev_accounts"),
        "prime_rows": totals.get("prime_award_rows"),
        "fy_span": span,
        # second domain - the cover names both, so both must come from the
        # bundle. A figure typed into the template is a figure the pipeline
        # cannot keep honest.
        "offshore_standing": ((bundle.get("offshore", {}) or {}).get("kpis", {}) or {})
            .get("standing"),
        "offshore_structures": ((bundle.get("offshore", {}) or {}).get("kpis", {}) or {})
            .get("structures_all_time"),
    }


def main() -> None:
    for p in (CONSOLE_TPL, LANDING_TPL, ARC):
        if not p.exists():
            raise SystemExit(f"missing {p}")
    if not DATA.exists():
        raise SystemExit(f"missing {DATA} - run export_app_data.py first")

    arc = ARC.read_text(encoding="utf-8")
    raw = DATA.read_text(encoding="utf-8")
    bundle = json.loads(raw)

    # A literal "</script>" inside the JSON would close the block early. JSON
    # escapes it harmlessly as <\/script>, which JSON.parse reads back identically.
    def safe(s: str) -> str:
        return s.replace("</", "<\\/")

    DIST.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- console
    html = CONSOLE_TPL.read_text(encoding="utf-8")
    _needs(html, PH_DATA, CONSOLE_TPL)
    _needs(html, PH_ARC, CONSOLE_TPL)
    html = html.replace(PH_ARC, arc).replace(PH_DATA, safe(raw))
    (DIST / "index.html").write_text(html, encoding="utf-8")

    # --------------------------------------------------------------- cover
    digest = landing_digest(bundle)
    missing = [k for k, v in digest.items() if v is None]
    if missing:
        log(f"WARNING cover figures missing from the bundle: {', '.join(missing)}")

    cover = LANDING_TPL.read_text(encoding="utf-8")
    _needs(cover, PH_LANDING, LANDING_TPL)
    _needs(cover, PH_ARC, LANDING_TPL)
    cover = cover.replace(PH_ARC, arc).replace(
        PH_LANDING, safe(json.dumps(digest, separators=(",", ":")))
    )
    (DIST / "landing.html").write_text(cover, encoding="utf-8")

    cs = (DIST / "index.html").stat().st_size
    ls = (DIST / "landing.html").stat().st_size
    log(f"console -> {DIST / 'index.html'} ({cs / 1e6:.2f} MB)")
    log(f"cover   -> {DIST / 'landing.html'} ({ls / 1e3:.0f} kB)")
    if cs > 15e6:
        log("WARNING over 15 MB; the artifact limit is 16 MB - trim MAX_ACCOUNT_ROWS")


if __name__ == "__main__":
    main()
