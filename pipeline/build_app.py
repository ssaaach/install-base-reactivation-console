"""Step 9b - Inline the data bundle into the console and write app/dist/index.html.

The console reads its data from a `<script type="application/json">` block rather
than fetching a sibling file, so the published page has no runtime dependency on
anything at all.

    app/index.html          the authored template, with an __APP_DATA__ placeholder
    app/data/app_data.json  the bundle from export_app_data.py
    app/dist/index.html     the file that gets published
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

TEMPLATE = C.ROOT / "app" / "index.html"
DATA = C.APP_DATA / "app_data.json"
DIST = C.ROOT / "app" / "dist" / "index.html"
PLACEHOLDER = "__APP_DATA__"


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def main() -> None:
    if not TEMPLATE.exists():
        raise SystemExit(f"missing {TEMPLATE}")
    if not DATA.exists():
        raise SystemExit(f"missing {DATA} - run export_app_data.py first")

    html = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"{TEMPLATE} has no {PLACEHOLDER} placeholder")
    data = DATA.read_text(encoding="utf-8")

    # A literal "</script>" inside the JSON would close the block early. JSON
    # escapes it harmlessly as <\/script>, which JSON.parse reads back identically.
    data = data.replace("</", "<\\/")

    DIST.parent.mkdir(parents=True, exist_ok=True)
    DIST.write_text(html.replace(PLACEHOLDER, data), encoding="utf-8")

    size = DIST.stat().st_size
    log(f"console -> {DIST} ({size / 1e6:.2f} MB)")
    if size > 15e6:
        log("WARNING over 15 MB; the artifact limit is 16 MB - trim MAX_ACCOUNT_ROWS")


if __name__ == "__main__":
    main()
