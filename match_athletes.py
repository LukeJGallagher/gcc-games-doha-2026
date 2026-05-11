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

DEFAULT_ROSTER_GLOB     = "KSA_GCC2026_Athletes_Events*.xlsx"
DEFAULT_REGREQUEST_GLOB = "GCC2026_REG_RegRequest_*.xlsx"
DEFAULT_SHORTLIST_GLOB  = "Athletes Details*.xlsx"

OUTPUT_COLUMNS = [
    "Given Name", "Family Name", "Date of Birth",
    "Sport", "Event", "Phase", "Date", "Time Start", "Time End", "Duration_Min",
    "Discipline_API", "Event_ID", "Venue", "Gender",
    "Match_Type", "Source_URL",
    # RegRequest enrichment
    "Person_Key", "Photo_URL", "Photo_Stale",
    "Reg_Disciplines", "Reg_Status", "Reg_Created", "Reg_In_Bornan",
    # Shortlist enrichment
    "SOTC", "Time_Source",
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
# RegRequest enrichment
# ---------------------------------------------------------------------------
# Discipline-code → readable sport name (from RegRequest 'discKeys' sheet)
DISC_CODE_TO_SPORT = {
    "GCC2026|ARC": "Archery",
    "GCC2026|ATH": "Athletics",
    "GCC2026|BK3": "Basketball 3x3",
    "GCC2026|BKB": "Basketball 5x5",
    "GCC2026|BLD": "Billiards",
    "GCC2026|BOX": "Boxing",
    "GCC2026|BWL": "Bowling",
    "GCC2026|EQU": "Equestrian",
    "GCC2026|FEN": "Fencing",
    "GCC2026|HBL": "Handball",
    "GCC2026|KTE": "Karate",
    "GCC2026|PDL": "Padel",
    "GCC2026|SHO": "Shooting",
    "GCC2026|SNO": "Snooker",
    "GCC2026|SWM": "Swimming",
    "GCC2026|TKW": "Taekwondo",
    "GCC2026|TTE": "Table Tennis",
}


def _name_key(family: str, given: str, dob: str) -> tuple[str, str, str]:
    f = (family or "").strip().lower()
    g = (given or "").strip().lower()
    d = str(dob or "")[:10]
    return f, g, d


def load_regrequest(path: Path) -> dict:
    """Return {(family,given,dob): {photo, person_key, status, disciplines, created}} keyed.

    Multi-discipline athletes (e.g. Basketball 3x3 + 5x5) get their codes
    aggregated into a single comma-joined Reg_Disciplines string.
    """
    df = pd.read_excel(path, sheet_name="RegRequest", skiprows=1)
    enriched: dict = {}
    for _, row in df.iterrows():
        key = _name_key(row.get("familyName"), row.get("givenName"), row.get("dateOfBirth"))
        existing = enriched.get(key)
        disc_code = (row.get("discKeys[0]") or "").strip()
        disc_name = DISC_CODE_TO_SPORT.get(disc_code, disc_code.split("|")[-1] if "|" in disc_code else disc_code)
        if existing:
            # aggregate disciplines for multi-event athletes
            existing["Reg_Disciplines"] = ",".join(sorted(
                set(existing["Reg_Disciplines"].split(",")) | {disc_name}
            ))
        else:
            enriched[key] = {
                "Person_Key":      str(row.get("personKey") or ""),
                "Photo_URL":       str(row.get("photo") or ""),
                "Reg_Status":      str(row.get("statusReg.desc.en.short") or ""),
                "Reg_Disciplines": disc_name,
                "Reg_Created":     str(row.get("__createdAt") or "")[:19],
            }
    return enriched


def _coerce_dob(value) -> str:
    """Excel sometimes stores DoB as a numeric serial. Coerce to YYYY-MM-DD."""
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (int, float)):
        try:
            # Excel epoch is 1899-12-30 (yes, not 1900-01-01 - 1900 leap bug)
            from datetime import datetime, timedelta
            d = datetime(1899, 12, 30) + timedelta(days=float(value))
            return d.strftime("%Y-%m-%d")
        except Exception:
            return ""
    s = str(value)[:10]
    return s


def _tokenise_name(name: str) -> frozenset:
    """Lowercase tokens with non-alpha stripped. 'ALI, MOHAMED A' → {'ali','mohamed','a'}."""
    if not name:
        return frozenset()
    t = re.sub(r"[^a-z\s]", " ", str(name).lower())
    return frozenset(w for w in t.split() if len(w) > 1)


