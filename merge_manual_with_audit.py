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


def write_audit_md(out: Path, *, latest_path: Path, prev_path: Path | None,
                    conflicts: list[dict], appended: list[dict], diff: dict) -> None:
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

    if not conflicts and not diff["new"] and not diff["status"] and not appended:
        lines.append("## ✅ Nothing to audit")
        lines.append("")
        lines.append("No conflicts, no diff against previous pull, no manual entries to merge.")

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

    # Markdown audit
    md = AUDIT_DIR / f"CHANGES_{ts}.md"
    write_audit_md(md, latest_path=latest_path, prev_path=prev_path,
                   conflicts=conflicts, appended=appended, diff=diff)
    print(f"[SAVE] {md.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
