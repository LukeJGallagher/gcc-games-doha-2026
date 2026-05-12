"""
Compare the two most recent scrape pulls and surface what changed.

Looks at RESULTS_ALL_*.csv pairs (latest vs previous) and reports:

  + new athlete entries        (Event_ID + Athlete first appears)
  ~ status changes             (Scheduled -> Live -> Completed)
  ! result changes             (Rank or Result populated where it was empty)
  M new medals                 (Medal field populated)
  - dropped entries            (was there last pull, not now)

Also shows day-by-day breakdown so you can see "what's new for May 12".

Usage:
    python daily_diff.py                       # latest vs previous RESULTS_ALL
    python daily_diff.py --ksa                 # latest vs previous RESULTS_KSA
    python daily_diff.py --since YYYYMMDD_HHMMSS    # everything since this pull
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from config import RESULTS_DIR


def load(p: Path) -> dict[tuple[str, str], dict]:
    """key = (Event_ID, Athlete) -> row"""
    rows = {}
    for r in csv.DictReader(p.open(encoding="utf-8-sig")):
        key = (r.get("Source_URL", "").split("/")[-1] or r.get("Event_ID", ""),
               r.get("Athlete", ""))
        rows[key] = r
    return rows


def find_pair(args) -> tuple[Path, Path]:
    pattern = "RESULTS_KSA_*.csv" if args.ksa else "RESULTS_ALL_*.csv"
    files = sorted(RESULTS_DIR.glob(pattern), reverse=True)
    files = [f for f in files if "CLEAN" not in f.name and "ENHANCED" not in f.name]
    if len(files) < 2:
        raise SystemExit(f"Need at least 2 {pattern} files; found {len(files)}")

    if args.since:
        prev = next((f for f in files if args.since in f.name), None)
        if not prev:
            raise SystemExit(f"No file matching --since {args.since}")
        return files[0], prev
    return files[0], files[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ksa", action="store_true")
    p.add_argument("--since", help="Compare latest against pull with this timestamp")
    args = p.parse_args()

    new_path, old_path = find_pair(args)
    print(f"[NEW] {new_path.name}")
    print(f"[OLD] {old_path.name}")
    new = load(new_path)
    old = load(old_path)

    added   = [k for k in new if k not in old]
    dropped = [k for k in old if k not in new]
    common  = [k for k in new if k in old]

    status_changed: list[tuple] = []
    result_appeared: list[tuple] = []
    new_medals:     list[tuple] = []
    for k in common:
        n, o = new[k], old[k]
        if (n.get("Status") or "") != (o.get("Status") or ""):
            status_changed.append((k, o.get("Status"), n.get("Status")))
        if not (o.get("Rank") or o.get("Result")) and (n.get("Rank") or n.get("Result")):
            result_appeared.append((k, n.get("Rank"), n.get("Result")))
        if not (o.get("Medal") or "") and (n.get("Medal") or ""):
            new_medals.append((k, n.get("Medal")))

    # ---- summary ----
    print(f"\n{'='*60}")
    print(f"  + {len(added):4d} new entries")
    print(f"  - {len(dropped):4d} dropped entries")
    print(f"  ~ {len(status_changed):4d} status changes")
    print(f"  ! {len(result_appeared):4d} new results")
    print(f"  M {len(new_medals):4d} new medals")
    print(f"{'='*60}")

    def show(label, items, fmt):
        if not items:
            return
        print(f"\n{label} ({len(items)}):")
        for x in items[:25]:
            print("  " + fmt(x))
        if len(items) > 25:
            print(f"  ...and {len(items) - 25} more")

    show("+ NEW ENTRIES", added,
         lambda k: f"{k[0]:14s} {new[k].get('Sport',''):15s} {new[k].get('Country','') or '?':4s} {k[1] or new[k].get('Discipline','')}")
    show("M NEW MEDALS", new_medals,
         lambda x: f"{x[1]} -> {new[x[0]].get('Athlete'):25s} {new[x[0]].get('Sport',''):15s} {new[x[0]].get('Discipline','')}")
    show("! NEW RESULTS", result_appeared,
         lambda x: f"R{x[1] or '?':4s} {x[2] or '':10s} | {new[x[0]].get('Athlete'):25s} {new[x[0]].get('Sport',''):15s} {new[x[0]].get('Discipline','')}")
    show("~ STATUS CHANGES", status_changed,
         lambda x: f"{x[1]:12s} -> {x[2]:12s} | {new[x[0]].get('Athlete'):25s} {new[x[0]].get('Sport',''):15s} {new[x[0]].get('Discipline','')}")
    show("- DROPPED", dropped,
         lambda k: f"{k[0]:14s} {old[k].get('Sport',''):15s} {old[k].get('Country','') or '?':4s} {k[1] or old[k].get('Discipline','')}")

    # ---- per-day breakdown of new entries ----
    if added:
        by_day = defaultdict(int)
        for k in added:
            by_day[new[k].get("Date", "?")] += 1
        print("\nNew entries by date:")
        for d, n in sorted(by_day.items()):
            print(f"  {d} : {n}")


if __name__ == "__main__":
    main()