def load_shortlist_times(path: Path) -> dict:
    """Return {(dob, date_iso, phase_lower): (start, end)} from the Shortlist.

    Lets us override scraper time estimates with user-verified competition
    times whenever they exist.
    """
    df = pd.read_excel(path, sheet_name="Shortlist")
    out: dict = {}
    for _, row in df.iterrows():
        dob   = _coerce_dob(row.get("Date Of Birth"))
        date  = pd.to_datetime(row.get("Date"), errors="coerce")
        phase = str(row.get("Stage", "")).strip().lower()
        ts    = row.get("Time Start")
        te    = row.get("Time End")
        if pd.isna(date) or not dob:
            continue
        # Coerce times to HH:MM:SS strings
        def _fmt(t) -> str:
            if t is None or pd.isna(t):
                return ""
            if hasattr(t, "strftime"):
                return t.strftime("%H:%M:%S")
            return str(t)[:8]
        ts_s, te_s = _fmt(ts), _fmt(te)
        if not ts_s and not te_s:
            continue
        key = (dob, date.strftime("%Y-%m-%d"), phase)
        out[key] = (ts_s, te_s)
    return out


def load_shortlist(path: Path) -> tuple[dict, dict]:
    """Return (by_dob, by_nameset) lookups: each → SOTC 'Yes'/'No'.

    Shortlist uses CAPS LAST, FIRST format; events file uses separate
    Given/Family columns. DoBs are reliable for ~70% of athletes; the
    remainder need a name-token fallback (event 'Abdulaziz Aljadani'
    matches Shortlist 'Abdulaziz ALJADANI').
    """
    df = pd.read_excel(path, sheet_name="Shortlist")
    ath = df.groupby("Full Name").agg({"SOTC": "first", "Date Of Birth": "first"}).reset_index()
    by_dob: dict = {}
    by_name: dict = {}
    for _, row in ath.iterrows():
        sotc = "Yes" if str(row.get("SOTC", "")).strip().upper() == "YES" else "No"
        dob  = _coerce_dob(row.get("Date Of Birth"))
        if dob and by_dob.get(dob) != "Yes":
            by_dob[dob] = sotc
        tokens = _tokenise_name(row.get("Full Name", ""))
        if tokens and by_name.get(tokens) != "Yes":
            by_name[tokens] = sotc
    return by_dob, by_name


