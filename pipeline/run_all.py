"""Run the whole pipeline in order.

    python pipeline/run_all.py                 # everything, reusing the raw cache
    python pipeline/run_all.py --from 3        # restart at step 3
    python pipeline/run_all.py --only 7 8      # just those steps
    python pipeline/run_all.py --skip-fetch    # never touch the network

Steps 1 and 2a hit the network; everything after is local and deterministic.
Re-running is safe: the raw cache is only re-downloaded with --force-fetch.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

STEPS = [
    (1, "fetch_usaspending", "USAspending bulk download -> data/raw", True),
    (2, "fetch_backblaze", "Backblaze hazard curve (optional, REAL)", True),
    (3, "fetch_sec_benchmarks", "SEC filing benchmarks (benchmark only)", True),
    (4, "build_awards", "flatten downloads -> awards_raw.parquet", False),
    (5, "entity_resolution", "entity resolution + match report", False),
    (6, "install_base", "accounts, coverage, renewals, cohorts, whitespace", False),
    (7, "subawards", "prime->sub graph, vendor network, channel corroboration", False),
    (8, "provenance", "field-level provenance table", False),
    (9, "synthetic_layer", "SYNTHETIC telemetry, cases, health", False),
    (10, "plays", "reason codes and play assignment", False),
    (11, "survival", "survival model, time-based validation, calibration", False),
    (12, "ranking", "expected-value ranking + sensitivity", False),
    (13, "export_app_data", "bundle app/data/app_data.json", False),
    (14, "build_app", "inline the bundle -> app/dist/index.html", False),
    (15, "make_docs", "regenerate ASSUMPTIONS.md, docs/PROVENANCE.md, README tables", False),
]


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def run_step(n: int, mod: str, desc: str, extra: list) -> bool:
    log(f"=== step {n}: {mod} - {desc}")
    t0 = time.time()
    r = subprocess.run([PY, str(HERE / f"{mod}.py"), *extra], cwd=str(HERE.parent))
    dt = time.time() - t0
    if r.returncode != 0:
        log(f"=== step {n} FAILED after {dt:.1f}s (exit {r.returncode})")
        return False
    log(f"=== step {n} done in {dt:.1f}s")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the install-base pipeline")
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--only", nargs="*", type=int)
    ap.add_argument("--skip-fetch", action="store_true", help="skip every network step")
    ap.add_argument("--force-fetch", action="store_true", help="re-download the raw cache")
    ap.add_argument("--stop-on-error", action="store_true", default=True)
    ap.add_argument("--test", action="store_true",
                    help="run the invariant checks after the pipeline")
    a = ap.parse_args()

    t0 = time.time()
    failed = []
    for n, mod, desc, is_net in STEPS:
        if a.only and n not in a.only:
            continue
        if not a.only and n < a.start:
            continue
        if is_net and a.skip_fetch:
            log(f"=== step {n}: {mod} skipped (--skip-fetch)")
            continue
        extra = ["--force"] if (a.force_fetch and mod == "fetch_usaspending") else []
        if not run_step(n, mod, desc, extra):
            failed.append(n)
            if a.stop_on_error and mod in ("build_awards", "entity_resolution",
                                           "install_base"):
                log("stopping: later steps depend on this one")
                break
    log(f"pipeline finished in {time.time() - t0:.1f}s"
        + (f"; FAILED steps: {failed}" if failed else "; all steps OK"))

    if a.test:
        log("=== invariant checks")
        r = subprocess.run([PY, str(HERE.parent / "tests" / "test_invariants.py")],
                           cwd=str(HERE.parent))
        if r.returncode != 0:
            failed.append("invariants")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
