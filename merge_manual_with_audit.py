"""
Overlay manual entries onto the latest ENHANCED_RESULTS file and produce
a Markdown audit report.

Track-both-flag-conflicts policy:
  - If the scraper row has a blank Result/Rank/Medal and the manual row has
    a value, the manual value fills the gap (scraper had nothing).
  - If both have values AND they disagree, BOTH are preserved on the output
    row: scraper value stays in Rank/Result/Medal, manual value goes into
    Rank_Manual/Result_Manual/Medal_Manual, and Conflict_Flag = "y".
  - If a manual row has no matching scheduled row in the scraper output,
    it is appended as a new row with Detection_Method = "Manual".

Outputs:
  data/results/ENHANCED_WITH_MANUAL_<ts>.csv   - merged source of truth
  data/audit/CHANGES_<ts>.md                   - human-readable audit report

The Markdown report covers:
  - Conflicts (manual vs scraper disagreements)
  - Status changes (Scheduled -> Official etc) since previous scrape
  - New entries since previous scrape
  - New medals
  - Dropped entries
  - Manual entries merged this run

Usage:
  python merge_manual_with_audit.py            # uses latest ENHANCED + previous for diff
  python merge_manual_with_audit.py --since YYYYMMDD_HHMMSS   # diff from specific pull
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from enhance_rankings import standardise_rank  # noqa: E402

RESULTS_DIR = HERE / "data" / "results"
AUDIT_DIR = HERE / "data" / "audit"
MANUAL_CSV = HERE / "data" / "manual_results.csv"

EXTRA_COLS = ["Rank_Manual", "Result_Manual", "Medal_Manual",
              "Conflict_Flag", "Manual_Source", "Manual_Entered_By"]


def normalise_discipline(s: str) -> str:
    s = (s or "").strip().replace("’", "'")
    s = re.sub(r"^(?:Men|Women|Mixed)['s\s]+", "", s)
    s = re.sub(r"\s+Metres\b", "m", s, flags=re.I)
    s = re.sub(r"(\d)\s*M\b", r"\1m", s)
    s = re.sub(r"\s+Throw\b", "", s, flags=re.I)
    # gccgames.qa adds " Final Results" / " Final" suffixes to the discipline
    # for ranking-table comps; other sources (FTL fencing, Ianseo archery, the
    # athletics PDFs) don't. Strip so the merge key lines up across sources.
    s = re.sub(r"\s+Final\s+Results?\s*$", "", s, flags=re.I)
    s = re.sub(r"\s+Final\s*$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def normalise_athlete(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def match_key(row: dict) -> tuple:
    return (
        normalise_athlete(row.get("Athlete", "")),
        (row.get("Date") or "").strip(),
        normalise_discipline(row.get("Discipline", "")),
    )


def load_csv(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def latest(glob: str, root: Path) -> Path | None:
    files = sorted(root.glob(glob))
    return files[-1] if files else None


def find_latest_enhanced() -> Path:
    files = sorted(
        RESULTS_DIR.glob("ENHANCED_RESULTS_KSA_*.csv"),
        key=lambda p: p.stat().st_mtime,
    )
    if not files:
        raise SystemExit(f"No ENHANCED_RESULTS_KSA file in {RESULTS_DIR}")
    return files[-1]


def find_previous_raw(latest_path: Path, since: str | None) -> Path | None:
    files = sorted(
        [f for f in RESULTS_DIR.glob("RESULTS_KSA_*.csv") if "ENHANCED" not in f.name],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if since:
        return next((f for f in files if since in f.name), None)
    # Need 2+ raw RESULTS files for a diff
    return files[1] if len(files) >= 2 else None


def merge(rows: list[dict], manual: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (merged_rows, conflicts, manual_appended)."""
    by_key: dict[tuple, dict] = {match_key(r): r for r in rows}
    conflicts: list[dict] = []
    appended: list[dict] = []
    fields = (list(rows[0].keys()) if rows else []) + [c for c in EXTRA_COLS if c not in (rows[0].keys() if rows else [])]

    # Ensure every row has the extra columns
    for r in rows:
        for c in EXTRA_COLS:
            r.setdefault(c, "")

    for m in manual:
        if not m.get("Athlete") or not m.get("Discipline"):
            continue
        key = match_key(m)
        target = by_key.get(key)
        if target is None:
            # No matching scheduled row → append, but only for KSA.
            # The ENHANCED file is KSA-filtered; non-KSA manual rows stay in
            # manual_results.csv as reference data without polluting the
            # KSA dashboard / medal count.
            country = (m.get("Country") or "").strip().upper()
            if country and country != "KSA":
                continue
            new_row = {f: "" for f in fields}
            for k in ["Athlete", "Sport", "Date", "Competition", "Comp Set", "Class",
                     "Discipline", "Phase", "Gender", "Age", "Rank", "Result", "Medal",
                     "Wind", "Attempt", "Status", "Country"]:
                if k in m:
                    new_row[k] = m[k]
            new_row.setdefault("Comp Set", "GCC Games")
            new_row.setdefault("Country", "KSA")
            new_row.setdefault("Status", "Official" if m.get("Result") else "Scheduled")
            new_row["Detection_Method"] = "Manual"
            new_row["Manual_Source"] = m.get("Entry_Source", "manual")
            new_row["Manual_Entered_By"] = m.get("Entered_By", "")
            new_row["Rank_Original"] = m.get("Rank", "")
            new_row["Rank_Std"] = standardise_rank(new_row)
            appended.append(new_row)
            continue

        # Match found - apply track-both-flag-conflicts policy
        scraper_rank = (target.get("Rank") or "").strip()
        scraper_result = (target.get("Result") or "").strip()
        scraper_medal = (target.get("Medal") or "").strip()
        m_rank = (m.get("Rank") or "").strip()
        m_result = (m.get("Result") or "").strip()
        m_medal = (m.get("Medal") or "").strip()

        # Provenance always recorded
        target["Manual_Source"] = m.get("Entry_Source", "manual")
        target["Manual_Entered_By"] = m.get("Entered_By", "")

        conflict_fields = []
        # Fill where scraper is blank
        if not scraper_rank and m_rank:
            target["Rank"] = m_rank
            target["Rank_Original"] = m_rank
            target["Detection_Method"] = (target.get("Detection_Method") or "") + " + Manual"
        if not scraper_result and m_result:
            target["Result"] = m_result
            target["Detection_Method"] = (target.get("Detection_Method") or "") + " + Manual"
        if not scraper_medal and m_medal:
            target["Medal"] = m_medal

        # Conflict: both present and differ
        if scraper_rank and m_rank and scraper_rank != m_rank:
            target["Rank_Manual"] = m_rank
            conflict_fields.append(("Rank", scraper_rank, m_rank))
        if scraper_result and m_result and scraper_result != m_result:
            target["Result_Manual"] = m_result
            conflict_fields.append(("Result", scraper_result, m_result))
        if scraper_medal and m_medal and scraper_medal != m_medal:
            target["Medal_Manual"] = m_medal
            conflict_fields.append(("Medal", scraper_medal, m_medal))

        if conflict_fields:
            target["Conflict_Flag"] = "y"
            for field, scr, man in conflict_fields:
                conflicts.append({
                    "Athlete": target.get("Athlete", ""),
                    "Sport": target.get("Sport", ""),
                    "Date": target.get("Date", ""),
                    "Discipline": target.get("Discipline", ""),
                    "Field": field,
                    "Scraper_Value": scr,
                    "Manual_Value": man,
                    "Entered_By": m.get("Entered_By", ""),
                    "Notes": m.get("Notes", ""),
                })

        if m_result and not (target.get("Status") or "").lower().startswith("offic"):
            target["Status"] = "Official"
        target["Rank_Std"] = standardise_rank(target)

    rows.extend(appended)
    return rows, conflicts, appended


