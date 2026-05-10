# CLAUDE.md - 4th GCC Games Doha 2026

## What this folder is

API-based scraper for `gccgames.qa`. Pulls schedule + results + medals via the public Frappe JSON API. No Selenium, no auth, no AI.

## Files

- `config.py` - constants (routes, NOCs, fallback sport list, schemas)
- `api_client.py` - 6-method JSON wrapper around `/api/method/gms.api.*`
- `scraper.py` - one consolidated pull → schedule + results + medals
- `enhance_rankings.py` - adds Rank_Std (Q/DNQ/R32/QF/SF/F/1/2/3) column from Phase+Rank+Result+Medal
- `ksa_filter.py` - defensive cleanup: "vs KSA" pattern + opponent name allowlist (empty for now)
- `daily_diff.py` - compares two RESULTS_*.csv pulls; ports key insight from AYG day-by-day system
- `run_scrape.py` - CLI wrapper, supports --full to chain scrape→diff→enhance→clean

## Site facts

- Frappe backend, public JSON API (no auth needed for read methods)
- Games run **12-22 May 2026**, 18 sports, 6 GCC NOCs (BRN, KUW, OMA, QAT, KSA, UAE) — Yemen NOT participating this edition
- Sport list pulled live each run from `gms.api.sport.sports` (config.SPORTS is fallback only)
- Bahrain code is **BRN** in this API, not BHR

## Key endpoint: `gms.api.results.sport_results_summary?sport=<id>`

Single source of truth. Per sport, returns full competition list with:
- date, time, venue, field_of_play, gender_category, stage_name, status
- top-level `participants[]` (entries: NOC + team/athlete id, no result yet)
- `results_summary.participants[]` (with `pos` and `final_result` when played)
- `results_summary.periods[]` (per-period scores for team sports)

Two participant shapes the parser handles:
- Team: `{id: TEAM-XXXX, noc_code, noc_name, final_result, pos}` → Athlete becomes "<Country> (Team)"
- Individual: `{id, noc_code, athlete: {english_name}, ...}` → uses athlete.english_name

## Workflow

```bash
python scraper.py            # full pull, all sports
python scraper.py --mode results --sports Athletics Swimming
```

## When games start (12 May 2026)

No code changes needed. Same call, the `results_summary.participants` will start including `pos`/`final_result`/`medal` and the KSA filter does the rest. Run on demand.

## Lineage / why these files exist

Same vendor (BORNAN) built both the AYG and GCC results portals. AYG team
spent weeks on Selenium before discovering the JSON API; we leapfrogged
that on day one. The three post-processors port the patterns AYG proved:
- enhance_rankings.py - direct port of AYG enhance_rankings.py logic
- ksa_filter.py       - simplified port of AYG filter_ksa_only.py (API gives clean noc_code, so we don't need the 28-name AYG allowlist)
- daily_diff.py       - distillation of AYG day_by_day_scraper checkpoint pattern

## Open follow-ups (deferred)

- **Athlete name detection**: only team-sport shape seen so far. When individual sports start producing results, verify `athlete.english_name` extraction (parser is ready, untested).
- **Snooker vs Billiards**: API treats as separate sports — confirm with Luke if KSA enters one or both.
- **Profile match**: port AYG fuzzy_match_profiles.py once we have a KSA athlete master list AND individual entries arrive.
- **Streamlit dashboard**: live medal/schedule view — flag if wanted.
- **Phone access**: see chat — push to private GitHub, use claude.ai/code from mobile.
