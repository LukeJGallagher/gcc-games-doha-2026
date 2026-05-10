"""
Post-process RESULTS_*.csv to add a database-friendly Rank_Std column.

Maps the API's numeric `pos` + `stage_name` (Phase) to standardised codes:
    1, 2, 3                  - podium placements
    Q                        - qualified to next round
    DNQ, DNS, DNF, DQ        - did-not codes
    R64, R32, R16            - lost in that round
    QF, SF, F                - lost in quarter/semi/final
    GROUP                    - group-stage placement unclear

Reads the most recent RESULTS_ALL_*.csv (or RESULTS_KSA_*.csv with --ksa)
and writes ENHANCED_<source>_<ts>.csv alongside it.

Usage:
    python enhance_rankings.py             # most recent RESULTS_ALL
    python enhance_rankings.py --ksa       # most recent RESULTS_KSA
    python enhance_rankings.py --file path/to/file.csv
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from config import RESULTS_DIR


def standardise_rank(row: dict) -> str:
    """Return a standardised rank code from a result row."""
    phase  = (row.get("Phase") or "").strip().upper()
    rank   = (row.get("Rank")  or "").strip()
    result = (row.get("Result") or "").strip().upper()
    medal  = (row.get("Medal") or "").strip().upper()
    status = (row.get("Status") or "").strip().upper()

    # Status codes from API trump everything
    for code in ("DNS", "DNF", "DQ", "DNQ"):
        if status == code or code in result:
            return code

    # Medal trumps everything else
    if medal in {"G", "GOLD"}:    return "1"
    if medal in {"S", "SILVER"}:  return "2"
    if medal in {"B", "BRONZE"}:  return "3"

    # Numeric rank in a final = podium logic; elsewhere keep numeric
    if rank.isdigit():
        n = int(rank)
        if "FINAL" in phase and "SEMI" not in phase and "QUARTER" not in phase:
            if n <= 3:
                return str(n)
        return str(n)

    # Round-by-round elimination
    if "ROUND OF 64" in phase or phase in {"R64", "ROUND 64"}:
        return "Q" if _won(result) else "R64"
    if "ROUND OF 32" in phase or phase in {"R32", "ROUND 32"}:
        return "Q" if _won(result) else "R32"
    if "ROUND OF 16" in phase or phase in {"R16", "ROUND 16"}:
        return "Q" if _won(result) else "R16"
    if "QUARTER" in phase or phase == "QF":
        return "Q" if _won(result) else "QF"
    if "SEMI" in phase:
        # Bronze match in many sports = semi loser still gets bronze
        if "BRONZE" in result or "3RD" in result:
            return "3"
        return "Q" if _won(result) else "SF"
    if "FINAL" in phase:
        if _won(result) or "GOLD" in result:
            return "1"
        if "SILVER" in result or "LOST" in result or "LOSS" in result:
            return "2"
        return "F"

    # Qualifying / preliminary / heats
    if any(k in phase for k in ("QUALIF", "PRELIM", "HEAT", "TRAINING")):
        if _won(result) or "QUALIFIED" in result:
            return "Q"
        if result and not _is_blank(result):
            return rank or "Q"   # has a time/score
        return ""

    # Group stage
    if "GROUP" in phase:
        if _won(result) or "ADVANCED" in result:
            return "Q"
        if "LOST" in result or "ELIMINATED" in result:
            return "DNQ"
        return rank or "GROUP"

    return rank


def _won(result: str) -> bool:
    return any(k in result for k in ("WON", "WIN", "QUALIFIED")) and "LOST" not in result


def _is_blank(s: str) -> bool:
    return not s or s in {"NAN", "NONE", "-"}


# ---------------------------------------------------------------------------
def find_input(args) -> Path:
    if args.file:
        return Path(args.file)
    pattern = "RESULTS_KSA_*.csv" if args.ksa else "RESULTS_ALL_*.csv"
    files = sorted(RESULTS_DIR.glob(pattern), reverse=True)
    if not files:
        raise SystemExit(f"No file matching {pattern} in {RESULTS_DIR}")
    return files[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ksa", action="store_true", help="Use most recent RESULTS_KSA file")
    p.add_argument("--file", help="Specific input CSV path")
    args = p.parse_args()

    src = find_input(args)
    print(f"[LOAD] {src.name}")
    rows = list(csv.DictReader(src.open(encoding="utf-8-sig")))
    print(f"  {len(rows)} rows")

    before = Counter((r.get("Rank") or "").strip() for r in rows)
    for r in rows:
        r["Rank_Original"] = r.get("Rank", "")
        r["Rank_Std"]      = standardise_rank(r)
    after = Counter(r["Rank_Std"] for r in rows)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = src.parent / f"ENHANCED_{src.stem}_{ts}.csv"
    fields = list(rows[0].keys()) if rows else []
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"[SAVE] {out.name}")

    print("\nBefore:", dict(before.most_common()))
    print("After: ", dict(after.most_common()))
    changed = sum(1 for r in rows if r["Rank_Std"] != r["Rank_Original"])
    print(f"Changed: {changed}/{len(rows)}")


if __name__ == "__main__":
    main()