def diff_against_previous(latest_path: Path, prev_path: Path | None) -> dict:
    if prev_path is None:
        return {"new": [], "dropped": [], "status": [], "results": [], "medals": []}

    def load_indexed(p: Path) -> dict:
        d = {}
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            k = (r.get("Source_URL", "").split("/")[-1], r.get("Athlete", ""))
            d[k] = r
        return d

    new_data = load_indexed(latest_path)
    old_data = load_indexed(prev_path)
    added = [k for k in new_data if k not in old_data]
    dropped = [k for k in old_data if k not in new_data]
    common = [k for k in new_data if k in old_data]
    status_changed, result_appeared, new_medals = [], [], []
    for k in common:
        n, o = new_data[k], old_data[k]
        if (n.get("Status") or "") != (o.get("Status") or ""):
            status_changed.append((k, o.get("Status"), n.get("Status"), n))
        if not (o.get("Rank") or o.get("Result")) and (n.get("Rank") or n.get("Result")):
            result_appeared.append((k, n.get("Rank"), n.get("Result"), n))
        if not (o.get("Medal") or "") and (n.get("Medal") or ""):
            new_medals.append((k, n.get("Medal"), n))

    return {
        "new": [new_data[k] for k in added],
        "dropped": [old_data[k] for k in dropped],
        "status": status_changed,
        "results": result_appeared,
        "medals": new_medals,
    }


