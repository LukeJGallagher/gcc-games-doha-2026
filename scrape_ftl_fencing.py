"""
Scrape KSA fencing results from Fencing Time Live (gccgames.qa only publishes
top-4 medalists for some events, FTL has the full ranking).

Requires a logged-in FTL session: export browser cookies to
data/cookies-2026-05-19.json (Cookie Editor extension, JSON format).

Append every KSA row found to data/manual_results.csv with Entry_Source=
"ftl:<eventId>", so the merge_manual_with_audit.py track-both-flag-conflicts
policy kicks in for any disagreement with gccgames.qa.

Each event is configured by FTL event-id + the metadata we need to map back to
the dashboard's Discipline / Date / Phase columns. Add new IDs as the meet
progresses.

Usage:
    python scrape_ftl_fencing.py            # scrape every configured event
    python scrape_ftl_fencing.py --dry-run  # show what would be written
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests


HERE = Path(__file__).resolve().parent
COOKIE_FILE = HERE / "data" / "cookies-2026-05-19.json"
MANUAL_CSV = HERE / "data" / "manual_results.csv"
RAW_DIR = HERE / "data" / "ftl_raw"

# (event_id, discipline, date, phase, gender, individual-or-team)
# Date matches the gccgames.qa published date for that event so the
# (athlete, date, discipline) merge key lines up.
EVENTS: list[dict] = [
    {"id": "A38E370D8AA34E29BF9EE261777E570B", "discipline": "Women's Épée",
     "date": "2026-05-13", "phase": "Final", "gender": "Women", "kind": "individual"},
    {"id": "6176887FC4C44A27A8E8EFC071601899", "discipline": "Men's Foil",
     "date": "2026-05-13", "phase": "Final", "gender": "Men",   "kind": "individual"},
    {"id": "DECE14309C6A422D9AACDDB03756D6F9", "discipline": "Women's Foil",
     "date": "2026-05-14", "phase": "Final", "gender": "Women", "kind": "individual"},
    {"id": "3B2F8522BAD447C598120B25B8A6953F", "discipline": "Men's Épée",
     "date": "2026-05-14", "phase": "Final", "gender": "Men",   "kind": "individual"},
    {"id": "A34C254CDE934B678C7149DBA48663BF", "discipline": "Men's Épée",
     "date": "2026-05-15", "phase": "Final", "gender": "Men",   "kind": "team"},
]


def load_session() -> requests.Session:
    if not COOKIE_FILE.exists():
        raise SystemExit(f"Missing cookie file: {COOKIE_FILE}\n"
                         "Export FTL cookies (logged in) to this path using the "
                         "Cookie Editor browser extension (JSON format).")
    cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
    })
    for c in cookies:
        s.cookies.set(c["name"], c["value"],
                       domain=c.get("domain", "www.fencingtimelive.com"),
                       path=c.get("path", "/"))
    return s


def name_lastfirst_to_titlecase(s: str) -> str:
    """'ABED Nada' -> 'Nada Abed' (FTL prints LASTNAME Firstname)."""
    parts = (s or "").strip().split()
    if not parts:
        return ""
    # Heuristic: tokens in ALL CAPS are surname tokens, rest is given names
    surnames, givens = [], []
    for p in parts:
        if p.isupper() and len(p) > 1:
            surnames.append(p)
        else:
            givens.append(p)
    if not surnames or not givens:
        return " ".join(p.title() for p in parts)
    return " ".join(g.title() for g in givens) + " " + " ".join(s.title() for s in surnames)


def parse_place(p: str) -> tuple[str, str]:
    """Return (rank, medal-letter). FTL uses '1', '2', '3T' (tied 3rd), etc."""
    if not p:
        return "", ""
    n = re.sub(r"\D+$", "", str(p))  # drop trailing 'T' for tied
    medal = {"1": "G", "2": "S", "3": "B"}.get(n, "")
    return n, medal


def fetch_event(s: requests.Session, eid: str) -> list[dict]:
    url = f"https://www.fencingtimelive.com/events/results/data/{eid}"
    r = s.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def to_manual_row(rec: dict, evt: dict, ts: str) -> dict:
    rank_str, medal = parse_place(rec.get("place", ""))
    if evt["kind"] == "team":
        # FTL team rows have `name` = country name, `members` = list of athletes
        athlete = "Saudi Arabia (Team)"
        notes = "Team: " + ", ".join(name_lastfirst_to_titlecase(m) for m in rec.get("members", []))
        discipline = evt["discipline"]
        # Mirror the existing convention used by gccgames.qa for team rows
        comp_disc = f"{discipline} Team" if "team" not in discipline.lower() else discipline
    else:
        athlete = name_lastfirst_to_titlecase(rec.get("name", ""))
        notes = ""
        comp_disc = evt["discipline"]

    status = "Podium" if medal else ("Top 8" if rank_str.isdigit() and int(rank_str) <= 8 else "Official")

    return {
        "Athlete": athlete,
        "Sport": "Fencing",
        "Date": evt["date"],
        "Competition": "4th GCC Games Doha 2026",
        "Comp Set": "GCC Games",
        "Class": "",
        "Discipline": comp_disc,
        "Phase": evt["phase"],
        "Gender": evt["gender"],
        "Age": "",
        "Rank": rank_str,
        "Result": rec.get("place", ""),
        "Medal": medal,
        "Wind": "",
        "Attempt": "",
        "Status": status,
        "Country": "KSA",
        "Entered_By": "ftl_scraper",
        "Entered_At": ts,
        "Entry_Source": f"ftl:{evt['id']}",
        "Notes": notes,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Print rows but don't write to manual_results.csv")
    args = p.parse_args()

    s = load_session()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")

    new_rows: list[dict] = []
    for evt in EVENTS:
        print(f"\n[FTL] {evt['discipline']:<14} {evt['kind']:<10} ({evt['id'][:12]}…)")
        try:
            records = fetch_event(s, evt["id"])
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
        # cache raw JSON for audit
        (RAW_DIR / f"{evt['id']}.json").write_text(json.dumps(records, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
        ksa = [r for r in records if (r.get("country") or "").upper() in ("KSA", "SAU")]
        print(f"  total={len(records):>3}  KSA={len(ksa)}")
        for rec in ksa:
            row = to_manual_row(rec, evt, ts)
            new_rows.append(row)
            print(f"    {row['Rank']:<3} {row['Medal'] or '-':<2} {row['Athlete']:<24} {row['Discipline']}")

    if not new_rows:
        print("\nNothing to write.")
        return 0

    if args.dry_run:
        print(f"\n[DRY] Would write {len(new_rows)} rows to {MANUAL_CSV.name}")
        return 0

    # Dedupe within batch + append, skipping rows already in manual_results.csv
    if MANUAL_CSV.exists():
        with MANUAL_CSV.open(encoding="utf-8-sig") as f:
            existing = list(csv.DictReader(f))
    else:
        existing = []

    def key(r):
        return (r.get("Athlete", "").strip().lower(),
                r.get("Date", "").strip(),
                r.get("Discipline", "").strip().lower())

    seen = {key(r) for r in existing}
    fresh = [r for r in new_rows if key(r) not in seen]
    dupes = len(new_rows) - len(fresh)

    if not MANUAL_CSV.exists():
        MANUAL_CSV.parent.mkdir(parents=True, exist_ok=True)
        MANUAL_CSV.write_text(
            "Athlete,Sport,Date,Competition,Comp Set,Class,Discipline,Phase,Gender,Age,"
            "Rank,Result,Medal,Wind,Attempt,Status,Country,Entered_By,Entered_At,"
            "Entry_Source,Notes\n",
            encoding="utf-8",
        )

    with MANUAL_CSV.open(encoding="utf-8-sig") as f:
        fields = next(csv.reader(f))
    with MANUAL_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        for r in fresh:
            w.writerow(r)

    print(f"\n[WRITE] Appended {len(fresh)} rows to {MANUAL_CSV.name} ({dupes} dupes skipped)")
    print("Run: python merge_manual_with_audit.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
