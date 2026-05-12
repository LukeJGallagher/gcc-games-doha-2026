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
    ("Athletics", ["decathlon"],          480),   # 2-day combined event, scheduled in chunks
    ("Athletics", ["heptathlon"],         360),
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

    Mutates the rows' 'Time' and 'Time_End' fields in place. Operates on
    a list of schedule-row dicts (keys: Sport, Date, Time, Venue, Discipline,
    Phase, Duration_Min).
    """
    from collections import defaultdict
    buckets: dict = defaultdict(list)
    for r in rows:
        key = (r.get("Date"), r.get("Sport"), r.get("Time"), r.get("Venue"))
        buckets[key].append(r)

    for key, group in buckets.items():
        if len(group) <= 1:
            continue
        # Stagger by stage_name ordinal if available, else by list order.
        # 'Event 1', 'Event 2' → use the trailing number.
        def order_key(r):
            import re as _re
            phase = r.get("Phase", "")
            m = _re.search(r"\d+", phase)
            return int(m.group(0)) if m else 0

        # Only stagger if all events have order info (i.e. Swimming Event N)
        # or all came from same session naming
        if all(order_key(r) > 0 for r in group):
            group_sorted = sorted(group, key=order_key)
        else:
            group_sorted = list(group)
        # Apply cumulative offsets
        start_hhmmss = group_sorted[0].get("Time", "")
        if not start_hhmmss:
            continue
        try:
            from datetime import datetime, timedelta
            t = datetime.strptime(start_hhmmss[:8] if len(start_hhmmss) >= 8 else start_hhmmss + ":00",
                                   "%H:%M:%S")
        except Exception:
            continue
        cursor = t
        for r in group_sorted:
            dur = int(r.get("Duration_Min") or 10)
            new_start = cursor
            new_end   = cursor + timedelta(minutes=dur)
            r["Time"]     = new_start.strftime("%H:%M:%S")
            r["Time_End"] = new_end.strftime("%H:%M:%S")
            cursor = new_end
    return rows


# ---------------------------------------------------------------------------
# Time arithmetic
# ---------------------------------------------------------------------------
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?")


def add_minutes(start_hms: str, minutes: int) -> str:
    """Given a 'HH:MM:SS' or 'HH:MM' string, return start + minutes as 'HH:MM:SS'.
    Returns '' if start is empty/unparseable.
    """
    if not start_hms:
        return ""
    m = _TIME_RE.match(start_hms)
    if not m:
        return ""
    h, mm = int(m.group(1)), int(m.group(2))
    s = int(m.group(3) or 0)
    end = datetime(2000, 1, 1, h, mm, s) + timedelta(minutes=minutes)
    return end.strftime("%H:%M:%S")


def estimate_end_time(sport: str, discipline: str, phase: str, start_hms: str) -> str:
    """One-shot helper: derive Time_End from Time_Start using the duration estimator."""
    return add_minutes(start_hms, estimate_duration_minutes(sport, discipline, phase))