# Expected-events templates per sport. Each entry: (required-keywords, note).
# The check needs ALL keywords to appear (in any order) in at least one
# Discipline string for the sport. Robust to the various naming styles the
# API uses ("Men's Team Recurve" vs "Recurve Men Team" etc.). Use lowercase.
# "men" matches both "men" and "men's"; we strip apostrophes before checking.
EXPECTED_EVENTS: dict[str, list[tuple[tuple[str, ...], str]]] = {
    "Archery": [
        (("recurve", "men", "individual"),    "Recurve Men's Individual"),
        (("recurve", "women", "individual"),  "Recurve Women's Individual"),
        (("recurve", "men", "team"),          "Recurve Men's Team (3 athletes)"),
        (("recurve", "women", "team"),        "Recurve Women's Team (3 athletes)"),
        (("recurve", "mixed", "team"),        "Recurve Mixed Team (1M + 1W)"),
        (("compound", "men", "individual"),   "Compound Men's Individual"),
        (("compound", "women", "individual"), "Compound Women's Individual"),
        (("compound", "men", "team"),         "Compound Men's Team (3 athletes)"),
        (("compound", "women", "team"),       "Compound Women's Team (3 athletes)"),
        (("compound", "mixed", "team"),       "Compound Mixed Team (1M + 1W)"),
    ],
    # Other sports can be templated here as we learn them. Kept conservative —
    # we only flag missing for sports KSA actually entered.
}


def _norm_disc(s: str) -> str:
    """Lowercase + strip apostrophes for keyword matching."""
    return (s or "").lower().replace("’", "").replace("'", "")


