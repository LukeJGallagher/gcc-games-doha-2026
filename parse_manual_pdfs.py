"""
Drop-in PDF intake for sports gccgames.qa doesn't cover.

How it works:
  Drop PDF files into data/manual_pdfs/<sport>/  (sport folders are free-form,
  e.g. data/manual_pdfs/Karate/, data/manual_pdfs/Jujitsu/).
  Run this script and it tries — in order:

    1. Structured table extraction with pdfplumber + per-sport regex
       (currently supports the gccgames.qa athletics format used by
       process_gcc_games.py — works for any timing-portal PDF with
       "RANK BIB NAME (CLUB|COUNTRY) ... RESULT" header).
    2. AI fallback (OpenRouter + free models) by handing the text to
       the project's existing terminal_scraper.OpenRouterClient. Requires
       OPENROUTER_API_KEY in .env.

Whatever it extracts gets appended to data/manual_results.csv with
Entry_Source=pdf:<filename> so the audit log can trace provenance.

  python parse_manual_pdfs.py                    # process everything new in manual_pdfs/
  python parse_manual_pdfs.py --sport Karate     # one sport
  python parse_manual_pdfs.py --file path.pdf --sport Karate --date 2026-05-16
  python parse_manual_pdfs.py --ai               # force AI extraction (skip regex first)
  python parse_manual_pdfs.py --dry-run          # show what would be appended; don't write
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pdfplumber

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT))  # for terminal_scraper imports
sys.path.insert(0, str(HERE))

MANUAL_PDF_DIR = HERE / "data" / "manual_pdfs"
MANUAL_CSV = HERE / "data" / "manual_results.csv"
PROCESSED_LOG = HERE / "data" / "manual_pdfs_processed.txt"

COUNTRY_CODES = {"KSA", "SAU", "QAT", "UAE", "OMA", "BRN", "KUW", "BHR", "OMN"}
MEDAL_WORDS = {"GOLD": "G", "SILVER": "S", "BRONZE": "B"}
COMPETITION = "4th GCC Games Doha 2026"


def already_processed() -> set[str]:
    if not PROCESSED_LOG.exists():
        return set()
    return set(PROCESSED_LOG.read_text(encoding="utf-8").splitlines())


def mark_processed(rel: str) -> None:
    PROCESSED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PROCESSED_LOG.open("a", encoding="utf-8") as f:
        f.write(rel + "\n")


def extract_structured(text: str, sport: str, pdf_name: str, date: str | None) -> list[dict]:
    """Try the gccgames.qa athletics-style table layout. Returns [] if no header found."""
    header_re = re.compile(
        r"^\s*RANK\s+BIB\s+NAME\s+(?:CLUB|COUNTRY)\s+(LANE\s+)?RESULT", re.I,
    )
    date_match = re.search(r"(20\d{2})/(\d{2})/(\d{2})", text)
    date_iso = (f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
                if date_match else (date or ""))

    # Best-effort discipline/event from the first non-blank line.
    # PDFs typically print "<EVENT> <CLASS>" (e.g. "100M MEN", "HIGH JUMP WOMEN").
    # We capture the gender from CLASS, then strip it so the discipline string
    # matches the gccgames.qa convention ("Men's 100 Metres" -> normalised "100m").
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    upper = first_line.upper()
    gender = ""
    if re.search(r"\bWOMEN\b", upper): gender = "F"
    elif re.search(r"\bMEN\b", upper): gender = "M"
    elif re.search(r"\bMIXED\b", upper): gender = "X"
    cleaned = re.sub(r"\b(MEN|WOMEN|MIXED|HEPATATHLON|HEPTATHLON|HEPTHALON|DECATHLON)\b",
                     "", first_line, flags=re.I)
    discipline = re.sub(r"\s+", " ", cleaned).strip().title()[:60]

    parsing = False
    has_lane = False
    rows: list[dict] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if not parsing:
            m = header_re.match(s)
            if m:
                has_lane = bool(m.group(1))
                parsing = True
            continue

        tokens = s.split()
        if len(tokens) < 4:
            continue
        if all(re.fullmatch(r"[-OX]+", t) for t in tokens):
            continue
        rank = tokens[0]
        if not (rank.isdigit() or rank.upper() in {"DNF", "DNS", "DQ", "NM", "NH"}):
            continue
        country_idx = None
        for i in range(1, len(tokens)):
            if tokens[i].upper() in COUNTRY_CODES:
                country_idx = i
                break
        if country_idx is None:
            continue
        bib_idx = None
        for i in range(country_idx - 1, 0, -1):
            if re.fullmatch(r"\d{1,4}", tokens[i]):
                bib_idx = i
                break
        if bib_idx is None or bib_idx == 0:
            continue
        name = " ".join(tokens[bib_idx + 1:country_idx]).strip()
        if not name:
            continue
        country = tokens[country_idx].upper()
        country = {"SAU": "KSA", "BHR": "BRN"}.get(country, country)
        tail = tokens[country_idx + 1:]
        if has_lane and tail and re.fullmatch(r"\d+", tail[0]):
            tail = tail[1:]
        medal = ""
        if tail and tail[-1].upper() in MEDAL_WORDS:
            medal = MEDAL_WORDS[tail[-1].upper()]
            tail = tail[:-1]
        result_val = " ".join(tail).strip()
        if not result_val:
            continue
        rows.append({
            "Athlete": name.title(),
            "Sport": sport,
            "Date": date_iso,
            "Competition": COMPETITION,
            "Comp Set": "GCC Games",
            "Class": "",
            "Discipline": discipline,
            "Phase": "Final",
            "Gender": gender,
            "Age": "",
            "Rank": rank,
            "Result": result_val,
            "Medal": medal,
            "Wind": "",
            "Attempt": "",
            "Status": "Podium" if medal else "Official",
            "Country": country,
        })
    return rows


async def extract_ai(text: str, sport: str, pdf_name: str, date: str | None) -> list[dict]:
    """Hand the text to the project's OpenRouter client for free/AI extraction."""
    try:
        from terminal_scraper import OpenRouterClient  # type: ignore
    except Exception as e:
        print(f"  [AI] cannot import OpenRouterClient: {e}")
        return []
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        print("  [AI] OPENROUTER_API_KEY not set; skipping AI extraction")
        return []
    client = OpenRouterClient(api_key)
    results = await client.analyze_content(
        content=text,
        sport=sport,
        competition_name=COMPETITION,
        competition_date=date or "",
        extract_all=True,
    )
    out: list[dict] = []
    for r in results or []:
        out.append({
            "Athlete": r.athlete_name,
            "Sport": r.sport or sport,
            "Date": r.date or (date or ""),
            "Competition": r.competition or COMPETITION,
            "Comp Set": r.comp_set or "GCC Games",
            "Class": r.classification or "",
            "Discipline": r.discipline,
            "Phase": "Final",
            "Gender": r.gender,
            "Age": r.age,
            "Rank": r.rank,
            "Result": r.result,
            "Medal": r.medal,
            "Wind": r.wind,
            "Attempt": r.attempt_number,
            "Status": r.status or ("Podium" if r.medal else "Official"),
            "Country": (r.country or "").upper(),
        })
    return out


