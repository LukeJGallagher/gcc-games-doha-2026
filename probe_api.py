"""
Systematic GCC API endpoint discovery.

Hits a wide net of Frappe method names + asset paths, records which 200
and what shape they return. Output is a manifest CSV + JSON snapshots
of each successful response for downstream analysis.

Categories probed:
  1. Standard Frappe doctype access
  2. BORNAN method namespaces (athlete, competition, results, record,
     live, noc, discipline, venue, country, event)
  3. Per-competition / per-athlete detail (with real IDs from current data)
  4. Static assets (sport pictograms, NOC flags)

Output:
    data/probe/probe_<ts>.csv          one row per endpoint tested
    data/probe/responses/<safe>.json   full JSON body for each 2xx
"""
from __future__ import annotations

import csv
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from api_client import GccApi
from config import API_BASE, BASE_URL, USER_AGENT, GCC_COUNTRIES, SPORTS


HERE          = Path(__file__).parent
PROBE_DIR     = HERE / "data" / "probe"
RESPONSES_DIR = PROBE_DIR / "responses"
PROBE_DIR.mkdir(parents=True, exist_ok=True)
RESPONSES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Build endpoint list
# ---------------------------------------------------------------------------
def build_endpoint_list():
    """Return list of (label, url) to try."""
    eps: list[tuple[str, str]] = []

    # We'll seed with one of each ID type we already know
    api = GccApi()
    try:
        comps = api.sport_results_summary(sport="Swimming")
    except Exception:
        comps = []
    sample_comp_id = comps[0]["id"] if comps else "COM-2026-0001"

    sample_person_keys = []
    for c in comps[:3]:
        for p in (c.get("participants") or []):
            pk_url = p.get("player_photo") or ""
            m = re.search(r"_(\d{5,9})_photo", pk_url)
            if m:
                sample_person_keys.append(m.group(1))
    sample_person_key = sample_person_keys[0] if sample_person_keys else "15926886"

    # --- A. Frappe doctype access (read-only) ---
    for dt in [
        "Athlete", "Sport", "Competition", "Event", "Match",
        "Schedule", "Medal Tally", "Result", "Country", "Venue",
        "Discipline", "Record", "Heat", "GMS Athlete", "GMS Competition",
    ]:
        eps.append((
            f"frappe.get_list {dt}",
            f"{API_BASE}/frappe.client.get_list?doctype={urllib.parse.quote(dt)}&limit_page_length=3",
        ))

    # --- B. BORNAN gms.api.<area>.<verb> matrix ---
    areas_verbs = {
        "athlete":     ["athletes", "athlete", "get", "detail", "profile", "bio",
                        "list", "by_noc", "by_sport", "by_event"],
        "competition": ["competition", "detail", "get", "info", "participants",
                        "competition_participants", "competition_detail"],
        "results":     ["results", "result", "competition_results", "detail",
                        "by_competition", "by_athlete", "by_country"],
        "record":      ["records", "record", "by_event", "by_athlete", "by_sport",
                        "history", "personal_best"],
        "live":        ["stream", "status", "current", "now", "live"],
        "noc":         ["nocs", "noc", "detail", "summary", "athletes", "roster", "medals"],
        "discipline":  ["disciplines", "list", "events", "by_sport"],
        "venue":       ["venues", "venue", "detail", "list"],
        "country":     ["countries", "country", "athletes", "results"],
        "event":       ["events", "event", "detail", "get", "list"],
        "match":       ["matches", "match", "detail", "get"],
        "schedule":    ["schedule", "all", "by_date"],
        "media":       ["photos", "videos", "highlights"],
    }
    for area, verbs in areas_verbs.items():
        for v in verbs:
            eps.append((
                f"gms.api.{area}.{v}",
                f"{API_BASE}/gms.api.{area}.{v}",
            ))

    # --- C. Parameter-bearing endpoints (with real IDs) ---
    param_eps = [
        ("gms.api.competition.detail?id",        f"gms.api.competition.detail?id={sample_comp_id}"),
        ("gms.api.competition.get?id",           f"gms.api.competition.get?id={sample_comp_id}"),
        ("gms.api.competition.competition?id",   f"gms.api.competition.competition?id={sample_comp_id}"),
        ("gms.api.competition.participants?id",  f"gms.api.competition.participants?id={sample_comp_id}"),
        ("gms.api.results.competition?id",       f"gms.api.results.competition?id={sample_comp_id}"),
        ("gms.api.results.by_competition?id",    f"gms.api.results.by_competition?id={sample_comp_id}"),
        ("gms.api.results.detail?id",            f"gms.api.results.detail?id={sample_comp_id}"),
        ("gms.api.results.results_by_country?noc=KSA",
                                                  "gms.api.results.results_by_country?noc=KSA"),
        ("gms.api.athlete.detail?id",            f"gms.api.athlete.detail?id={sample_person_key}"),
        ("gms.api.athlete.profile?id",           f"gms.api.athlete.profile?id={sample_person_key}"),
        ("gms.api.athlete.bio?id",               f"gms.api.athlete.bio?id={sample_person_key}"),
        ("gms.api.athlete.athletes_by_noc?noc=KSA",
                                                  "gms.api.athlete.athletes_by_noc?noc=KSA"),
        ("gms.api.athlete.athletes_by_sport?sport=Swimming",
                                                  "gms.api.athlete.athletes_by_sport?sport=Swimming"),
        ("gms.api.discipline.events?sport=Swimming",
                                                  "gms.api.discipline.events?sport=Swimming"),
        ("gms.api.discipline.by_sport?sport=Swimming",
                                                  "gms.api.discipline.by_sport?sport=Swimming"),
        ("gms.api.record.by_event",              f"gms.api.record.by_event?event_id={sample_comp_id}"),
        ("gms.api.record.records",               "gms.api.record.records"),
        ("gms.api.noc.detail?noc=KSA",           "gms.api.noc.detail?noc=KSA"),
        ("gms.api.noc.summary?noc=KSA",          "gms.api.noc.summary?noc=KSA"),
        ("gms.api.noc.athletes?noc=KSA",         "gms.api.noc.athletes?noc=KSA"),
        ("gms.api.noc.roster?noc=KSA",           "gms.api.noc.roster?noc=KSA"),
        ("gms.api.country.athletes?noc=KSA",     "gms.api.country.athletes?noc=KSA"),
        ("gms.api.country.results?noc=KSA",      "gms.api.country.results?noc=KSA"),
    ]
    for label, path in param_eps:
        eps.append((label, f"{API_BASE}/{path}"))

    # --- D. Static assets (we already know the pattern) ---
    for noc in GCC_COUNTRIES:
        eps.append((f"flag {noc}", f"{BASE_URL}/files/{noc.lower()}.png"))
        eps.append((f"flag {noc}.svg", f"{BASE_URL}/files/{noc.lower()}.svg"))
    for sport in SPORTS:
        slug = sport.lower().replace(" ", "")
        eps.append((f"pictogram {sport}", f"{BASE_URL}/files/{slug}.svg"))
    # Additional asset guesses
    eps.append(("event logo png", f"{BASE_URL}/files/event-logo.png"))
    eps.append(("event logo svg", f"{BASE_URL}/files/event-logo.svg"))

    return eps


