"""Step 2b - BSEE offshore platform data -> data/raw.

Input : nothing (network)
Output: data/raw/bsee/<name>.zip          the archives as served
        data/raw/bsee/<name>/<file>       extracted
        data/raw/manifest.json            same manifest the federal fetch writes

Three files, all public domain, all refreshed by BSEE roughly daily:

    platstrufixed        Platform Structures  - one row per STRUCTURE, carries
                         Install Date and Removal Date: the entry and the event
    platmastfixed        Platform Masters     - one row per COMPLEX, carries
                         water depth, lease, operator, Abandon Flag
    platstruremdelimit   Structures Removed   - the removal applications

WHY THE MAGIC-NUMBER CHECK MATTERS. BSEE serves a styled HTML error page, with
HTTP 200, for any filename it does not recognise. The plausible spelling of the
structures file - platstrucfixed.zip, with the c - is one of those: it returns
200 and 28 kB of HTML that a status-code-only fetcher writes out as data and
reports as a success. Every response here is checked for the PK zip magic
number before it is allowed near the cache, and the row count is recorded in
the manifest so a silent truncation upstream shows up as a diff rather than as
a quietly smaller model.

Run:
    python pipeline/fetch_bsee.py              # reuse the cache
    python pipeline/fetch_bsee.py --force      # re-download
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

RAW = C.RAW / "bsee"
RAW.mkdir(parents=True, exist_ok=True)

TIMEOUT = 120
RETRIES = 4
BACKOFF = 4.0
UA = C.USER_AGENT

LICENCE = ("US Government work, public domain (17 U.S.C. 105). "
           "Bureau of Safety and Environmental Enforcement open data.")


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def get(url: str) -> bytes:
    """Fetch, retrying on transport errors. Content is validated by the caller."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
            r.raise_for_status()
            return r.content
        except Exception as e:                                   # noqa: BLE001
            last = e
            if attempt < RETRIES:
                wait = BACKOFF * attempt
                log(f"    attempt {attempt} failed ({type(e).__name__}); retrying in {wait:.0f}s")
                time.sleep(wait)
    raise RuntimeError(f"giving up on {url}: {last}")


def is_zip(blob: bytes) -> bool:
    """PK\\x03\\x04, or the empty-archive / spanned variants."""
    return blob[:2] == b"PK" and blob[2:4] in (b"\x03\x04", b"\x05\x06", b"\x07\x08")


def describe_html(blob: bytes) -> str:
    head = blob[:400].decode("latin-1", "replace").replace("\n", " ")
    return " ".join(head.split())[:160]


def fetch_one(name: str, path: str, force: bool) -> dict:
    url = C.BSEE_BASE + path
    dest = RAW / f"{name}.zip"
    outdir = RAW / name

    if dest.exists() and not force:
        blob = dest.read_bytes()
        log(f"  {name}: cached ({len(blob) / 1e6:.1f} MB)")
    else:
        log(f"  {name}: GET {url}")
        blob = get(url)
        if not is_zip(blob):
            # The 200-with-HTML case. Refuse it loudly rather than caching it.
            raise RuntimeError(
                f"{name}: {url} returned {len(blob)} bytes that are not a zip archive. "
                f"BSEE serves HTTP 200 with an HTML error page for unknown filenames, "
                f"so this is almost certainly a wrong path, not an outage. "
                f"First bytes: {describe_html(blob)!r}")
        dest.write_bytes(blob)
        log(f"    saved {dest.name} ({len(blob) / 1e6:.1f} MB)")

    sha = hashlib.sha256(blob).hexdigest()

    outdir.mkdir(parents=True, exist_ok=True)
    members = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            target = outdir / Path(info.filename).name
            target.write_bytes(z.read(info))
            rows = sum(1 for line in target.read_bytes().splitlines() if line.strip())
            members.append({"name": target.name, "bytes": info.file_size, "rows": rows})
            log(f"    -> {target.name}  {rows:,} rows")

    return {
        "kind": "bsee",
        "url": url,
        "layout": C.BSEE_FILES[name][1],
        "layout_url": f"{C.BSEE_BASE}/Main/HtmlPage.aspx?page={C.BSEE_FILES[name][1]}",
        "bytes": len(blob),
        "sha256": sha,
        "members": members,
        "rows": sum(m["rows"] for m in members),
        "retrieved": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "licence": LICENCE,
    }


def run(force: bool = False) -> None:
    manifest = json.loads(C.MANIFEST.read_text()) if C.MANIFEST.exists() else {}
    manifest.setdefault("downloads", {})
    bsee = manifest.setdefault("bsee", {})

    log(f"BSEE offshore fetch - {len(C.BSEE_FILES)} files -> {RAW}")
    for name, (path, _layout) in C.BSEE_FILES.items():
        bsee[name] = fetch_one(name, path, force)

    manifest["bsee_meta"] = {
        "source": "Bureau of Safety and Environmental Enforcement (BSEE) open data",
        "base": C.BSEE_BASE,
        "licence": LICENCE,
        "region": "US Outer Continental Shelf, Gulf of Mexico",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "files": len(bsee),
            "rows": sum(v["rows"] for v in bsee.values()),
            "bytes": sum(v["bytes"] for v in bsee.values()),
        },
    }
    C.MANIFEST.write_text(json.dumps(manifest, indent=2))
    t = manifest["bsee_meta"]["totals"]
    log(f"manifest -> {C.MANIFEST}")
    log(f"done: {t['files']} files, {t['rows']:,} rows, {t['bytes'] / 1e6:.1f} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch BSEE offshore platform data")
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    a = ap.parse_args()
    run(force=a.force)