def load_schedule_disciplines() -> dict[str, set[str]]:
    """Per-sport set of Discipline strings from the latest KSA_ATHLETE_SCHEDULE_*.csv.

    The schedule covers ALL events KSA entered — including events with no
    results yet (like archery early in the meet) — so it's a more complete
    source than the ENHANCED file for the structural-completeness check.
    """
    files = sorted(RESULTS_DIR.glob("KSA_ATHLETE_SCHEDULE_*.csv"))
    if not files:
        return {}
    out: dict[str, set[str]] = {}
    with open(files[-1], encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sport = (r.get("Sport") or "").strip()
            # Schedule uses "Event" as the column, results use "Discipline"
            disc = (r.get("Discipline") or r.get("Event") or "").strip().replace("’", "'")
            if sport and disc:
                out.setdefault(sport, set()).add(disc.lower())
    return out


def expected_events_check(coverage: dict | None,
                           sched_disciplines: dict | None = None) -> dict[str, list[tuple[str, str]]]:
    """For each templated sport, return missing-event tuples (event, note).

    Only fires for sports that already have at least one row in the coverage
    snapshot — we don't want to nag about sports KSA didn't enter at all.
    """
    coverage = coverage or {}
    sched_disciplines = sched_disciplines or {}
    gaps: dict[str, list[tuple[str, str]]] = {}
    for sport, expected in EXPECTED_EVENTS.items():
        # Union of disciplines seen in results + schedule. Schedule is the
        # source of truth for events the federation listed KSA in, even if
        # results haven't been published yet.
        from_results = coverage.get(sport, {}).get("disciplines", set())
        from_sched = sched_disciplines.get(sport, set())
        present = [_norm_disc(d) for d in (from_results | from_sched)]
        if not present:
            continue  # KSA didn't enter this sport at all
        missing = []
        for keywords, note in expected:
            # Match if ANY discipline contains ALL keywords (in any order)
            matched = any(all(kw in d for kw in keywords) for d in present)
            if not matched:
                missing.append((note,
                                f"no Discipline contains all of {list(keywords)}"))
        if missing:
            gaps[sport] = missing
    return gaps


def coverage_audit(rows: list[dict]) -> dict:
    """Find per-sport gaps: empty Official rows, likely-medal candidates not
    tagged, and total counts. Helps surface where row-level data is missing
    even though the API has marked entries Official.

    Returns dict keyed by sport with:
      - total / official / with_result / with_medal counts
      - empty_official: rows where Status=Official but Rank+Result both blank
      - likely_medals: rows with Rank in {1,2,3} in Final/Knockout-like phase
        but no Medal column populated
    """
    by_sport: dict[str, dict] = {}
    for r in rows:
        if (r.get("Country") or "KSA").strip().upper() != "KSA":
            continue
        sp = (r.get("Sport") or "?").strip() or "?"
        s = by_sport.setdefault(sp, {
            "total": 0, "official": 0, "with_result": 0, "with_medal": 0,
            "empty_official": [], "likely_medals": [],
            "disciplines": set(),
        })
        s["total"] += 1
        disc = (r.get("Discipline") or "").strip().replace("’", "'")
        if disc:
            s["disciplines"].add(disc.lower())
        status = (r.get("Status") or "").strip().lower()
        rank = (r.get("Rank") or "").strip()
        result = (r.get("Result") or "").strip()
        medal = (r.get("Medal") or "").strip().upper()[:1]
        has_data = bool(rank) or bool(result)
        if status.startswith("offic"):
            s["official"] += 1
        if has_data:
            s["with_result"] += 1
        if medal in {"G", "S", "B"}:
            s["with_medal"] += 1

        if status.startswith("offic") and not has_data:
            s["empty_official"].append(r)

        phase = (r.get("Phase") or "").strip().lower()
        looks_terminal = any(t in phase for t in
                             ("final", "medal", "knockout", "gold", "bronze", "podium"))
        # "Semi-final" exception: only treat as a medal slot for the bronze loser
        if rank in {"1", "2", "3"} and looks_terminal and medal not in {"G", "S", "B"}:
            s["likely_medals"].append(r)
    return by_sport


def load_medal_totals() -> dict[str, dict[str, int]]:
    """Read the latest MEDALS_*.csv (NOC-level totals) so we can cross-check."""
    files = sorted(RESULTS_DIR.glob("MEDALS_*.csv"))
    if not files:
        return {}
    with open(files[-1], encoding="utf-8-sig") as f:
        out: dict[str, dict[str, int]] = {}
        for r in csv.DictReader(f):
            noc = (r.get("NOC") or "").strip().upper()
            if not noc:
                continue
            def _i(k):
                try:
                    return int(r.get(k, 0) or 0)
                except ValueError:
                    return 0
            out[noc] = {
                "Gold":   _i("Gold"),
                "Silver": _i("Silver"),
                "Bronze": _i("Bronze"),
                "Total":  _i("Total"),
            }
    return out


def write_audit_md(out: Path, *, latest_path: Path, prev_path: Path | None,
                    conflicts: list[dict], appended: list[dict], diff: dict,
                    coverage: dict | None = None,
                    medal_totals: dict | None = None) -> None:
    lines: list[str] = []
    lines.append(f"# GCC Games audit — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(f"- Latest ENHANCED: `{latest_path.name}`")
    lines.append(f"- Previous (for diff): `{prev_path.name if prev_path else '(none — single pull)'}`")
    lines.append(f"- Manual entries merged: **{len(appended)}** appended (no scraper target)")
    lines.append(f"- Conflicts flagged: **{len(conflicts)}**")
    lines.append("")

    if conflicts:
        lines.append("## ⚠️ Conflicts — scraper vs manual disagree")
        lines.append("")
        lines.append("| Athlete | Sport | Date | Discipline | Field | Scraper | Manual | Entered by | Notes |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for c in conflicts:
            lines.append(
                f"| {c['Athlete']} | {c['Sport']} | {c['Date']} | "
                f"{c['Discipline']} | {c['Field']} | `{c['Scraper_Value']}` | "
                f"`{c['Manual_Value']}` | {c['Entered_By']} | {c['Notes']} |"
            )
        lines.append("")
        lines.append("**Action required:** decide which value is correct, then update the")
        lines.append("manual_results.csv row (or re-run the scraper if the API was wrong).")
        lines.append("")

    if diff["medals"]:
        lines.append(f"## 🏅 New medals since last scrape ({len(diff['medals'])})")
        lines.append("")
        lines.append("| Medal | Athlete | Sport | Discipline |")
        lines.append("|---|---|---|---|")
        for k, medal, n in diff["medals"]:
            lines.append(f"| {medal} | {n.get('Athlete','')} | {n.get('Sport','')} | "
                         f"{n.get('Discipline','')} |")
        lines.append("")

    if diff["results"]:
        lines.append(f"## ✓ New results filled ({len(diff['results'])})")
        lines.append("")
        lines.append("| Athlete | Sport | Discipline | Rank | Result |")
        lines.append("|---|---|---|---|---|")
        for k, rank, result, n in diff["results"][:50]:
            lines.append(f"| {n.get('Athlete','')} | {n.get('Sport','')} | "
                         f"{n.get('Discipline','')} | {rank or ''} | {result or ''} |")
        if len(diff["results"]) > 50:
            lines.append(f"\n_…and {len(diff['results']) - 50} more (see full diff)_")
        lines.append("")

    if diff["status"]:
        by_change = defaultdict(int)
        for k, o, n, _ in diff["status"]:
            by_change[f"{o or '∅'} → {n or '∅'}"] += 1
        lines.append(f"## ~ Status changes ({len(diff['status'])})")
        lines.append("")
        for change, n in sorted(by_change.items(), key=lambda x: -x[1]):
            lines.append(f"- **{n}** × `{change}`")
        lines.append("")

    if diff["new"]:
        by_sport = defaultdict(list)
        for r in diff["new"]:
            by_sport[r.get("Sport", "?")].append(r)
        lines.append(f"## + New entries by sport ({len(diff['new'])})")
        lines.append("")
        for sport, rs in sorted(by_sport.items(), key=lambda x: -len(x[1])):
            lines.append(f"- **{sport}**: {len(rs)}")
        lines.append("")

    if diff["dropped"]:
        lines.append(f"## − Dropped entries ({len(diff['dropped'])})")
        lines.append("")
        lines.append("Entries present in the previous pull but missing now. Usually a")
        lines.append("competition ID got renumbered — check if any rows need to be")
        lines.append("re-added via manual_results.csv.")
        lines.append("")
        for r in diff["dropped"][:25]:
            lines.append(f"- {r.get('Athlete','')} ({r.get('Sport','')}) — "
                         f"{r.get('Discipline','')}, {r.get('Date','')}")
        if len(diff["dropped"]) > 25:
            lines.append(f"- _…and {len(diff['dropped']) - 25} more_")
        lines.append("")

    if appended:
        lines.append(f"## 📝 Manual entries appended ({len(appended)})")
        lines.append("")
        lines.append("Rows added by manual_results.csv with no matching scraper row.")
        lines.append("")
        lines.append("| Athlete | Sport | Date | Discipline | Result | Entered by |")
        lines.append("|---|---|---|---|---|---|")
        for r in appended:
            lines.append(f"| {r.get('Athlete','')} | {r.get('Sport','')} | "
                         f"{r.get('Date','')} | {r.get('Discipline','')} | "
                         f"{r.get('Result','')} | {r.get('Manual_Entered_By','')} |")
        lines.append("")

    # ---- Per-sport coverage gap section ----
    if coverage:
        # Headline: official NOC totals vs row-level medals
        my_medal_total = sum(s["with_medal"] for s in coverage.values())
        ksa_official = (medal_totals or {}).get("KSA", {})
        official_total = ksa_official.get("Total", 0)
        if official_total > my_medal_total:
            gap = official_total - my_medal_total
            lines.append(f"## 🚨 Coverage gap — {gap} medals not tagged at row level")
            lines.append("")
            lines.append(f"- Official NOC table (KSA): **{ksa_official.get('Gold', 0)}G "
                         f"/ {ksa_official.get('Silver', 0)}S "
                         f"/ {ksa_official.get('Bronze', 0)}B = "
                         f"{official_total} medals**")
            lines.append(f"- Row-level data has Medal populated on **{my_medal_total} rows**")
            lines.append(f"- **Gap: {gap}** — the API publishes the NOC-level total but doesn't")
            lines.append(f"  tag the Medal column on individual rows for some team / match sports.")
            lines.append("")

        # Per-sport breakdown
        lines.append("## 📋 Per-sport coverage")
        lines.append("")
        lines.append("| Sport | Rows | Official | Result populated | Medals tagged | Likely medals | Empty Official |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for sport, s in sorted(coverage.items(), key=lambda x: -x[1]["total"]):
            flag = ""
            if s["likely_medals"] or s["empty_official"]:
                flag = " ⚠️"
            lines.append(f"| {sport}{flag} | {s['total']} | {s['official']} | "
                         f"{s['with_result']} | {s['with_medal']} | "
                         f"{len(s['likely_medals'])} | {len(s['empty_official'])} |")
        lines.append("")

        # Likely-medal candidates per sport
        candidates = [(sp, s) for sp, s in coverage.items() if s["likely_medals"]]
        if candidates:
            lines.append("### 🎯 Likely medals not tagged")
            lines.append("")
            lines.append("Rows where `Rank` is 1/2/3 in a final/knockout phase but the `Medal`")
            lines.append("column is empty. Almost certainly podiums the API never tagged.")
            lines.append("Review and add to `manual_results.csv` with the correct medal letter.")
            lines.append("")
            lines.append("| Sport | Athlete | Discipline | Phase | Rank | Result |")
            lines.append("|---|---|---|---|---:|---|")
            for sport, s in sorted(candidates, key=lambda x: -len(x[1]["likely_medals"])):
                for r in s["likely_medals"][:8]:
                    disc = str(r.get("Discipline", "")).replace("’", "'")
                    lines.append(f"| {sport} | {r.get('Athlete', '')} | {disc} | "
                                 f"{r.get('Phase', '')} | {r.get('Rank', '')} | "
                                 f"{r.get('Result', '') or '—'} |")
                if len(s["likely_medals"]) > 8:
                    lines.append(f"| {sport} | _…and {len(s['likely_medals']) - 8} more_ | | | | |")
            lines.append("")

        # Empty Official rows — Status=Official but no Rank or Result
        gaps = [(sp, s) for sp, s in coverage.items() if s["empty_official"]]
        if gaps:
            total_empty = sum(len(s["empty_official"]) for _, s in gaps)
            lines.append(f"### 📭 Empty Official rows ({total_empty})")
            lines.append("")
            lines.append("Rows the API marked `Status=Official` but with both `Rank` and")
            lines.append("`Result` blank. Either the result was withdrawn / not yet published,")
            lines.append("or the API only updated the status flag. Top 30:")
            lines.append("")
            lines.append("| Sport | Date | Athlete | Discipline | Phase |")
            lines.append("|---|---|---|---|---|")
            flat = []
            for sport, s in gaps:
                for r in s["empty_official"]:
                    flat.append((sport, r))
            for sport, r in flat[:30]:
                disc = str(r.get("Discipline", "")).replace("’", "'")
                lines.append(f"| {sport} | {r.get('Date', '')} | {r.get('Athlete', '')} | "
                             f"{disc} | {r.get('Phase', '')} |")
            if total_empty > 30:
                lines.append(f"\n_…and {total_empty - 30} more empty Official rows_")
            lines.append("")

    # ---- Expected-events structural check ----
    # The schedule covers events that haven't generated results yet (e.g.
    # archery), so we union it with the row-data disciplines.
    expected_gaps = expected_events_check(coverage, load_schedule_disciplines())
    if expected_gaps:
        total_missing = sum(len(v) for v in expected_gaps.values())
        lines.append(f"## 🧩 Missing expected events ({total_missing})")
        lines.append("")
        lines.append("Sports with a fixed competition structure (Archery: 3 individual +")
        lines.append("3 team disciplines × 2 genders; Swimming: 4×100m + 4×400m relays;")
        lines.append("etc.) that are missing standard events in the data. Add via the")
        lines.append("manual_results.csv if KSA actually entered the event.")
        lines.append("")
        lines.append("| Sport | Missing event | Notes |")
        lines.append("|---|---|---|")
        for sport, items in expected_gaps.items():
            for evt, note in items:
                lines.append(f"| {sport} | {evt} | {note} |")
        lines.append("")

    if (not conflicts and not diff["new"] and not diff["status"] and not appended
            and not (coverage and any(s["likely_medals"] or s["empty_official"]
                                       for s in coverage.values()))
            and not expected_gaps):
        lines.append("## ✅ Nothing to audit")
        lines.append("")
        lines.append("No conflicts, no diff against previous pull, no manual entries to merge,")
        lines.append("no coverage gaps detected.")

    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", help="Compare against the scrape pull with this timestamp")
    args = p.parse_args()

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    latest_path = find_latest_enhanced()
    print(f"[ENHANCED] {latest_path.name}")
    rows = load_csv(latest_path)
    print(f"  {len(rows)} rows")

    if not MANUAL_CSV.exists():
        MANUAL_CSV.parent.mkdir(parents=True, exist_ok=True)
        MANUAL_CSV.write_text(
            "Athlete,Sport,Date,Competition,Comp Set,Class,Discipline,Phase,Gender,Age,"
            "Rank,Result,Medal,Wind,Attempt,Status,Country,Entered_By,Entered_At,"
            "Entry_Source,Notes\n",
            encoding="utf-8",
        )
        print(f"[MANUAL]   created empty template at {MANUAL_CSV.relative_to(HERE)}")
    manual = load_csv(MANUAL_CSV)
    print(f"[MANUAL]   {len(manual)} rows in {MANUAL_CSV.name}")

    merged_rows, conflicts, appended = merge(rows, manual)
    print(f"  merged.   conflicts={len(conflicts)}  appended={len(appended)}")

    # Diff against prior raw scrape
    prev_path = find_previous_raw(latest_path, args.since)
    diff = diff_against_previous(latest_path, prev_path)
    print(f"[DIFF]     prev={prev_path.name if prev_path else '(none)'}")
    print(f"  new={len(diff['new'])} dropped={len(diff['dropped'])} "
          f"status={len(diff['status'])} results={len(diff['results'])} "
          f"medals={len(diff['medals'])}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = RESULTS_DIR / f"ENHANCED_WITH_MANUAL_{ts}.csv"
    fields = list(merged_rows[0].keys()) if merged_rows else []
    for c in EXTRA_COLS:
        if c not in fields:
            fields.append(c)
    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in merged_rows:
            for c in fields:
                r.setdefault(c, "")
            w.writerow(r)
    print(f"[SAVE] {out_csv.name}")

    # Write conflicts CSV too (machine-readable)
    if conflicts:
        conf_csv = AUDIT_DIR / f"CONFLICTS_{ts}.csv"
        with conf_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(conflicts[0].keys()))
            w.writeheader()
            w.writerows(conflicts)
        print(f"[SAVE] {conf_csv.name}")

    # Coverage audit over the merged output (drives gap + expected-events sections)
    coverage = coverage_audit(merged_rows)
    medal_totals = load_medal_totals()
    gap_total = sum(s["with_medal"] for s in coverage.values())
    official_total = medal_totals.get("KSA", {}).get("Total", 0)
    if official_total and gap_total < official_total:
        print(f"[COVERAGE] {official_total - gap_total} medal(s) untagged at row level "
              f"(official KSA: {official_total}, row data: {gap_total})")
    empty_total = sum(len(s["empty_official"]) for s in coverage.values())
    likely_total = sum(len(s["likely_medals"]) for s in coverage.values())
    if empty_total or likely_total:
        print(f"[COVERAGE] {empty_total} empty Official rows, "
              f"{likely_total} likely-medal candidates needing review")

    # Markdown audit
    md = AUDIT_DIR / f"CHANGES_{ts}.md"
    write_audit_md(md, latest_path=latest_path, prev_path=prev_path,
                   conflicts=conflicts, appended=appended, diff=diff,
                   coverage=coverage, medal_totals=medal_totals)
    print(f"[SAVE] {md.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