def enrich_row(out_row: dict, reg_lookup: dict,
               sotc_dob: dict | None = None, sotc_name: dict | None = None,
               time_lookup: dict | None = None) -> dict:
    # Manual time override from Shortlist
    if time_lookup is not None:
        dob   = str(out_row.get("Date of Birth", "") or "")[:10]
        date  = str(out_row.get("Date", "") or "")[:10]
        phase = str(out_row.get("Phase", "") or "").strip().lower()
        key   = (dob, date, phase)
        manual = time_lookup.get(key)
        if manual:
            ts_m, te_m = manual
            if ts_m: out_row["Time Start"] = ts_m
            if te_m: out_row["Time End"]   = te_m
            out_row["Time_Source"] = "Manual (Shortlist)"
        else:
            out_row["Time_Source"] = "API + estimate"
    else:
        out_row["Time_Source"] = "API + estimate"


    key = _name_key(out_row.get("Family Name"), out_row.get("Given Name"), out_row.get("Date of Birth"))
    info = reg_lookup.get(key)
    # SOTC: try DoB first, then full-name token-set superset match
    sotc_val = ""
    dob = str(out_row.get("Date of Birth", "") or "")[:10]
    if sotc_dob and dob in sotc_dob:
        sotc_val = sotc_dob[dob]
    elif sotc_name:
        event_tokens = _tokenise_name(
            f"{out_row.get('Given Name', '')} {out_row.get('Family Name', '')}")
        # match if event's tokens are a subset of any shortlist entry's tokens
        for tokens, val in sotc_name.items():
            if event_tokens and event_tokens.issubset(tokens):
                sotc_val = val
                break
    out_row["SOTC"] = sotc_val
    if not info:
        out_row.update({
            "Person_Key": "", "Photo_URL": "", "Photo_Stale": "",
            "Reg_Disciplines": "", "Reg_Status": "", "Reg_Created": "",
            "Reg_In_Bornan": "False",
        })
        return out_row
    photo = info["Photo_URL"]
    out_row.update({
        "Person_Key":      info["Person_Key"],
        "Photo_URL":       photo,
        # AWS-signed URLs have ~5min TTL. Flag for downstream.
        "Photo_Stale":     "True" if "X-Amz-Expires=" in photo else "False",
        "Reg_Disciplines": info["Reg_Disciplines"],
        "Reg_Status":      info["Reg_Status"],
        "Reg_Created":     info["Reg_Created"],
        "Reg_In_Bornan":   "True",
    })
    return out_row


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
    p.add_argument("--regrequest", help="Path to BORNAN RegRequest xlsx (optional enricher)")
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

    # RegRequest (BORNAN DB) - optional enricher
    reg_path = Path(args.regrequest) if args.regrequest else next(
        iter(sorted(Path(".").glob(DEFAULT_REGREQUEST_GLOB))), None)
    reg_lookup: dict = {}
    if reg_path:
        print(f"[REGREQ]   {reg_path.name}")
        reg_lookup = load_regrequest(reg_path)
        print(f"  {len(reg_lookup)} unique athletes in BORNAN DB")
    else:
        print(f"[REGREQ]   no file matching {DEFAULT_REGREQUEST_GLOB} - skipping enrichment")

    # Shortlist (SOTC flag + manual times) - optional enricher
    sl_path = next(iter(sorted(Path(".").glob(DEFAULT_SHORTLIST_GLOB))), None)
    sotc_dob: dict = {}
    sotc_name: dict = {}
    time_lookup: dict = {}
    if sl_path:
        print(f"[SHORTLIST] {sl_path.name}")
        sotc_dob, sotc_name = load_shortlist(sl_path)
        time_lookup = load_shortlist_times(sl_path)
        n_sotc = sum(1 for v in sotc_dob.values() if v == "Yes")
        print(f"  {len(sotc_dob)} athletes, {n_sotc} flagged SOTC, {len(time_lookup)} manual time entries")

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
            out_rows.append(enrich_row({
                "Given Name":     ath.get("Given Name", ""),
                "Family Name":    ath.get("Family Name", ""),
                "Date of Birth":  str(ath.get("Date of Birth", "") or "")[:10],
                "Sport":          alias_sport(ath.get("Sport", "")),
                "Event":          ath.get("Event", ""),
                "Phase":          s.get("Phase", ""),
                "Date":           s.get("Date", ""),
                "Time Start":     s.get("Time", ""),
                "Time End":       s.get("Time_End", ""),
                "Duration_Min":   s.get("Duration_Min", ""),
                "Discipline_API": s.get("Discipline", ""),
                "Event_ID":       s.get("Event_ID", ""),
                "Venue":          s.get("Venue", ""),
                "Gender":         s.get("Gender", ""),
                "Match_Type":     "team" if "Team" in str(ath.get("Event","")) else "individual",
                "Source_URL":     s.get("Source_URL", ""),
            }, reg_lookup, sotc_dob, sotc_name, time_lookup))

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

    # ---- BORNAN coverage summary ----
    if reg_lookup:
        matched_keys = {
            _name_key(r["Family Name"], r["Given Name"], r["Date of Birth"])
            for r in out_rows
        }
        in_bornan    = matched_keys & set(reg_lookup.keys())
        missing      = matched_keys - set(reg_lookup.keys())
        orphan_in_bornan = set(reg_lookup.keys()) - matched_keys
        print(f"\n[BORNAN coverage]")
        print(f"  Roster athletes IN BORNAN:        {len(in_bornan)}/{len(matched_keys)}")
        if missing:
            print(f"  In events file but NOT in BORNAN: {len(missing)}")
            for f,g,d in sorted(missing)[:10]:
                print(f"     - {g.title()} {f.title()} (DOB {d})")
            if len(missing) > 10:
                print(f"     ...and {len(missing)-10} more")
        if orphan_in_bornan:
            print(f"  In BORNAN but NOT in events file: {len(orphan_in_bornan)}")
            # Cross-check Basketball 5x5 orphans (event was dropped)
            bkb_orphans = [k for k in orphan_in_bornan
                           if "Basketball 5x5" in reg_lookup[k]["Reg_Disciplines"]]
            if bkb_orphans:
                print(f"     {len(bkb_orphans)} are Basketball 5x5 registrations (event was dropped from games)")


if __name__ == "__main__":
    main()
