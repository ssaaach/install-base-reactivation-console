"""One-off discovery: dump the PSC branches relevant to enterprise storage.

Not part of the production pipeline. Run to justify the code list in config.py.
"""
import json
import sys

import requests

BASE = "https://api.usaspending.gov/api/v2/references/filter_tree/psc"
S = requests.Session()
S.headers.update({"User-Agent": "install-base-reactivation-console/2.0 (research)"})


def tree(path: str, depth: int = 1):
    url = f"{BASE}/{path}/?depth={depth}" if path else f"{BASE}/?depth={depth}"
    r = S.get(url, timeout=90)
    r.raise_for_status()
    return r.json()["results"]


def walk(nodes, prefix=""):
    for n in nodes:
        print(f"{prefix}{n['id']:<8} {n.get('description', '')[:95]}  (n={n.get('count')})")
        if n.get("children"):
            walk(n["children"], prefix + "    ")


if __name__ == "__main__":
    for path in sys.argv[1:]:
        print(f"\n===== {path} =====")
        try:
            walk(tree(path, depth=1))
        except Exception as e:  # noqa: BLE001
            print(f"  !! {e}")
