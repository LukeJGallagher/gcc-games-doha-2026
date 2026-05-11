"""
4th GCC Games Doha 2026 - Scraper
=================================
Uses the public Frappe JSON API at gccgames.qa (no auth required, no Selenium).
Endpoints discovered live on 2026-05-09.

Outputs:
    data/schedule/SCHEDULE_<ts>.csv             full grid schedule
    data/schedule/SCHEDULE_GRID_<ts>.json       raw API dump (for debugging)
    data/results/RESULTS_ALL_<ts>.csv           one row per athlete-event (all NOCs)
    data/results/RESULTS_KSA_<ts>.csv           KSA-only filter
    data/results/MEDALS_<ts>.csv                live medal table
    logs/scrape_<ts>.log                        run log

Usage:
    python scraper.py                       # full pull (schedule + results + medals)
    python scraper.py --mode schedule       # just the schedule
    python scraper.py --mode results        # just results (per-sport)
    python scraper.py --mode medals         # just medal table
    python scraper.py --sports Athletics Swimming   # subset
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from api_client import GccApi
from config import (
    SPORTS, COMPETITION_NAME, COMP_SET, KSA_CODES,
    RESULTS_DIR, SCHEDULE_DIR, LOGS_DIR,
    RESULTS_COLUMNS, SCHEDULE_COLUMNS,
)
from duration import estimate_duration_minutes, add_minutes

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / f"scrape_{ts}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("gcc")

api = GccApi()
SCHEDULE_URL = "https://gccgames.qa/frontend/schedule-competition"
SPORT_URL_TPL = "https://gccgames.qa/frontend/sport/{slug}"


def _split_bilingual(text: str) -> tuple[str, str]:
    """API titles are 'English Name - Arabic Name'. Split into (en, ar).

    Splits on the first ' - ' followed by Arabic characters.
    Returns (text, '') if no Arabic suffix is present.
    """
    if not text:
        return "", ""
    text = text.strip()
    if " - " not in text:
        return text, ""
    head, _, tail = text.partition(" - ")
    if any(ord(c) > 127 for c in tail):
        return head.strip(), tail.strip()
    return text, ""


# ---------------------------------------------------------------------------
# Per-sport pull -> drives both schedule and results
# ---------------------------------------------------------------------------
def _participant_rows(comp: dict, sport: str) -> list[dict]:
    """Flatten one competition's participants into result rows.

    Handles two shapes seen in the API:
      - team sports: {id: TEAM-XXXX, noc_code, noc_name, final_result, pos}
      - individual : {id, noc_code, athlete: {english_name, arabic_name}, result, rank}
    Uses results_summary.participants (with results) when present,
    falls back to top-level participants (entries only, no result yet).
    """
    rs   = comp.get("results_summary") or {}
    rich = rs.get("participants") or []
    thin = comp.get("participants") or []

    # merge: prefer rich entry per id, fill in with thin
    by_id: dict[str, dict] = {p.get("id", f"_{i}"): p for i, p in enumerate(thin)}
    for p in rich:
        by_id[p.get("id", f"_r{id(p)}")] = p   # rich version wins
    if not by_id:
        return []

    sport_url = SPORT_URL_TPL.format(slug=sport.lower().replace(" ", "-"))
    title_en, title_ar = _split_bilingual(comp.get("title", ""))
    if not title_ar:
        title_ar = comp.get("title_ar", "")
    base = {
        "Sport":            sport,
        "Date":             comp.get("date", ""),
        "Competition":      COMPETITION_NAME,
        "Comp Set":         COMP_SET,
        "Class":            "",
        "Discipline":       title_en,
        "Discipline_AR":    title_ar,
        "Phase":            comp.get("stage_name", ""),
        "Gender":           comp.get("gender_category", ""),
        "Age":              "",
        "Wind":             "",
        "Attempt":          "",
        "Status":           comp.get("status", ""),
        "Detection_Method": "GCC API",
        "Source_URL":       sport_url + "/" + comp.get("id", ""),
    }
    out: list[dict] = []
    for p in by_id.values():
        athlete = p.get("athlete") or {}
        # Individual sports use `player_name` flat on the participant.
        # Team sports use TEAM-XXXX id + noc_name. Try the most-specific
        # field first and fall back through legacy shapes.
        athlete_name = (
            p.get("player_name")
            or athlete.get("english_name")
            or p.get("english_name")
            or p.get("name")
            or (f"{p.get('noc_name', '')} (Team)" if p.get("id", "").startswith("TEAM-") else "")
        )
        result = p.get("final_result")
        if result is None:
            result = p.get("result") or p.get("score") or p.get("time") or ""
        rank = p.get("pos") or p.get("rank") or p.get("position") or ""
        out.append({
            **base,
            "Athlete":  athlete_name,
            "Country":  p.get("noc_code") or p.get("noc") or athlete.get("noc_code") or "",
            "Rank":     str(rank) if rank != "" else "",
            "Result":   str(result) if result != "" else "",
            "Medal":    (p.get("medal") or "")[:1].upper(),
        })
    return out


def _schedule_row(comp: dict, sport: str, country_entries: list[str]) -> dict:
    title_en, title_ar = _split_bilingual(comp.get("title", ""))
    if not title_ar:
        title_ar = comp.get("title_ar", "")
    phase    = comp.get("stage_name", "")
    start    = comp.get("time", "")
    duration = estimate_duration_minutes(sport, title_en, phase)
    return {
        "Date":            comp.get("date", ""),
        "Time":            start,
        "Time_End":        add_minutes(start, duration),
        "Duration_Min":    duration,
        "Sport":           sport,
        "Discipline":      title_en,
        "Discipline_AR":   title_ar,
        "Phase":           phase,
        "Gender":          comp.get("gender_category", ""),
        "Venue":           comp.get("venue", ""),
        "Country_Entries": ",".join(sorted(set(country_entries))),
        "Event_ID":        comp.get("id", ""),
        "Source_URL":      SCHEDULE_URL,
    }


def pull_per_sport(sports: list[str]) -> tuple[list[dict], list[dict], dict]:
    """Single API pass per sport. Returns (schedule_rows, result_rows, raw_dump)."""
    schedule_rows: list[dict] = []
    result_rows:   list[dict] = []
    raw_dump:      dict       = {}

    for sport in sports:
        try:
            comps = api.sport_results_summary(sport=sport)
        except Exception as e:
            log.warning("skip %s: %s", sport, e)
            continue
        raw_dump[sport] = comps
        n_athletes = 0
        for comp in comps:
            entries = [
                p.get("noc_code", "")
                for p in (comp.get("participants") or [])
                if p.get("noc_code")
            ]
            schedule_rows.append(_schedule_row(comp, sport, entries))
            rows = _participant_rows(comp, sport)
            n_athletes += len(rows)
            result_rows.extend(rows)
        log.info("  %-18s %3d competitions, %3d athlete rows",
                 sport, len(comps), n_athletes)
    return schedule_rows, result_rows, raw_dump


def write_schedule(rows: list[dict]) -> Path:
    out = SCHEDULE_DIR / f"SCHEDULE_{ts}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SCHEDULE_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    log.info("schedule -> %s (%d rows, %d sports)", out, len(rows),
             len({r["Sport"] for r in rows}))
    return out


def write_results(rows: list[dict]) -> tuple[Path, Path]:
    all_path = RESULTS_DIR / f"RESULTS_ALL_{ts}.csv"
    ksa_path = RESULTS_DIR / f"RESULTS_KSA_{ts}.csv"
    _write_results(rows, all_path)
    _write_results(
        [r for r in rows if r.get("Country", "").upper() in {c.upper() for c in KSA_CODES}],
        ksa_path,
    )
    return all_path, ksa_path


def _write_results(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=RESULTS_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({col: r.get(col, "") for col in RESULTS_COLUMNS})
    log.info("results -> %s (%d rows)", path, len(rows))


# ---------------------------------------------------------------------------
# Medals
# ---------------------------------------------------------------------------
def scrape_medals() -> Path:
    log.info("Fetching medal_standings...")
    medals = api.medal_standings()
    out = RESULTS_DIR / f"MEDALS_{ts}.csv"
    cols = ["Rank", "NOC", "Country", "Gold", "Silver", "Bronze", "Total"]
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for m in medals:
            w.writerow({
                "Rank":    m.get("rank", ""),
                "NOC":     m.get("noc", ""),
                "Country": m.get("name", ""),
                "Gold":    m.get("gold", 0),
                "Silver":  m.get("silver", 0),
                "Bronze":  m.get("bronze", 0),
                "Total":   m.get("total", 0),
            })
    log.info("medals -> %s (%d nations)", out, len(medals))
    return out


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["all", "schedule", "results", "medals"], default="all")
    p.add_argument("--sports", nargs="+", help="Limit to specific sport names (exact API ids)")
    args = p.parse_args()

    if args.sports:
        sports = args.sports
    else:
        try:
            sports = [s["id"] for s in api.sports()]
            log.info("sport list (live): %d", len(sports))
        except Exception as e:
            log.warning("could not pull live sport list: %s -- using fallback", e)
            sports = SPORTS

    if args.mode in ("all", "schedule", "results"):
        log.info("Pulling per-sport data for %d sports...", len(sports))
        sched_rows, res_rows, raw = pull_per_sport(sports)
        # raw dump for forensic debugging
        raw_path = SCHEDULE_DIR / f"RAW_{ts}.json"
        raw_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("raw dump -> %s", raw_path)

        if args.mode in ("all", "schedule"):
            write_schedule(sched_rows)
        if args.mode in ("all", "results"):
            write_results(res_rows)

    if args.mode in ("all", "medals"):
        scrape_medals()


if __name__ == "__main__":
    main()