def parse_pdf(path: Path, sport: str, date: str | None, force_ai: bool) -> list[dict]:
    print(f"  reading: {path.name}")
    with pdfplumber.open(path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    if not text.strip():
        print("    [WARN] no extractable text — would need OCR. Use pdf_batch_processor.py instead.")
        return []

    if not force_ai:
        rows = extract_structured(text, sport, path.name, date)
        if rows:
            print(f"    [STRUCT] {len(rows)} rows via regex")
            return rows
        print("    [STRUCT] no rows matched the timing-portal template — falling back to AI")

    rows = asyncio.run(extract_ai(text, sport, path.name, date))
    print(f"    [AI] {len(rows)} rows extracted")
    return rows


def append_to_manual_csv(new_rows: list[dict], pdf_name: str, dry_run: bool) -> None:
    if not new_rows:
        return
    now = datetime.now().isoformat(timespec="seconds")
    for r in new_rows:
        r["Entered_By"] = "pdf_parser"
        r["Entered_At"] = now
        r["Entry_Source"] = f"pdf:{pdf_name}"
        r["Notes"] = ""

    # Read existing rows to keep dedup discipline
    existing = []
    if MANUAL_CSV.exists():
        with MANUAL_CSV.open(encoding="utf-8-sig") as f:
            existing = list(csv.DictReader(f))
    keyed = {(r.get("Athlete", "").lower().strip(),
              r.get("Date", "").strip(),
              r.get("Discipline", "").lower().strip()) for r in existing}
    fresh = [r for r in new_rows
             if (r["Athlete"].lower().strip(),
                 r["Date"].strip(),
                 r["Discipline"].lower().strip()) not in keyed]
    dupes = len(new_rows) - len(fresh)

    if dry_run:
        print(f"    [DRY] would append {len(fresh)} rows ({dupes} dupes skipped)")
        for r in fresh[:5]:
            print(f"      {r['Athlete']:<26} {r['Discipline']:<20} {r['Rank']:<3} {r['Result']}")
        if len(fresh) > 5:
            print(f"      ...and {len(fresh)-5} more")
        return

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
    print(f"    [WRITE] appended {len(fresh)} rows to {MANUAL_CSV.name} ({dupes} dupes skipped)")


def discover_pdfs(sport_filter: str | None, only_file: Path | None) -> list[tuple[Path, str]]:
    """Returns [(pdf_path, sport_name), ...]"""
    if only_file:
        return [(only_file, sport_filter or only_file.parent.name)]
    MANUAL_PDF_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for sport_dir in sorted(p for p in MANUAL_PDF_DIR.iterdir() if p.is_dir()):
        if sport_filter and sport_dir.name.lower() != sport_filter.lower():
            continue
        for pdf in sorted(sport_dir.glob("*.pdf")):
            out.append((pdf, sport_dir.name))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sport", help="Process only this sport folder")
    p.add_argument("--file", type=Path, help="Process a single PDF path")
    p.add_argument("--date", help="Date override (YYYY-MM-DD) for PDFs that don't include one")
    p.add_argument("--ai", action="store_true", help="Skip regex; go straight to AI")
    p.add_argument("--dry-run", action="store_true", help="Don't write to manual_results.csv")
    p.add_argument("--reparse", action="store_true",
                   help="Re-process even PDFs already in manual_pdfs_processed.txt")
    args = p.parse_args()

    pdfs = discover_pdfs(args.sport, args.file)
    if not pdfs:
        print(f"No PDFs found in {MANUAL_PDF_DIR}")
        print(f"Drop PDFs into data/manual_pdfs/<sport>/ and re-run.")
        return 0

    seen = set() if args.reparse else already_processed()
    print(f"Discovered {len(pdfs)} PDF(s) across "
          f"{len({s for _,s in pdfs})} sport folder(s)")

    for pdf_path, sport in pdfs:
        rel = str(pdf_path.relative_to(HERE)) if pdf_path.is_relative_to(HERE) else str(pdf_path)
        if rel in seen and not args.reparse:
            print(f"\n[SKIP] {rel} (already processed; --reparse to redo)")
            continue
        print(f"\n[{sport}] {pdf_path.name}")
        try:
            rows = parse_pdf(pdf_path, sport, args.date, args.ai)
        except Exception as e:
            print(f"  [ERROR] {e}")
            continue
        if rows:
            append_to_manual_csv(rows, pdf_path.name, args.dry_run)
        if not args.dry_run and rows:
            mark_processed(rel)

    print("\nDone. Run `python merge_manual_with_audit.py` to overlay these into the")
    print("ENHANCED file and produce a Markdown audit report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
