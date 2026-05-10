"""
Frappe JSON API client for gccgames.qa.

Endpoints discovered from live network capture (2026-05-09):
    sports                 - list of all sports
    competition_dates      - list of competition days
    competition_by_date    - per-day sport summary (counts only)
    grid_competitions      - full schedule grid (all sports, all days)
    sport_results_summary  - per-sport full competition list incl. participants + results
    medal_standings        - medal table

No auth required. All responses are JSON wrapped in {"message": ...}.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Any

from config import API_BASE, USER_AGENT

log = logging.getLogger("gcc.api")


class GccApi:
    def __init__(self, timeout: int = 25, retries: int = 2, retry_wait: float = 2.0):
        self.timeout = timeout
        self.retries = retries
        self.retry_wait = retry_wait

    def _call(self, method: str, **params) -> Any:
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        url = f"{API_BASE}/{method}{qs}"
        last_err: Exception | None = None
        for attempt in range(1, self.retries + 2):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                })
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read().decode("utf-8", "ignore")
                data = json.loads(body)
                return data.get("message", data)
            except Exception as e:
                last_err = e
                log.warning("API %s attempt %d failed: %s", method, attempt, e)
                if attempt <= self.retries:
                    time.sleep(self.retry_wait)
        raise RuntimeError(f"API {method} failed after {self.retries+1} attempts: {last_err}")

    # -------- public methods ------------------------------------------------
    def sports(self) -> list[dict]:
        return self._call("gms.api.sport.sports")

    def competition_dates(self) -> list[str]:
        return self._call("gms.api.competition.competition_dates")

    def competition_by_date(self, date: str) -> list[dict]:
        return self._call("gms.api.competition.competition_by_date", date=date)

    def grid_competitions(self) -> list[dict]:
        return self._call("gms.api.competition.grid_competitions")

    def sport_results_summary(self, sport: str | None = None) -> list[dict]:
        if sport:
            return self._call("gms.api.results.sport_results_summary", sport=sport)
        return self._call("gms.api.results.sport_results_summary")

    def medal_standings(self) -> list[dict]:
        return self._call("gms.api.medal_standings.medal_standings")
