"""
Approximate session-duration estimator for GCC Games events.

Returns minutes for a (sport, discipline, phase) tuple based on typical
session lengths at regional/continental multisport games. Bias is toward
the FULL SESSION length (not the time the athlete is on the field) so the
Time_End column tells you when to expect the next thing to start.

Source: typical schedules from Asian Games, Asian Youth Games, regional
championships in each sport. Numbers are intentionally rounded.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Per-sport defaults (in minutes)
# ---------------------------------------------------------------------------
DEFAULT_DURATION = {
    # Track & field — most events are scheduled in 2-3h sessions; specific events override
    "Athletics":         120,

    # Pool — typical evening session is 2-2.5h regardless of event count
    "Swimming":          150,

    # Combat — qualification rounds run all day; semis/finals are tighter
    "Boxing":             90,
    "Taekwondo":         120,
    "Karate":             90,
    "Fencing":           180,

    # Bracket / racquet — single match
    "Padel":              90,
    "Table Tennis":       45,
    "Tennis":            120,

    # Team sports — game + warm-up + venue clearance
    "Basketball 3x3":     40,
    "Basketball 5x5":    105,
    "Handball":          110,
    "Volleyball":        105,

    # Precision
    "Archery":           240,   # qualification days are LONG
    "Shooting":           90,
    "Snooker":            90,
    "Billiards":          90,
    "Bowling":           120,

    # Equestrian
    "Equestrian":        180,
}

# ---------------------------------------------------------------------------
# Phase modifiers — multiply the sport default.
# Only applied to SESSION sports (where Final sessions are shorter than Qual).
# For "match" sports (basketball, boxing, taekwondo etc.) each schedule entry
# is one match/bout regardless of phase, so the duration stays constant.
# ---------------------------------------------------------------------------
PHASE_MULT = {
    "Training":       1.5,
    "Qualification":  1.0,
    "Preliminary":    1.0,
    "Round of 64":    0.5,
    "Round of 32":    0.6,
    "Round of 16":    0.7,
    "Quarter Final":  0.6,
    "Semi Final":     0.6,
    "Final":          0.8,
}

# Phase multipliers only apply to these "session" sports.
# Match sports (basketball, handball, padel, boxing, taekwondo, karate, fencing,
# table tennis, volleyball) get a flat duration regardless of phase.
SESSION_SPORTS = {
    "Athletics", "Swimming",
    "Archery", "Shooting",
    "Snooker", "Billiards", "Bowling",
    "Equestrian",
}

# ---------------------------------------------------------------------------
# Per-event overrides (matched on sport + discipline keywords).
# Tuples: (sport, keyword(s) - all must appear), minutes
# Order matters: first match wins.
# ---------------------------------------------------------------------------
EVENT_OVERRIDES: list[tuple[str, list[str], int]] = [
    # Athletics specific
    # Decathlon/Heptathlon: API breaks the combined event into one row per
    # sub-event AND duplicates the row 3-4× per session. So each "Decathlon"
    # row is really one sub-event slot (~40-60 min), not the full 2-day event.
    ("Athletics", ["decathlon"],           45),
    ("Athletics", ["heptathlon"],          45),
    ("Athletics", ["10000"],               40),
    ("Athletics", ["5000"],                25),
    ("Athletics", ["3000m steeplechase"],  20),
    ("Athletics", ["race walk"],           50),
    ("Athletics", ["pole vault"],         180),
    ("Athletics", ["high jump"],          120),
    ("Athletics", ["long jump"],           90),
    ("Athletics", ["triple jump"],         90),
    ("Athletics", ["shot put"],            90),
    ("Athletics", ["discus throw"],        90),
    ("Athletics", ["hammer throw"],       100),
    ("Athletics", ["javelin throw"],       90),
    ("Athletics", ["4x100", "relay"],      40),
    ("Athletics", ["4x400", "relay"],      40),

    # Swimming — API treats each heat as its own row but shares the session
    # start time. Each heat slot is really ~10-15 min; the 150-min session
    # default would overlap every heat together. Per-heat overrides:
    ("Swimming", ["heat"],                 12),
    ("Swimming", ["relay"],                20),
    ("Swimming", ["1500m"],                25),
    ("Swimming", ["800m"],                 18),
    ("Swimming", ["400m"],                 15),
    ("Swimming", ["200m"],                 12),
    ("Swimming", ["100m"],                  8),
    ("Swimming", ["50m"],                   6),

    # Shooting — qualification windows
    ("Shooting", ["qualification", "75 targets"], 120),
    ("Shooting", ["qualification", "rifle"],       90),
    ("Shooting", ["qualification", "pistol"],      90),
    ("Shooting", ["final"],                        60),
    ("Shooting", ["team", "final"],                60),

    # Archery — qualification sessions are very long
    ("Archery", ["qualification"],        360),
    ("Archery", ["training"],             240),
    ("Archery", ["finals"],               180),

    # Snooker / Billiards — match lengths
    ("Snooker", ["singles"],              120),
    ("Snooker", ["team"],                 180),
    ("Snooker", ["6-red"],                 90),
    ("Billiards", ["doubles"],             90),
    ("Billiards", ["singles"],             60),

    # Equestrian
    ("Equestrian", ["team"],              240),
    ("Equestrian", ["individual"],        180),

    # Padel — Round-Robin matches stack inside longer sessions
    ("Padel", ["preliminary"],             75),
]


def _ci_contains_all(haystack: str, needles: list[str]) -> bool:
    h = haystack.lower()
    return all(n.lower() in h for n in needles)


def estimate_duration_minutes(sport: str, discipline: str = "", phase: str = "") -> int:
    """Return approximate session length in minutes for one schedule row."""
    sport = (sport or "").strip()
    discipline = (discipline or "").strip()
    phase = (phase or "").strip()

    # Specific overrides first
    for ov_sport, kws, mins in EVENT_OVERRIDES:
        if sport == ov_sport and _ci_contains_all(discipline + " " + phase, kws):
            return mins

    base = DEFAULT_DURATION.get(sport, 60)
    mult = PHASE_MULT.get(phase, 1.0) if sport in SESSION_SPORTS else 1.0
    # Tighter minimum than the old 15 — swimming heats can be 5 min
    return max(5, int(round(base * mult)))


def stagger_session_events(rows: list[dict]) -> list[dict]:
    """When the API returns multiple events at the same start time + venue
    (session-level scheduling rather than per-event), space them out by
    cumulative duration so the Gantt shows them sequentially.

    Applies ONLY to Swimming-style ordered sessions: same Date+Sport+Time+Venue
    across many different Disciplines, all with numbered "Event N" phases.
    Sorted by phase ordinal and spaced by their per-event durations.

    Same-time same-discipline clusters in match sports (Snooker singles on
    multiple tables, Padel preliminary on parallel courts) are LEGITIMATE
    parallel matches and are left alone — the API correctly reports them.

    Mutates the rows' 'Time' and 'Time_End' fields in place.
    """
    from collections import defaultdict
    import re as _re

    session_buckets: dict = defaultdict(list)
    for r in rows:
        phase = r.get("Phase", "") or ""
        disc  = r.get("Discipline", "") or ""
        sport = r.get("Sport", "") or ""
        # Trigger if Phase looks ordinal ("Event N"), OR Swimming with a
        # "Heat N" pattern in the Discipline title, OR any session sport
        # with multiple distinct disciplines listed at the same start time.
        if (_re.search(r"\bevent\s*\d+\b", phase, _re.I)
                or _re.fullmatch(r"\s*\d+\s*", phase)
                or (sport == "Swimming" and _re.search(r"\bheat\s*\d+\b", disc, _re.I))):
            skey = (r.get("Date"), r.get("Sport"), r.get("Time"), r.get("Venue"))
            session_buckets[skey].append(r)

    for key, group in session_buckets.items():
        if len(group) <= 1:
            continue
        def order_key(r):
            # Order by (Heat ordinal if found, else Phase ordinal, else 0),
            # then by Discipline to keep deterministic ordering.
            phase = r.get("Phase", "") or ""
            disc  = r.get("Discipline", "") or ""
            mh = _re.search(r"\bheat\s*(\d+)\b", disc, _re.I)
            mp = _re.search(r"\d+", phase)
            ord_h = int(mh.group(1)) if mh else 0
            ord_p = int(mp.group(0)) if mp else 0
            return (ord_h or ord_p, disc)
        group_sorted = sorted(group, key=order_key)
        _apply_stagger(group_sorted)
    return rows


def _apply_stagger(group_sorted: list[dict]) -> None:
    """Walk a sorted group of same-time rows and apply cumulative time offsets."""
    if not group_sorted:
        return
    start_hhmmss = group_sorted[0].get("Time", "")
    if not start_hhmmss:
        return
    try:
        t = datetime.strptime(
            start_hhmmss[:8] if len(start_hhmmss) >= 8 else start_hhmmss + ":00",
            "%H:%M:%S",
        )
    except Exception:
        return
    cap = datetime(1900, 1, 1, 23, 59, 0).replace(year=t.year, month=t.month, day=t.day)
    cursor = t
    for r in group_sorted:
        dur = int(r.get("Duration_Min") or 10)
        new_start = min(cursor, cap)
        new_end   = min(cursor + timedelta(minutes=dur), cap)
        r["Time"]     = new_start.strftime("%H:%M:%S")
        r["Time_End"] = new_end.strftime("%H:%M:%S")
        cursor = new_end


# ---------------------------------------------------------------------------
# Time arithmetic
# ---------------------------------------------------------------------------
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?")


def add_minutes(start_hms: str, minutes: int) -> str:
    """Given a 'HH:MM:SS' or 'HH:MM' string, return start + minutes as 'HH:MM:SS'.
    Capped at 23:59:00 — competition sessions never legitimately roll past
    midnight, and a value like '01:00:00' from a 480-min decathlon estimate
    confuses the dashboard Gantt. Returns '' if start is empty/unparseable.
    """
    if not start_hms:
        return ""
    m = _TIME_RE.match(start_hms)
    if not m:
        return ""
    h, mm = int(m.group(1)), int(m.group(2))
    s = int(m.group(3) or 0)
    start = datetime(2000, 1, 1, h, mm, s)
    end = start + timedelta(minutes=minutes)
    cap = datetime(2000, 1, 1, 23, 59, 0)
    if end.day != start.day or end > cap:
        end = cap
    return end.strftime("%H:%M:%S")


def estimate_end_time(sport: str, discipline: str, phase: str, start_hms: str) -> str:
    """One-shot helper: derive Time_End from Time_Start using the duration estimator."""
    return add_minutes(start_hms, estimate_duration_minutes(sport, discipline, phase))