# ---------------------------------------------------------------------------
# Probe one URL
# ---------------------------------------------------------------------------
def _safe_name(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", label)
    return s[:80]


def probe_one(label: str, url: str) -> dict:
    out: dict = {
        "Label": label, "URL": url, "Status": "", "Size_KB": "",
        "Content_Type": "", "JSON_Keys": "", "Notes": "",
    }
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept":     "application/json, image/*;q=0.8, */*;q=0.5",
        })
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read()
        out["Status"]       = str(r.status)
        out["Size_KB"]      = f"{len(body)//1024}"
        out["Content_Type"] = r.headers.get("Content-Type", "")
        if "json" in out["Content_Type"].lower():
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    msg = data.get("message", data)
                    if isinstance(msg, list) and msg and isinstance(msg[0], dict):
                        out["JSON_Keys"] = ",".join(list(msg[0].keys())[:10])
                        out["Notes"]     = f"list of {len(msg)} dicts"
                    elif isinstance(msg, dict):
                        out["JSON_Keys"] = ",".join(list(msg.keys())[:10])
                        out["Notes"]     = "dict"
                    else:
                        out["Notes"] = repr(msg)[:80]
                # Save the response for later inspection
                (RESPONSES_DIR / f"{_safe_name(label)}.json").write_bytes(body[:120000])
            except Exception as e:
                out["Notes"] = f"json parse: {e}"
    except urllib.error.HTTPError as e:
        out["Status"] = str(e.code)
        out["Notes"]  = e.reason
    except Exception as e:
        out["Status"] = "ERR"
        out["Notes"]  = f"{type(e).__name__}: {str(e)[:80]}"
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    endpoints = build_endpoint_list()
    print(f"[PROBE] Testing {len(endpoints)} endpoints ...\n")

    rows: list[dict] = []
    n_ok = 0
    for label, url in endpoints:
        r = probe_one(label, url)
        rows.append(r)
        if r["Status"].startswith("2"):
            n_ok += 1
            print(f"  [OK  {r['Status']}] {label:55s} | {r['Content_Type'][:25]:25s} | {r['Notes'][:50]}")
        # else silent — only print successes to keep output readable

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = PROBE_DIR / f"probe_{ts}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\n[DONE] {n_ok}/{len(endpoints)} endpoints responded successfully")
    print(f"[SAVE] Manifest: {out}")
    print(f"[SAVE] Full JSON responses: {RESPONSES_DIR}/\n")

    # Summary by category
    from collections import Counter
    by_status = Counter(r["Status"] for r in rows)
    print("Status distribution:")
    for s, n in by_status.most_common():
        print(f"  {s:6s} {n}")


if __name__ == "__main__":
    main()
