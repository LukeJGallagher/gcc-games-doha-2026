# Manual entry, PDF intake, and audit workflow

The `run_scrape.py` chain handles everything gccgames.qa publishes. Three new
scripts handle everything it doesn't.

## Quick reference

| Goal | Command |
|---|---|
| Type results in by hand | Edit `data/manual_results.csv` in Excel |
| Drop in a federation PDF | Save it to `data/manual_pdfs/<Sport>/`, then `python parse_manual_pdfs.py` |
| Build the final file + audit report | `python merge_manual_with_audit.py` |

## End-to-end cadence after each session

```bash
python run_scrape.py --full                  # 1. fresh API pull
python parse_manual_pdfs.py                  # 2. ingest any new PDFs in data/manual_pdfs/
python merge_manual_with_audit.py            # 3. produce ENHANCED_WITH_MANUAL_*.csv + audit
```

The audit Markdown lives at `data/audit/CHANGES_<ts>.md` — open it and review
before pointing the dashboard at the new file.

## File map

```
4th_GCC_Games_Doha2026/
├── data/
│   ├── manual_results.csv          # PA-edited, source of truth for non-API data
│   ├── manual_pdfs/                # drop PDFs here, organised by sport
│   │   ├── Karate/
│   │   ├── Jujitsu/
│   │   └── Shooting/
│   ├── manual_pdfs_processed.txt   # PDFs the parser has already seen
│   ├── results/
│   │   └── ENHANCED_WITH_MANUAL_<ts>.csv   # final output the dashboard reads
│   └── audit/
│       ├── CHANGES_<ts>.md         # human-readable audit report
│       └── CONFLICTS_<ts>.csv      # machine-readable conflicts list
├── parse_manual_pdfs.py
└── merge_manual_with_audit.py
```

## When to use manual entry

- A sport doesn't appear in `gms.api.sport.sports` at all (rare).
- The API returns the event but never populates a result (common late in the meet).
- The scraper marks a row Status=Official but leaves Rank/Result blank.
- The API value is wrong and the federation has issued a correction.
- The competition ID got renumbered and the original row was dropped (we've seen this with heptathlon Day 2 events).

## How conflicts are handled

Track-both-flag-conflicts. Manual never silently overwrites the scraper.

| Scraper has | Manual has | Result |
|---|---|---|
| Empty | Value | Manual value fills the row; `Detection_Method += " + Manual"` |
| Value | Empty | Scraper value kept untouched |
| Value A | Value B (≠ A) | Scraper stays in `Rank/Result/Medal`; manual goes into `Rank_Manual/Result_Manual/Medal_Manual`; `Conflict_Flag = "y"`; row appears in the audit report under **⚠️ Conflicts** |
| No matching row | Anything | Manual row appended with `Detection_Method = "Manual"` |

After reviewing a conflict, decide which value is correct and either:
- Update the manual row in `manual_results.csv` to match the scraper (if the
  scraper was right), then re-run the merger.
- Leave the manual row as-is (if manual is right). The dashboard reads
  `Rank/Result/Medal` so the scraper value will show; you'll need to swap them
  manually if the manual value should be primary — that's a deliberate gate so
  you never overwrite a federation-published value by accident.

## manual_results.csv schema

Same first 17 columns as the ENHANCED file plus 4 provenance columns:

```
Athlete, Sport, Date, Competition, Comp Set, Class, Discipline, Phase,
Gender, Age, Rank, Result, Medal, Wind, Attempt, Status, Country,
Entered_By, Entered_At, Entry_Source, Notes
```

Required for a usable row: **Athlete, Sport, Date, Discipline**. Everything
else is optional. Match-key for joining to the scraper is
`(Athlete normalised, Date, Discipline normalised)`, where normalisation:
- strips `Men's` / `Women's` / `Mixed` prefix
- lowercases
- converts `100M` → `100m`, `100 Metres` → `100m`
- strips trailing ` Throw` (so Javelin = Javelin Throw)

## PDF intake notes

The parser tries two strategies per file:

1. **Structured regex** — works for any PDF where the result table header is
   `RANK BIB NAME (CLUB|COUNTRY) [LANE] RESULT` (the gccgames.qa athletics
   timing-portal layout). No API or AI cost.
2. **AI fallback** — hands the raw text to `terminal_scraper.OpenRouterClient`
   which cycles through five free OpenRouter models (Gemini 2.0 Flash etc) before
   touching the paid Claude key. Requires `OPENROUTER_API_KEY` in `.env`.

Force AI from the start with `--ai` (useful when you know the format won't
match regex, e.g. karate match-card PDFs).

Already-processed PDFs are tracked in `data/manual_pdfs_processed.txt`. Drop a
revised PDF in with a different filename to re-parse, or pass `--reparse` to
ignore the log.

## Audit report sections

`CHANGES_<ts>.md` is generated each merge run. Sections, in priority order:

1. **⚠️ Conflicts** — scraper vs manual disagreements. Read first.
2. **🏅 New medals** — medals that appeared since the previous scrape.
3. **✓ New results filled** — rows that gained a Rank/Result.
4. **~ Status changes** — Scheduled → Official transitions (aggregated).
5. **+ New entries by sport** — completely new rows in the latest pull.
6. **− Dropped entries** — present in the previous pull, missing now. Usually
   indicates a competition-ID renumbering. Spot-check and re-add via
   `manual_results.csv` if any rows shouldn't have vanished.
7. **📝 Manual entries appended** — manual rows that landed on the output file.
