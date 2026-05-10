"""
Join the KSA roster (athletes + entered events) to the live schedule,
expanding each athlete-event pair into one row per scheduled phase.

Inputs:
    KSA_GCC2026_Athletes_Events_*.xlsx   (Excel roster, 277 athlete-event rows)
    data/schedule/SCHEDULE_*.csv         (latest API pull)

Outputs:
    data/results/KSA_ATHLETE_SCHEDULE_<ts>.csv   one row per (athlete, competition)
    data/results/UNMATCHED_EVENTS_<ts>.csv       Excel events with no schedule hit (review)

Usage:
    python match_athletes.py
    python match_athletes.py --roster path/to/roster.xlsx
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import RESULTS_DIR, SCHEDULE_DIR

DEFAULT_ROSTER_GLOB = "KSA_GCC2026_Athletes_Events*.xlsx"

OUTPUT_COLUMNS = [
    "Given Name", "Family Name", "Date of Birth",
    "Sport", "Event", "Phase", "Date", "Time Start",
    "Discipline_API", "Event_ID", "Venue", "Gender",
    "Match_Type", "Source_URL",
]

# ---------------------------------------------------------------------------
# Normalisation: collapse Excel and API event names to a comparable form
# ---------------------------------------------------------------------------
# Keep ASCII hyphen "-" so we preserve weight-class signs (+58, -58, +67 etc).
# Strip curly quotes, parens, en/em-dashes, underscores, slashes, commas, periods.
_PUNCT = re.compile(r"[‘’“”'\"\(\)–—_/.,]")
_WS    = re.compile(r"\s+")

# Athletics: Excel "100m" → API "100 Metres", and vice versa.
_DISTANCE_M = re.compile(r"\b(\d+)\s*m(?:etres)?\b", re.I)
# Swimming: keep "100m" as one token "100m" (don't expand to metres)


# Map Excel sport names → API sport ids when they differ
SPORT_ALIASES = {
    "3x3 basketball": "Basketball 3x3",
}

# Excel "team" events that mean "all preliminary matches for that gender"
TEAM_SPORTS = {"3x3 Basketball", "Basketball 3x3", "Handball", "Padel"}

# Words to strip from Excel event before keyword extraction
NOISE_WORDS = {
    "individual",   # implicit (vs team)
    "competition",  # generic suffix in Equestrian
    "snooker",      # Excel prefixes events with sport name (Snooker Singles)
    "kumite",       # Karate qualifier already handled in normalise
}


def normalise(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    t = _PUNCT.sub(" ", t)
    t = re.sub(r"\bmetres?\b", "m", t)
    t = re.sub(r"\bmeters?\b", "m", t)
    t = re.sub(r"\bkumite\b", "", t)
    t = re.sub(r"(\d+)\s*kg", r"\1 kg", t)
    # "100 m" → "100m" so it matches Excel "100m"
    t = re.sub(r"(\d+)\s*m\b", r"\1m", t)
    # "4 x 100m" → "4x100m"
    t = re.sub(r"(\d+)\s*x\s*(\d+)", r"\1x\2", t)
    # parenthetical heights etc. - already gone via _PUNCT
    t = _WS.sub(" ", t).strip()
    return t


def alias_sport(sport: str) -> str:
    return SPORT_ALIASES.get((sport or "").strip().lower(), (sport or "").strip())


def gender_token(event: str) -> str:
    e = event.lower()
    if "women" in e:  return "Women"
    if "mixed" in e:  return "Mixed"
    if "men" in e:    return "Men"
    return ""


def event_keywords(event: str) -> list[str]:
    n = normalise(event)
    n = re.sub(r"\b(men s|women s|men|women|mixed)\b", " ", n)
    n = _WS.sub(" ", n).strip()
    return [k for k in n.split() if len(k) > 1 and k not in NOISE_WORDS]


# ---------------------------------------------------------------------------
def load_schedule(path: Path) -> list[dict]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    # pre-compute normalised disciplines and gender for each schedule entry
    for r in rows:
        r["_norm_disc"] = normalise(r.get("Discipline", ""))
        r["_norm_full"] = normalise(f"{r.get('Sport','')} {r.get('Discipline','')}")
        r["_gender"]    = (r.get("Gender") or "").strip()
    return rows


def find_matches(athlete_row, schedule_rows: list[dict]) -> list[dict]:
    sport  = alias_sport(athlete_row["Sport"])
    event  = (athlete_row["Event"] or "").strip()
    target_gender = gender_token(event)
    keywords = event_keywords(event)
    is_team_event = bool(re.search(r"\bteam\b", event, re.I))

    matches: list[dict] = []
    # Sports where API pools genders into Mixed (Archery does this for individual + team)
    mixed_pools = sport in ("Archery",)
    for s in schedule_rows:
        if (s.get("Sport") or "").strip() != sport:
            continue
        if target_gender and s["_gender"]:
            if s["_gender"] != target_gender:
                # Mixed schedule rows can satisfy Men/Women entries when the sport pools
                if not (s["_gender"] == "Mixed" and (is_team_event or mixed_pools)):
                    continue
        disc = s["_norm_disc"]
        if all(k in disc for k in keywords):
            matches.append(s)

    # ---- Fallback 1: pure team sports - Excel "Men's Team" with no other keywords
    # match every Sport+Gender competition (handball, padel, basketball preliminaries)
    if not matches and sport in TEAM_SPORTS and is_team_event:
        for s in schedule_rows:
            if (s.get("Sport") or "").strip() != sport:
                continue
            if target_gender and s["_gender"] and s["_gender"] != target_gender:
                continue
            matches.append(s)

    # ---- Fallback 2: Archery / Shooting - match on weapon/discipline keywords only
    # (e.g. Excel "Men's Individual Compound" → API "Compound Finals - Individual & Team")
    if not matches and sport in ("Archery", "Shooting"):
        disc_kw = [k for k in keywords if k in {
            "compound", "recurve",
            "skeet", "trap", "pistol", "rifle",
            "10m", "25m", "50m",
            "air",
        }]
        for s in schedule_rows:
            if (s.get("Sport") or "").strip() != sport:
                continue
            if target_gender and s["_gender"]:
                if s["_gender"] != target_gender:
                    if not (is_team_event and s["_gender"] == "Mixed"):
                        continue
            disc = s["_norm_disc"]
            if disc_kw and all(k in disc for k in disc_kw):
                matches.append(s)

    # ---- Fallback 3: Equestrian - "Individual Competition" matches "Individual Show Jumping"
    if not matches and sport == "Equestrian":
        is_indiv = "individual" in event.lower()
        is_team  = is_team_event
        for s in schedule_rows:
            if (s.get("Sport") or "").strip() != sport:
                continue
            d = s["_norm_disc"]
            if is_indiv and "individual" in d:  matches.append(s)
            elif is_team and "team" in d:       matches.append(s)

    return matches


# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--roster", help="Path to KSA athletes-events xlsx")
    p.add_argument("--schedule", help="Path to schedule csv")
    args = p.parse_args()

    # Roster file
    roster_path = Path(args.roster) if args.roster else next(
        iter(sorted(Path(".").glob(DEFAULT_ROSTER_GLOB))), None)
    if not roster_path:
        sys.exit(f"No roster file matching {DEFAULT_ROSTER_GLOB}")
    print(f"[ROSTER]   {roster_path.name}")
    roster = pd.read_excel(roster_path)
    print(f"  {len(roster)} athlete-event rows, "
          f"{roster.groupby(['Given Name','Family Name']).ngroups} unique athletes, "
          f"{roster['Sport'].nunique()} sports")

    # Schedule
    sched_path = Path(args.schedule) if args.schedule else \
        sorted(SCHEDULE_DIR.glob("SCHEDULE_*.csv"))[-1]
    print(f"[SCHEDULE] {sched_path.name}")
    schedule = load_schedule(sched_path)
    print(f"  {len(schedule)} scheduled competitions")

    # Match
    out_rows: list[dict] = []
    unmatched: list[dict] = []
    for _, ath in roster.iterrows():
        matches = find_matches(ath, schedule)
        if not matches:
            unmatched.append({
                "Given Name":   ath.get("Given Name", ""),
                "Family Name":  ath.get("Family Name", ""),
                "Sport":        ath.get("Sport", ""),
                "Event":        ath.get("Event", ""),
                "Reason":       "no schedule match",
            })
            continue
        for s in matches:
            out_rows.append({
                "Given Name":     ath.get("Given Name", ""),
                "Family Name":    ath.get("Family Name", ""),
                "Date of Birth":  str(ath.get("Date of Birth", "") or "")[:10],
                "Sport":          alias_sport(ath.get("Sport", "")),
                "Event":          ath.get("Event", ""),
                "Phase":          s.get("Phase", ""),
                "Date":           s.get("Date", ""),
                "Time Start":     s.get("Time", ""),
                "Discipline_API": s.get("Discipline", ""),
                "Event_ID":       s.get("Event_ID", ""),
                "Venue":          s.get("Venue", ""),
                "Gender":         s.get("Gender", ""),
                "Match_Type":     "team" if "Team" in str(ath.get("Event","")) else "individual",
                "Source_URL":     s.get("Source_URL", ""),
            })

    # Write
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"KSA_ATHLETE_SCHEDULE_{ts}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        w.writerows(out_rows)
    print(f"\n[SAVE] {out.name}  ({len(out_rows)} athlete-phase rows)")

    if unmatched:
        unm = RESULTS_DIR / f"UNMATCHED_EVENTS_{ts}.csv"
        with unm.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(unmatched[0].keys()))
            w.writeheader()
            w.writerows(unmatched)
        print(f"[SAVE] {unm.name}  ({len(unmatched)} unmatched athlete-event rows)")

    # ---- summary ----
    by_sport = defaultdict(lambda: [0, 0])  # matched, unmatched
    for r in out_rows:    by_sport[r["Sport"]][0] += 1
    for r in unmatched:   by_sport[r["Sport"]][1] += 1

    print(f"\n{'Sport':18s}  matched  unmatched  total")
    for s, (m, u) in sorted(by_sport.items()):
        print(f"  {s:18s}  {m:5d}    {u:5d}     {m + u}")
    print(f"  {'TOTAL':18s}  {sum(m for m,_ in by_sport.values()):5d}    "
          f"{sum(u for _,u in by_sport.values()):5d}     "
          f"{sum(m+u for m,u in by_sport.values())}")


if __name__ == "__main__":
    main()
