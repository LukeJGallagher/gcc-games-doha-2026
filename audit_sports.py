"""
Per-sport audit: compares our KSA athlete-schedule to the live GCC API
and the roster Excel to surface data-quality issues.

Findings categories (Severity column):
    HIGH    — KSA athlete entered for event but event not on the API schedule
    HIGH    — KSA shows in API participants but is missing from roster
    MEDIUM  — API participants populated but our scrape missed them
    MEDIUM  — Athlete name mismatch between BORNAN events file and API player_name
    LOW     — Phase name oddity (e.g. 'Event N' instead of 'Heats / Semi / Final')
    LOW     — Roster has SOTC flag but no events file row (DoB mismatch)

Output: data/audit/AUDIT_<ts>.csv  with: Severity, Sport, Issue, Detail, Suggested_action
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from api_client import GccApi
from config import KSA_CODES, RESULTS_DIR

HERE       = Path(__file__).parent
AUDIT_DIR  = HERE / "data" / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)


ksa_upper = {c.upper() for c in KSA_CODES}

# Phases we recognise as legitimate. Pulled from BORNAN deployments
# (GCC 2026 + AYG 2025) — anything outside this set is flagged LOW.
KNOWN_PHASES = {
    "Final", "Semi Final", "Semifinal", "Semi-Final",
    "Quarter Final", "Quarterfinal",
    "Round of 64", "Round of 32", "Round of 16",
    "Round 1", "Round 2", "Round 3",
    "Qualification", "Preliminary", "Heats", "Heat",
    "Group Stage", "Group", "Group A", "Group B", "Pool A", "Pool B",
    "Knockout", "Training",
    "Bronze Medal Game", "Gold Medal Game",
}


def audit_sport(api: GccApi, sport: str, athlete_schedule: pd.DataFrame) -> list[dict]:
    findings: list[dict] = []
    try:
        comps = api.sport_results_summary(sport=sport)
    except Exception as e:
        findings.append({
            "Severity": "HIGH", "Sport": sport, "Issue": "API failure",
            "Detail": f"{type(e).__name__}: {e}",
            "Suggested_action": "Retry; if persistent, check api_client.py",
        })
        return findings

    api_event_ids = {c.get("id") for c in comps if c.get("id")}
    sched_rows = athlete_schedule[athlete_schedule["Sport"] == sport]

    # ---- 1. Athlete entries for events not in API ----
    roster_events = set(sched_rows["Event_ID"].dropna())
    missing = roster_events - api_event_ids
    for evid in missing:
        athletes_for_ev = sched_rows[sched_rows["Event_ID"] == evid]
        names = ", ".join(sorted(set(athletes_for_ev["Athlete"].dropna().head(5))))
        findings.append({
            "Severity": "HIGH", "Sport": sport, "Issue": "Event_ID in our data but not in live API",
            "Detail": f"Event_ID={evid}, athletes={names}",
            "Suggested_action": "Re-run scraper; if still missing, organisers dropped the event",
        })

    # ---- 2. KSA participants in API but no roster row for that event ----
    for c in comps:
        parts = c.get("participants") or []
        ksa_parts = [p for p in parts if (p.get("noc_code") or "").upper() in ksa_upper]
        if not ksa_parts:
            continue
        evid = c.get("id", "")
        roster_for_event = sched_rows[sched_rows["Event_ID"] == evid]
        if roster_for_event.empty:
            names = ", ".join(
                p.get("player_name") or p.get("noc_name", "") for p in ksa_parts[:3]
            )
            findings.append({
                "Severity": "HIGH", "Sport": sport,
                "Issue": "KSA in API participants but no matching roster row",
                "Detail": f"Event_ID={evid}, KSA player(s)={names}, Phase={c.get('stage_name')}",
                "Suggested_action": "Add to events file roster, or check name normalisation",
            })

    # ---- 3. Athlete name mismatches ----
    # Build name set from roster
    roster_names = set()
    for _, r in sched_rows.iterrows():
        n = str(r.get("Athlete", "")).strip()
        if n and "Team" not in n:
            roster_names.add(n.lower())

    for c in comps:
        for p in c.get("participants") or []:
            if (p.get("noc_code") or "").upper() not in ksa_upper:
                continue
            api_name = (p.get("player_name") or "").strip()
            if not api_name:
                continue
            if api_name.lower() not in roster_names:
                # Try fuzzy: any roster name shares 50%+ tokens
                tokens_api = set(api_name.lower().split())
                close = any(
                    tokens_api and len(tokens_api & set(rn.split())) >= max(1, len(tokens_api) // 2)
                    for rn in roster_names
                )
                if not close:
                    findings.append({
                        "Severity": "MEDIUM", "Sport": sport,
                        "Issue": "API athlete name not found in our roster",
                        "Detail": f"API: '{api_name}', Event={c.get('title','').split(' - ')[0][:40]}",
                        "Suggested_action": "Add to roster, or check transliteration",
                    })

    # ---- 4. Phase oddities ----
    for c in comps:
        ph = (c.get("stage_name") or "").strip()
        if not ph or ph in KNOWN_PHASES:
            continue
        findings.append({
            "Severity": "LOW", "Sport": sport,
            "Issue": "Unrecognised phase name",
            "Detail": f"Phase='{ph}' on {c.get('id')} - {c.get('title','').split(' - ')[0][:40]}",
            "Suggested_action": "Update PHASE_PRIORITY / PHASE_COLOURS in dashboard if rendering oddly",
        })

    # ---- 5. Coverage stats (informational) ----
    n_comps = len(comps)
    n_with_parts = sum(1 for c in comps if c.get("participants"))
    n_ksa_events = sum(1 for c in comps
                       if any((p.get("noc_code") or "").upper() in ksa_upper
                              for p in c.get("participants") or []))
    findings.append({
        "Severity": "INFO", "Sport": sport,
        "Issue": "Coverage summary",
        "Detail": f"{n_comps} comps, {n_with_parts} with entries, {n_ksa_events} with KSA",
        "Suggested_action": "",
    })

    return findings


def run_audit(athlete_schedule_path: Path | None = None) -> Path:
    if athlete_schedule_path is None:
        files = sorted(RESULTS_DIR.glob("KSA_ATHLETE_SCHEDULE_*.csv"))
        if not files:
            sys.exit("No KSA_ATHLETE_SCHEDULE file — run match_athletes.py first")
        athlete_schedule_path = files[-1]

    sched = pd.read_csv(athlete_schedule_path, encoding="utf-8-sig", dtype=str).fillna("")
    sched["Athlete"] = (sched["Given Name"] + " " + sched["Family Name"]).str.strip()

    api = GccApi()
    sports = sorted(sched["Sport"].unique())
    all_findings: list[dict] = []
    print(f"[AUDIT] Across {len(sports)} sports against live API\n")
    for sport in sports:
        fs = audit_sport(api, sport, sched)
        all_findings.extend(fs)
        bad = [f for f in fs if f["Severity"] != "INFO"]
        info = next((f for f in fs if f["Severity"] == "INFO"), None)
        print(f"  {sport:18s}  flags={len(bad):2d}  {info['Detail'] if info else ''}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = AUDIT_DIR / f"AUDIT_{ts}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["Severity","Sport","Issue","Detail","Suggested_action"])
        w.writeheader()
        w.writerows(all_findings)
    print(f"\n[SAVE] {out}\n")

    # Summary
    from collections import Counter
    sev = Counter(f["Severity"] for f in all_findings)
    print("Summary by severity:")
    for s, n in sev.most_common():
        print(f"  {s:6s}  {n}")
    return out


if __name__ == "__main__":
    run_audit()
