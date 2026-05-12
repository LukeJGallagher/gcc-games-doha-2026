"""
Schedule freshness check — runs FIRST in each scrape.

Pulls the live schedule from the API and compares to the most recent
SCHEDULE_*.csv we have on disk. Surfaces:
  + added events    — IDs in API not in our file
  - dropped events  — IDs in our file not in API
  ~ time changes    — same Event_ID, different start/end
  ~ phase changes
  ~ venue changes
  ~ status changes  (e.g. Scheduled → Cancelled)

The output goes to stdout AND data/schedule/CHECK_<ts>.csv for the
GitHub Actions log + cloud commit message.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from api_client import GccApi
from config import SCHEDULE_DIR


def _latest_schedule_csv() -> Path | None:
    files = sorted(p for p in SCHEDULE_DIR.glob("SCHEDULE_*.csv")
                   if "CHECK" not in p.name)
    return files[-1] if files else None


def _load_csv_by_event(path: Path) -> dict:
    """Read schedule CSV → {Event_ID: row dict}."""
    out: dict = {}
    if not path or not path.exists():
        return out
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            eid = r.get("Event_ID", "").strip()
            if eid:
                out[eid] = r
    return out


def _load_api() -> dict:
    """Pull live API schedule across all sports → {Event_ID: pseudo-row}."""
    api = GccApi()
    sports = [s["id"] for s in api.sports()]
    out: dict = {}
    for sp in sports:
        try:
            comps = api.sport_results_summary(sport=sp)
        except Exception:
            continue
        for c in comps:
            eid = c.get("id", "")
            if not eid: continue
            out[eid] = {
                "Sport":      sp,
                "Date":       c.get("date", ""),
                "Time":       c.get("time", "")[:5],
                "Stage":      c.get("stage_name", ""),
                "Status":     c.get("status", ""),
                "Venue":      c.get("venue", ""),
                "Title":      (c.get("title", "").split(" - ")[0] or "").strip(),
            }
    return out


def main():
    prev_path = _latest_schedule_csv()
    prev      = _load_csv_by_event(prev_path) if prev_path else {}
    live      = _load_api()

    added   = sorted(set(live) - set(prev))
    dropped = sorted(set(prev) - set(live))
    common  = set(live) & set(prev)

    time_changed   = []
    phase_changed  = []
    venue_changed  = []
    status_changed = []
    title_changed  = []
    for eid in sorted(common):
        a = live[eid]
        b = prev[eid]
        if (b.get("Time") or "")[:5] != a["Time"]:
            time_changed.append((eid, b.get("Sport",""), b.get("Time",""), a["Time"]))
        if (b.get("Phase") or "") != a["Stage"]:
            phase_changed.append((eid, b.get("Sport",""), b.get("Phase",""), a["Stage"]))
        if (b.get("Venue") or "") != a["Venue"] and a["Venue"]:
            venue_changed.append((eid, b.get("Sport",""), b.get("Venue",""), a["Venue"]))
        if (b.get("Status") or "") != a["Status"]:
            status_changed.append((eid, b.get("Sport",""), b.get("Status",""), a["Status"]))
        if (b.get("Discipline") or "") != a["Title"] and a["Title"]:
            title_changed.append((eid, b.get("Sport",""), b.get("Discipline","")[:50], a["Title"][:50]))

    # ---- Print summary ----
    print(f"[SCHEDULE-CHECK]")
    print(f"  Prev file: {prev_path.name if prev_path else '(none)'}")
    print(f"  Now:       {len(live)} live events vs {len(prev)} previously stored")
    print(f"  + added events:    {len(added)}")
    print(f"  - dropped events:  {len(dropped)}")
    print(f"  ~ time changes:    {len(time_changed)}")
    print(f"  ~ phase changes:   {len(phase_changed)}")
    print(f"  ~ venue changes:   {len(venue_changed)}")
    print(f"  ~ status changes:  {len(status_changed)}")
    print(f"  ~ title changes:   {len(title_changed)}")

    for label, items, fmt in [
        ("+ ADDED",          added[:10],
         lambda eid: f"{eid} {live[eid]['Sport']:15s} {live[eid]['Date']} {live[eid]['Time']:5s} {live[eid]['Stage']:12s} {live[eid]['Title']:.40s}"),
        ("- DROPPED",        dropped[:10],
         lambda eid: f"{eid} {prev[eid].get('Sport',''):15s} {prev[eid].get('Date',''):10s} {prev[eid].get('Time',''):5s} {prev[eid].get('Phase',''):12s}"),
        ("~ TIME",            time_changed[:10],
         lambda r: f"{r[0]} {r[1]:15s} {r[2]} → {r[3]}"),
        ("~ STATUS",          status_changed[:10],
         lambda r: f"{r[0]} {r[1]:15s} {r[2]} → {r[3]}"),
        ("~ PHASE",           phase_changed[:10],
         lambda r: f"{r[0]} {r[1]:15s} {r[2]} → {r[3]}"),
        ("~ VENUE",           venue_changed[:10],
         lambda r: f"{r[0]} {r[1]:15s} {r[2][:30]} → {r[3][:30]}"),
    ]:
        if items:
            print(f"\n{label}:")
            for it in items:
                print(f"  {fmt(it)}")

    # ---- Save log row ----
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = SCHEDULE_DIR / f"CHECK_{ts}.csv"
    with log.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Type","Event_ID","Sport","Before","After"])
        for eid in added:    w.writerow(["+ added",   eid, live[eid]["Sport"], "", live[eid]["Time"]])
        for eid in dropped:  w.writerow(["- dropped", eid, prev[eid].get("Sport",""), prev[eid].get("Time",""), ""])
        for r in time_changed:   w.writerow(["~ time",   r[0], r[1], r[2], r[3]])
        for r in phase_changed:  w.writerow(["~ phase",  r[0], r[1], r[2], r[3]])
        for r in status_changed: w.writerow(["~ status", r[0], r[1], r[2], r[3]])
        for r in venue_changed:  w.writerow(["~ venue",  r[0], r[1], r[2], r[3]])
        for r in title_changed:  w.writerow(["~ title",  r[0], r[1], r[2], r[3]])
    print(f"\n[SAVE] {log.name}")


if __name__ == "__main__":
    main()
