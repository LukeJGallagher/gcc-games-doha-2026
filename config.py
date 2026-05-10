"""
4th GCC Games Doha 2026 - Scraper Configuration
================================================
Site: https://gccgames.qa/frontend  (React SPA, Frappe backend)
Routes discovered from JS bundle:
  /                          - landing (countdown)
  /medals                    - medal table
  /schedule-competition      - schedule
  /sport/:sportSlug          - sport page (event list)
  /sport/:sportSlug/:eventId - specific event detail
  /match/:id                 - individual match (bracket sports)
API:    /api/method/<frappe_method>     (e.g. /api/method/ping -> pong)
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (laptop-local, no VM/cloud)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent
DATA_DIR     = PROJECT_ROOT / "data"
RESULTS_DIR  = DATA_DIR / "results"
SCHEDULE_DIR = DATA_DIR / "schedule"
RAW_HTML_DIR = DATA_DIR / "raw_html"
LOGS_DIR     = PROJECT_ROOT / "logs"

for d in (RESULTS_DIR, SCHEDULE_DIR, RAW_HTML_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------
BASE_URL = "https://gccgames.qa"
ROUTES = {
    "landing":  "/frontend",
    "medals":   "/frontend/medals",
    "schedule": "/frontend/schedule-competition",
    "sport":    "/frontend/sport/{slug}",
    "event":    "/frontend/sport/{slug}/{event_id}",
    "match":    "/frontend/match/{match_id}",
}
API_BASE = f"{BASE_URL}/api/method"

# ---------------------------------------------------------------------------
# Competition metadata (fixed for all rows)
# ---------------------------------------------------------------------------
COMPETITION_NAME = "4th GCC Games Doha 2026"
COMP_SET         = "GCC Games"
HOST_CITY        = "Doha"
HOST_COUNTRY     = "QAT"

# ---------------------------------------------------------------------------
# GCC nations (confirmed from /api/method/gms.api.medal_standings.medal_standings)
# 6 nations competing - Yemen NOT participating in this edition
# ---------------------------------------------------------------------------
GCC_COUNTRIES = {
    "BRN": "Bahrain",          # NOTE: API uses BRN, not BHR
    "KUW": "Kuwait",
    "OMA": "Oman",
    "QAT": "Qatar",
    "KSA": "Saudi Arabia",
    "UAE": "United Arab Emirates",
}
KSA_CODES = {"KSA", "SAU", "Saudi Arabia", "Saudi"}

# ---------------------------------------------------------------------------
# Sports - fallback list. The scraper pulls the live list from
# /api/method/gms.api.sport.sports each run, so this is only used
# when --offline is set or the API is down.
# Confirmed live on 2026-05-09: 18 sports.
# ---------------------------------------------------------------------------
SPORTS = [
    "Archery", "Athletics", "Basketball 3x3", "Basketball 5x5",
    "Billiards", "Bowling", "Boxing", "Equestrian", "Fencing",
    "Handball", "Karate", "Padel", "Shooting", "Snooker",
    "Swimming", "Table Tennis", "Taekwondo", "Volleyball",
]

# ---------------------------------------------------------------------------
# Output schema (matches export.csv 18-col format)
# ---------------------------------------------------------------------------
RESULTS_COLUMNS = [
    "Athlete", "Sport", "Date", "Competition", "Comp Set", "Class",
    "Discipline", "Discipline_AR", "Phase", "Gender", "Age",
    "Rank", "Result", "Medal", "Wind", "Attempt", "Status",
    "Country", "Detection_Method", "Source_URL",
]

SCHEDULE_COLUMNS = [
    "Date", "Time", "Sport", "Discipline", "Discipline_AR", "Phase", "Gender",
    "Venue", "Country_Entries", "Event_ID", "Source_URL",
]

# ---------------------------------------------------------------------------
# Selenium
# ---------------------------------------------------------------------------
HEADLESS         = True
PAGE_LOAD_WAIT   = 8     # seconds for SPA hydration
RETRY_COUNT      = 2
USER_AGENT       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Team-Saudi-PA"
