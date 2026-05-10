# 4th GCC Games Doha 2026 - Scraper

Pulls schedule, results, and medal table from `gccgames.qa` via its public Frappe JSON API. No Selenium, no auth, no AI fallback needed.

**Games dates: 12-22 May 2026** (Doha, Qatar). 18 sports, 6 GCC nations (BRN, KUW, OMA, QAT, KSA, UAE).

## Files

| File | Purpose |
|---|---|
| `config.py` | Constants: routes, GCC nations, fallback sport list, output schemas |
| `api_client.py` | Tiny wrapper around the 6 Frappe endpoints |
| `scraper.py` | Pulls per-sport data once, derives schedule + results in one pass |
| `enhance_rankings.py` | Adds `Rank_Std` column (Q/DNQ/R32/QF/SF/F/1/2/3) from `pos`+`Phase` |
| `ksa_filter.py` | Defensive cleanup: removes "vs KSA" pattern false positives |
| `daily_diff.py` | Diffs latest two pulls — new entries, status changes, new results, new medals |
| `match_athletes.py` | Joins KSA roster (Excel from BORNAN) to live schedule. One row per athlete per phase. Outputs `KSA_ATHLETE_SCHEDULE_*.csv` + `UNMATCHED_EVENTS_*.csv` |
| `run_scrape.py` | Friendly CLI wrapper for everything |
| `data/schedule/` | `SCHEDULE_<ts>.csv` + `RAW_<ts>.json` (full API dump) |
| `data/results/` | `RESULTS_ALL_<ts>.csv`, `RESULTS_KSA_<ts>.csv`, `MEDALS_<ts>.csv`, `ENHANCED_*`, `RESULTS_KSA_CLEAN_*` |
| `logs/` | Run logs |

## Run

```bash
cd 4th_GCC_Games_Doha2026

# Scrape
python scraper.py                           # everything (schedule + results + medals)
python scraper.py --mode schedule
python scraper.py --mode results
python scraper.py --sports Athletics Swimming

# Post-process
python daily_diff.py                        # what's new since last pull
python enhance_rankings.py --ksa            # add Rank_Std column
python ksa_filter.py                        # remove "vs KSA" false positives

# All-in-one (scrape + diff + enhance + clean)
python run_scrape.py --full
```

## Endpoints discovered (no auth)

| Method | Returns |
|---|---|
| `gms.api.sport.sports` | full sport list (id, names, pictogram) |
| `gms.api.competition.competition_dates` | competition day list |
| `gms.api.competition.competition_by_date?date=YYYY-MM-DD` | per-day sport summary |
| `gms.api.competition.grid_competitions` | thin schedule grid (all sports) |
| `gms.api.results.sport_results_summary?sport=<id>` | rich per-sport: comps, venue, gender, phase, participants, results |
| `gms.api.medal_standings.medal_standings` | live medal table |

The scraper uses **`sport_results_summary`** as the single source of truth — one call per sport gives both schedule and results. ~5 seconds for a full pull of 18 sports.

## Output schema

**Results CSV** (validator-compatible 18 cols):
`Athlete, Sport, Date, Competition, Comp Set, Class, Discipline, Gender, Age, Rank, Result, Medal, Wind, Attempt, Status, Country, Detection_Method, Source_URL`

**Schedule CSV**:
`Date, Time, Sport, Discipline, Phase, Gender, Venue, Country_Entries, Event_ID, Source_URL`

For team sports (Basketball, Handball, Volleyball) the Athlete column is `<Country> (Team)` and Country is the NOC code.

## Baseline pulled (2026-05-09, pre-games)

- **422 scheduled competitions** across 17 sports with venue, phase, time
- **75 participant entries** already pre-loaded (mostly basketball/handball brackets)
- **14 KSA team entries** confirmed (Basketball 3x3, 5x5, Handball)
- Medal table: all 6 nations, zero medals (games haven't started)

## Dependencies

Standard library only. No selenium, no requests, no `.env`. Just:
```bash
python scraper.py
```

## Once games start (12 May)

Just rerun. The same per-sport call will start returning populated `results_summary.participants` with `pos` and `final_result`. The KSA filter is automatic.
