"""
Clean RESULTS_KSA_*.csv to remove false-KSA entries.

The GCC API gives clean `noc_code` so most opponents are filtered already
by the scraper. This is a defensive second pass for edge cases:

  1. "vs KSA" / "vs SAU" / "vs Saudi" patterns in the Result text
  2. Opponent-name allowlist (starts empty - add names as we encounter them)

Reads the most recent RESULTS_KSA_*.csv, writes RESULTS_KSA_CLEAN_<ts>.csv.

Usage:
    python ksa_filter.py
    python ksa_filter.py --file path/to/file.csv
"""
from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

from config import RESULTS_DIR

# Add opponent surnames here as you encounter them in QA.
# Match is case-insensitive substring on the Athlete column.
OPPONENT_NAMES: list[str] = [
    # e.g. "GYLYJOV", "ENKHSAIKHAN" - empty for GCC until we see a false positive
]

VS_KSA_RE = re.compile(r"\bvs\s+(ksa|sau|saudi(\s+arabia)?)\b|\bagainst\s+(ksa|sau|saudi)\b", re.I)


def is_opponent(row: dict) -> tuple[bool, str]:
    """Return (is_opponent, reason)."""
    athlete = (row.get("Athlete") or "")
    result  = (row.get("Result")  or "")

    if VS_KSA_RE.search(result):
        return True, "vs-KSA pattern in Result"
    if OPPONENT_NAMES:
        athlete_upper = athlete.upper()
        for name in OPPONENT_NAMES:
            if name.upper() in athlete_upper:
                return True, f"opponent allowlist: {name}"
    return False, ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", help="Specific input CSV (defaults to latest RESULTS_KSA)")
    args = p.parse_args()

    src = Path(args.file) if args.file else (
        sorted(RESULTS_DIR.glob("RESULTS_KSA_*.csv"), reverse=True) or [None]
    )[0]
    if not src:
        raise SystemExit("No RESULTS_KSA_*.csv found")
    print(f"[LOAD] {src.name}")

    rows = list(csv.DictReader(src.open(encoding="utf-8-sig")))
    print(f"  {len(rows)} KSA rows in")

    kept, removed = [], []
    for r in rows:
        bad, why = is_opponent(r)
        (removed if bad else kept).append((r, why))

    if not removed:
        print("  no false-KSA entries detected; nothing to clean")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = src.parent / f"RESULTS_KSA_CLEAN_{ts}.csv"
    fields = list(rows[0].keys())
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(r for r, _ in kept)
    print(f"[SAVE] {out.name}  ({len(kept)} kept, {len(removed)} removed)")

    print("\nRemoved:")
    for r, why in removed[:20]:
        print(f"  - {r.get('Athlete', ''):28s} | {r.get('Sport', ''):15s} | {why}")
    if len(removed) > 20:
        print(f"  ...and {len(removed) - 20} more")


if __name__ == "__main__":
    main()
