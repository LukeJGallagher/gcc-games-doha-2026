"""
Fill 'Scheduled' athletics rows in the latest ENHANCED_RESULTS_KSA_*.csv with
real results parsed from the GCC Games timing-portal PDFs.

The master gccgames.qa portal lags the athletics timing portal by hours/days,
so this script bridges the gap until the master portal catches up.

- Source of truth (athletes/scheduling/Phase): existing ENHANCED_RESULTS file
- Source of results (rank/result/medal/wind): SAUDI_ONLY CSV produced by
  ../process_gcc_games.py

Match key: (athlete normalised, date, discipline normalised).
Writes a new ENHANCED_RESULTS_KSA_<orig_ts>_PDF_MERGED_<new_ts>.csv alongside
the input so the original is preserved untouched.

Re-applies the project's standardise_rank logic so Rank_Std stays consistent.
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import datetime
from pathlib import Path

# Project-local import (same standardise_rank used by enhance_rankings.py)
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from enhance_rankings import standardise_rank  # noqa: E402

RESULTS_DIR = HERE / "data" / "results"
PARSER_CSV_DIR = HERE.parent / "PDF_Extracted" / "CSV_Results"


def normalise_discipline(s: str) -> str:
    """'Women's 100 Metres Hurdles' / '100M Hurdles' -> '100m hurdles'."""
    s = (s or "").strip()
    s = s.replace("’", "'")  # curly to straight
    # NB: no re.I — the character class would otherwise also match an "S"
    # at the start of the next word (e.g. "Men's Shot Put" -> "hot Put").
    s = re.sub(r"^(?:Men|Women|Mixed)['s\s]+", "", s)
    s = re.sub(r"\s+Metres\b", "m", s, flags=re.I)
    s = re.sub(r"(\d)\s*M\b", r"\1m", s)  # 100M -> 100m
    # gccgames.qa uses "Javelin Throw" / "Hammer Throw" but the timing PDFs and
    # Azure FD events API just say "JAVELIN" / "HAMMER". Strip the suffix so
    # both sources collapse to the same key.
    s = re.sub(r"\s+Throw\b", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def normalise_athlete(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def match_key(row: dict, athlete_field: str = "Athlete") -> tuple:
    return (
        normalise_athlete(row.get(athlete_field, "")),
        (row.get("Date") or "").strip(),
        normalise_discipline(row.get("Discipline", "")),
    )


def latest(glob: str, root: Path) -> Path:
    matches = sorted(root.glob(glob))
    if not matches:
        raise SystemExit(f"No file matching {glob} in {root}")
    return matches[-1]


def main() -> int:
    src = latest("ENHANCED_RESULTS_KSA_*.csv", RESULTS_DIR)
    pdf_csv = latest("GCC_Games_2026_Athletics_*_SAUDI_ONLY.csv", PARSER_CSV_DIR)
    print(f"[ENHANCED] {src.name}")
    print(f"[PDF CSV ] {pdf_csv.name}\n")

    with open(src, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    with open(pdf_csv, encoding="utf-8") as f:
        pdf_rows = list(csv.DictReader(f))

    print(f"Existing rows: {len(rows)}  |  PDF rows: {len(pdf_rows)}\n")

    pdf_index = {match_key(r): r for r in pdf_rows}
    if len(pdf_index) != len(pdf_rows):
        print("  warn: duplicate keys in PDF input collapsed")

    filled, completed_already, no_match = 0, 0, 0
    pdf_keys_used = set()

    def has_result(r: dict) -> bool:
        return bool((r.get("Result") or "").strip()) or bool((r.get("Rank") or "").strip())

    for r in rows:
        if r.get("Sport") != "Athletics":
            continue
        # Fill if Rank+Result are blank, regardless of Status. The API sometimes
        # marks rows Status=Official without populating the result fields.
        if has_result(r):
            completed_already += 1
            continue
        key = match_key(r)
        m = pdf_index.get(key)
        if not m:
            no_match += 1
            continue
        r["Rank"] = m["Rank"]
        r["Result"] = m["Result"]
        r["Medal"] = m["Medal"]
        r["Wind"] = m["Wind"]
        r["Status"] = "Official"
        # Preserve existing Detection_Method if it's "GCC API"; append our source
        existing_dm = (r.get("Detection_Method") or "").strip()
        r["Detection_Method"] = (
            f"{existing_dm} + GCC Timing PDF" if existing_dm and "PDF" not in existing_dm
            else "GCC Timing PDF"
        )
        r["Rank_Original"] = m["Rank"]
        r["Rank_Std"] = standardise_rank(r)
        filled += 1
        pdf_keys_used.add(key)

    # Append PDF rows that don't exist in the source at all (e.g. competition IDs
    # the API later renumbered, dropping the original Scheduled row entirely).
    appended = []
    fields = list(rows[0].keys())
    for k, m in pdf_index.items():
        if k in pdf_keys_used:
            continue
        # Skip if a row with the same key already exists in the source (already filled
        # in a prior run / by the API)
        if any(match_key(r) == k and r.get("Sport") == "Athletics" for r in rows):
            continue
        # Build a new row in the ENHANCED schema. Best-effort discipline string;
        # the existing pattern is "Men's/Women's <Event Title>".
        gender_label = {"M": "Men", "F": "Women"}.get(m["Gender"], "")
        disc_title = m["Discipline"]
        # Restore the "Throw" suffix where the API uses it
        if disc_title in {"Javelin", "Hammer"}:
            disc_title = f"{disc_title} Throw"
        new_disc = f"{gender_label}'s {disc_title}" if gender_label else disc_title
        new_row = {f: "" for f in fields}
        new_row.update({
            "Athlete":           m["Athlete"],
            "Sport":             "Athletics",
            "Date":              m["Date"],
            "Competition":       m["Competition"],
            "Comp Set":          "GCC Games",
            "Discipline":        new_disc,
            "Phase":             m.get("Phase") or "Final",
            "Gender":            "Women" if m["Gender"] == "F" else "Men",
            "Rank":              m["Rank"],
            "Result":            m["Result"],
            "Medal":             m["Medal"],
            "Wind":              m["Wind"],
            "Status":            "Official",
            "Country":           "KSA",
            "Detection_Method":  "GCC Timing PDF (appended)",
            "Source_URL":        m["Source_URL"],
        })
        new_row["Rank_Original"] = m["Rank"]
        new_row["Rank_Std"] = standardise_rank(new_row)
        appended.append(new_row)
        pdf_keys_used.add(k)

    rows.extend(appended)
    unused_pdf = [k for k in pdf_index if k not in pdf_keys_used]
    print(f"Filled:                {filled}")
    print(f"Appended (no target):  {len(appended)}")
    print(f"Already complete:      {completed_already}")
    print(f"Empty w/ no PDF match: {no_match}")
    print(f"PDF rows unused:       {len(unused_pdf)}")
    if unused_pdf:
        print("  Unused PDF rows (no matching scheduled row to fill):")
        for k in unused_pdf:
            print(f"    {k}")

    if filled == 0 and len(appended) == 0:
        print("\nNo rows updated. Exiting without writing a new file.")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Strip any prior _PDF_MERGED_<ts> suffix so the filename doesn't accumulate.
    base_stem = re.sub(r"_PDF_MERGED_\d{8}_\d{6}$", "", src.stem)
    out = src.parent / f"{base_stem}_PDF_MERGED_{ts}.csv"
    fields = list(rows[0].keys())
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\n[SAVE] {out.name}")

    # Show the rows we updated
    print("\nUpdated rows:")
    print(f"{'Date':<11} {'Athlete':<22} {'Discipline':<26} {'Phase':<12} "
          f"{'Rk':<4} {'Result':<10} {'Med':<3} {'Status':<10}")
    for r in rows:
        if r.get("Sport") == "Athletics" and r.get("Status") == "Official":
            print(f"{r['Date']:<11} {r['Athlete']:<22} "
                  f"{r['Discipline'][:25].replace('’',chr(39)):<26} {r['Phase']:<12} "
                  f"{r['Rank']:<4} {r['Result']:<10} {r['Medal']:<3} {r['Status']:<10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
