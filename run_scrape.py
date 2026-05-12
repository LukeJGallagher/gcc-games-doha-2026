"""
Manual on-demand runner for the GCC Games scraper + post-processing.

Usage:
    python run_scrape.py                            # full pull (schedule + results + medals)
    python run_scrape.py --schedule-only
    python run_scrape.py --results-only
    python run_scrape.py --sports Athletics Swimming
    python run_scrape.py --diff                     # diff latest two pulls
    python run_scrape.py --enhance                  # standardise rank codes
    python run_scrape.py --clean                    # filter false-KSA opponents
    python run_scrape.py --full                     # scrape + diff + enhance + clean
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def run(*cmd) -> int:
    print(f"\n>>> {' '.join(cmd)}")
    return subprocess.call([sys.executable, *cmd], cwd=HERE)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--schedule-only", action="store_true")
    p.add_argument("--results-only", action="store_true")
    p.add_argument("--sports", nargs="+")

    p.add_argument("--diff",    action="store_true", help="Show what's new since last pull")
    p.add_argument("--enhance", action="store_true", help="Standardise rank codes")
    p.add_argument("--clean",   action="store_true", help="Filter false-KSA opponents")
    p.add_argument("--match",   action="store_true", help="Match KSA roster to schedule (one row per athlete-phase)")
    p.add_argument("--full",    action="store_true",
                   help="Scrape + match + diff + enhance + clean in one go")
    args = p.parse_args()

    # post-only modes (skip the scrape)
    if args.diff and not args.full:    return run("daily_diff.py")
    if args.enhance and not args.full: return run("enhance_rankings.py", "--ksa")
    if args.clean and not args.full:   return run("ksa_filter.py")
    if args.match and not args.full:   return run("match_athletes.py")

    # FIRST: check schedule freshness vs API (what's been added / dropped / shifted)
    if args.full:
        run("schedule_check.py")

    # scrape
    cmd = ["scraper.py"]
    if args.schedule_only:   cmd += ["--mode", "schedule"]
    elif args.results_only:  cmd += ["--mode", "results"]
    else:                    cmd += ["--mode", "all"]
    if args.sports:          cmd += ["--sports", *args.sports]
    rc = run(*cmd)
    if rc != 0 or not args.full:
        return rc

    # full pipeline tail
    run("enrich_from_isg.py")    # rebuild ISG enrichment from sibling folder (no-op in cloud)
    run("match_athletes.py")
    run("daily_diff.py")
    run("enhance_rankings.py", "--ksa")
    run("ksa_filter.py")
    run("download_photos.py", "--api-only")
    run("download_assets.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
