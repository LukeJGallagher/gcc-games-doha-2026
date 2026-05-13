"""
GCC Games Doha 2026 - Team Saudi competition dashboard.

Multi-tab Streamlit app:
  1. Overview          - medal table, athletes, schedule heatmap
  2. PA Coverage Plan  - Gantt-based coverage planning for Luke/Alanoud
  3. Fix List          - athletes needing manual data reconciliation

Run locally:
    streamlit run dashboard.py

Deploy:
    Push to Streamlit Cloud, point at dashboard.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# python-pptx is optional - dashboard still works if it isn't installed
try:
    from ppt_export import build_pptx
    PPTX_OK = True
except Exception:
    build_pptx = None
    PPTX_OK = False

# ---------------------------------------------------------------------------
# Team Saudi palette
# ---------------------------------------------------------------------------
ELITE      = "#235036"   # primary
ENABLER    = "#69c399"   # accent
DISCIPLINE = "#18342a"   # darkest
STAMINA    = "#c3d9d1"   # light
VICTORY    = "#ebce83"   # gold
LAVENDER   = "#9263aa"   # secondary
MALE_COL   = ELITE
FEMALE_COL = LAVENDER   # Team Saudi secondary — no pink

SPORT_COLOURS = {
    "Athletics": ELITE,
    "Swimming":  "#2a76b8",
    "Taekwondo": LAVENDER,
    "Karate":    VICTORY,
}

# Phase importance for conflict-resolution priority (higher = more important)
PHASE_PRIORITY = {
    "final":         100,
    "gold medal":    100,
    "bronze medal":   95,
    "semi final":     80,
    "semifinal":      80,
    "quarter final":  60,
    "quarterfinal":   60,
    "round of 16":    50,
    "round of 32":    40,
    "round of 64":    30,
    "preliminary":    25,
    "qualification":  20,
    "group stage":    20,
    "knockout":       70,
    "training":        5,
}

TARGET_SPORT_BONUS = {"Athletics": 10, "Swimming": 10, "Taekwondo": 10, "Karate": 10}


MEDAL_COLOURS = {
    "G": "#d4af37",  # gold
    "S": "#c0c0c0",  # silver
    "B": "#cd7f32",  # bronze
}
MEDAL_NAMES = {"G": "GOLD", "S": "SILVER", "B": "BRONZE"}


def athlete_photo_path(person_key: str | None) -> Path | None:
    if not person_key:
        return None
    p = PHOTOS_DIR / f"{person_key}.jpg"
    return p if p.exists() else None


def initials_svg(name: str, bg: str = ELITE) -> str:
    """Return inline SVG showing initials in a coloured circle (data URI)."""
    parts = [p for p in (name or "").split() if p]
    initials = "".join(p[0].upper() for p in parts[:2]) or "??"
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<rect width="100" height="100" rx="50" fill="{bg}"/>'
        f'<text x="50" y="60" font-size="40" font-family="Arial" fill="white" '
        f'text-anchor="middle" font-weight="700">{initials}</text>'
        f'</svg>'
    )
    import base64
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def lookup_person_key(athlete_full: str) -> str:
    """Find Person_Key from athlete-schedule by full name."""
    if sched_df.empty: return ""
    parts = (athlete_full or "").split()
    if len(parts) < 2: return ""
    mask = (sched_df["Given Name"].str.lower() == parts[0].lower()) & \
           (sched_df["Family Name"].str.lower() == " ".join(parts[1:]).lower())
    rows = sched_df[mask]
    if rows.empty: return ""
    return str(rows.iloc[0].get("Person_Key", "") or "")


def event_priority(row) -> int:
    """Priority score for conflict resolution: higher = keep, lower = drop."""
    score = 0
    if str(row.get("SOTC", "")).upper() == "YES":
        score += 50
    phase = str(row.get("Phase", "")).lower().strip()
    score += PHASE_PRIORITY.get(phase, 0)
    score += TARGET_SPORT_BONUS.get(row.get("Sport", ""), 0)
    return score


def group_into_sessions(df: pd.DataFrame, gap_min: int = 30) -> pd.DataFrame:
    """Group events at the same (Date, Sport, Venue) into sessions.

    If your crew is already parked at the pool covering one swim event, every
    other swim event in the same session at that venue is "free" — same camera,
    no extra cost. So for camera planning we count each session as ONE slot,
    not one slot per event.

    A session = events at same Date+Sport+Venue where each event starts
    within `gap_min` minutes of the previous one ending (so contiguous or
    near-contiguous events merge into one session).

    Returns a session-level dataframe with columns:
        Date, Sport, Venue, TS (earliest start), TE (latest end),
        Priority (max), Athletes (semicolon-list), ev_indices (list)
    """
    sessions = []
    if df.empty: return pd.DataFrame()
    for (date, sport, venue), g in df.groupby(["Date", "Sport", "Venue"], dropna=False):
        g = g.sort_values("TS")
        current = None
        for _, ev in g.iterrows():
            if current is None:
                current = {
                    "Date": date, "Sport": sport, "Venue": venue,
                    "TS": ev["TS"], "TE": ev["TE"],
                    "Priority": ev["Priority"],
                    "Athletes": [str(ev.get("Athlete", ""))],
                    "ev_indices": [ev.name],
                }
            elif pd.notna(ev["TS"]) and pd.notna(current["TE"]) and \
                 ev["TS"] <= current["TE"] + pd.Timedelta(minutes=gap_min):
                # Extend the current session
                current["TE"]       = max(current["TE"], ev["TE"])
                current["Priority"] = max(current["Priority"], ev["Priority"])
                current["Athletes"].append(str(ev.get("Athlete", "")))
                current["ev_indices"].append(ev.name)
            else:
                sessions.append(current)
                current = {
                    "Date": date, "Sport": sport, "Venue": venue,
                    "TS": ev["TS"], "TE": ev["TE"],
                    "Priority": ev["Priority"],
                    "Athletes": [str(ev.get("Athlete", ""))],
                    "ev_indices": [ev.name],
                }
        if current:
            sessions.append(current)
    out = pd.DataFrame(sessions)
    if not out.empty:
        out["Athletes"] = out["Athletes"].apply(lambda lst: "; ".join(sorted(set(lst))))
    return out


def allocate_cameras_by_session(events_df: pd.DataFrame, cams_available: int) -> pd.Series:
    """Allocate cameras per session, then propagate the camera number to all
    events within each session. Each event row gets a Camera value (0 = UNCOVERED)."""
    if events_df.empty:
        return pd.Series(dtype=int)
    sessions = group_into_sessions(events_df)
    sessions["__row_id"] = range(len(sessions))
    # The allocator works on a generic dataframe with TS/TE/Priority and a unique index
    sessions_idx = sessions.set_index("__row_id")
    session_cams = allocate_cameras(sessions_idx, cams_available)
    # Map back: each event in session i gets session i's camera
    out: dict = {}
    for sid, row in sessions.iterrows():
        cam = int(session_cams.get(row["__row_id"], 0))
        for ev_idx in row["ev_indices"]:
            out[ev_idx] = cam
    return pd.Series(out)


def allocate_cameras(events_df, cams_available: int) -> pd.Series:
    """Time-ordered, load-balanced camera allocator with priority bumping.

    1. Sort events by start time.
    2. Free any camera whose booking has ended.
    3. If any camera is free, take the one with the LEAST total time used so far
       (so 2-operator days spread load across Luke + Alanoud rather than dumping
       everything on Cam 1).
    4. If all cameras are busy, bump the lowest-priority running event to
       UNCOVERED only if the new event is higher priority. Otherwise UNCOVERED.

    Returns: pd.Series of camera assignments (0 = UNCOVERED) indexed by row index.
    """
    load = {cam: pd.Timedelta(0) for cam in range(1, cams_available + 1)}
    assignments: dict = {}
    active: list = []  # (cam_id, end_time, ev_index, priority)
    for _, ev in events_df.sort_values("TS").iterrows():
        # Expire ended bookings
        active = [a for a in active if a[1] > ev["TS"]]
        used = {a[0] for a in active}
        free = [c for c in range(1, cams_available + 1) if c not in used]
        duration = ev["TE"] - ev["TS"]

        if free:
            # Pick the free camera with the least load so far (tie → lowest id)
            cam = min(free, key=lambda c: (load[c], c))
            assignments[ev.name] = cam
            load[cam] += duration
            active.append((cam, ev["TE"], ev.name, ev["Priority"]))
        else:
            lowest = min(active, key=lambda a: a[3])
            if lowest[3] < ev["Priority"]:
                assignments[lowest[2]] = 0
                active.remove(lowest)
                cam = lowest[0]
                assignments[ev.name] = cam
                load[cam] += duration
                active.append((cam, ev["TE"], ev.name, ev["Priority"]))
            else:
                assignments[ev.name] = 0
    return pd.Series(assignments)

HERE         = Path(__file__).parent
DATA         = HERE / "data"
RESULTS_DIR  = DATA / "results"
SCHEDULE_DIR = DATA / "schedule"
PHOTOS_DIR   = HERE / "photos"
ASSETS_DIR   = HERE / "assets"

st.set_page_config(
    page_title="GCC Games Doha 2026 — Team Saudi",
    page_icon="🇸🇦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
# Team Saudi gradients (matching the Jiu-Jitsu dashboard style)
HEADER_GRADIENT = f"linear-gradient(135deg, {ELITE} 0%, {DISCIPLINE} 100%)"
GOLD_BAR        = f"linear-gradient(90deg, {ELITE} 0%, {VICTORY} 50%, {DISCIPLINE} 100%)"

st.markdown(f"""
<style>
.block-container {{padding-top: 1rem; padding-bottom: 1rem; max-width: 1600px;}}
h1, h2, h3 {{color: {DISCIPLINE};}}

/* Header banner: gradient + gold accent stripe */
.header-bar {{
    background: {HEADER_GRADIENT};
    color: white;
    padding: 1.2rem 1.5rem;
    border-radius: 12px;
    margin-bottom: 1rem;
    box-shadow: 0 8px 25px rgba(35, 80, 54, 0.25);
    position: relative; overflow: hidden;
}}
.header-bar::after {{
    content:""; position:absolute; bottom:0; left:0; right:0; height:4px;
    background: {GOLD_BAR};
}}
.header-bar h1 {{color: white; margin: 0; font-size: 1.7rem;}}
.header-bar .subline {{color: {VICTORY}; font-size: 0.9rem; margin-top: 0.25rem;}}

/* Metric cards */
.metric-card {{
    background: white; padding: 0.85rem 1rem; border-radius: 8px;
    border-left: 4px solid {ENABLER};
    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
}}
.metric-card .label {{font-size: 0.78rem; color: #555; text-transform: uppercase; letter-spacing: 0.5px;}}
.metric-card .value {{font-size: 1.55rem; color: {DISCIPLINE}; font-weight: 700; margin-top: 0.15rem;}}

/* Tabs styling */
[data-baseweb="tab"] {{
    font-weight: 600;
    color: {DISCIPLINE} !important;
}}
[data-baseweb="tab"][aria-selected="true"] {{
    color: {ELITE} !important;
    border-bottom: 3px solid {VICTORY} !important;
}}

/* Primary buttons in Team Saudi green */
.stButton > button, .stDownloadButton > button {{
    background: {ELITE}; color: white; font-weight: 600;
    border: none; border-radius: 6px;
    padding: 0.45rem 1.1rem; transition: all 0.18s ease;
    box-shadow: 0 2px 4px rgba(35, 80, 54, 0.2);
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
    background: {DISCIPLINE}; color: {VICTORY};
    transform: translateY(-1px);
    box-shadow: 0 4px 10px rgba(35, 80, 54, 0.32);
}}

/* Radio pills (date picker) */
div[role="radiogroup"] > label {{
    background: {STAMINA}; color: {DISCIPLINE};
    padding: 0.35rem 0.85rem; margin-right: 0.4rem !important;
    border-radius: 16px; font-weight: 500; cursor: pointer;
    transition: all 0.15s ease;
}}
div[role="radiogroup"] > label:has(input:checked) {{
    background: {ELITE}; color: white;
}}
div[role="radiogroup"] > label > div:first-child {{ display: none; }}  /* hide radio bullet */

/* Subheader accent stripe */
h3 {{
    border-left: 4px solid {VICTORY};
    padding-left: 0.6rem;
}}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _latest(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern))
    return files[-1] if files else None


@st.cache_data(ttl=60)
def load_athlete_schedule() -> pd.DataFrame:
    f = _latest(RESULTS_DIR, "KSA_ATHLETE_SCHEDULE_*.csv")
    if not f: return pd.DataFrame()
    df = pd.read_csv(f, encoding="utf-8-sig", dtype=str).fillna("")
    df["Athlete"]      = (df["Given Name"] + " " + df["Family Name"]).str.strip()
    df["Date"]         = pd.to_datetime(df["Date"], errors="coerce")
    df["Duration_Min"] = pd.to_numeric(df.get("Duration_Min", 0), errors="coerce").fillna(60).astype(int)
    return df


@st.cache_data(ttl=60)
def load_medals() -> pd.DataFrame:
    f = _latest(RESULTS_DIR, "MEDALS_*.csv")
    if not f: return pd.DataFrame()
    return pd.read_csv(f, encoding="utf-8-sig")


@st.cache_data(ttl=60)
def load_medal_diff() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (latest, previous) medal tables for delta comparison."""
    files = sorted(RESULTS_DIR.glob("MEDALS_*.csv"))
    if len(files) < 2:
        return load_medals(), pd.DataFrame()
    latest = pd.read_csv(files[-1], encoding="utf-8-sig")
    prev   = pd.read_csv(files[-2], encoding="utf-8-sig")
    return latest, prev


def detect_medal_changes() -> list[dict]:
    """Return list of {NOC, Country, before, after, delta} for any nation
    whose Gold/Silver/Bronze totals changed between the two latest pulls."""
    latest, prev = load_medal_diff()
    if prev.empty or latest.empty:
        return []
    out = []
    for _, row in latest.iterrows():
        noc = row["NOC"]
        prev_row = prev[prev["NOC"] == noc]
        if prev_row.empty:
            continue
        p = prev_row.iloc[0]
        for col in ("Gold", "Silver", "Bronze"):
            delta = int(row[col]) - int(p[col])
            if delta != 0:
                out.append({
                    "NOC":     noc,
                    "Country": row["Country"],
                    "Medal":   col,
                    "Before":  int(p[col]),
                    "After":   int(row[col]),
                    "Delta":   delta,
                })
    return out


@st.cache_data(ttl=60)
def load_results_ksa() -> pd.DataFrame:
    f = _latest(RESULTS_DIR, "RESULTS_KSA_*.csv")
    if not f: return pd.DataFrame()
    df = pd.read_csv(f, encoding="utf-8-sig", dtype=str).fillna("")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df


@st.cache_data(ttl=60)
def load_results_all() -> pd.DataFrame:
    """All-NOC result rows — needed to compute Win/Loss scores for matches
    where we want to show KSA-vs-opponent (e.g. '22-16' for basketball)."""
    f = _latest(RESULTS_DIR, "RESULTS_ALL_*.csv")
    if not f: return pd.DataFrame()
    df = pd.read_csv(f, encoding="utf-8-sig", dtype=str).fillna("")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df


@st.cache_data(ttl=3600)
def load_history_medal_table() -> pd.DataFrame:
    f = HERE / "data" / "history" / "gcc_2022_medal_table.csv"
    if not f.exists(): return pd.DataFrame()
    return pd.read_csv(f)


@st.cache_data(ttl=3600)
def load_history_ksa_sport() -> pd.DataFrame:
    f = HERE / "data" / "history" / "gcc_2022_ksa_by_sport.csv"
    if not f.exists(): return pd.DataFrame()
    return pd.read_csv(f)


@st.cache_data(ttl=600)
def load_venues() -> dict:
    """Venue → {lat, lon, district} from venues.json."""
    import json
    f = HERE / "venues.json"
    if not f.exists():
        return {}
    raw = json.loads(f.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


@st.cache_data(ttl=60)
def load_shortlist_raw() -> pd.DataFrame:
    """Master roster from BORNAN-derived Shortlist xlsx (athlete + SOTC + manual times)."""
    f = next(iter(HERE.glob("Athletes Details*.xlsx")), None)
    if not f: return pd.DataFrame()
    df = pd.read_excel(f, sheet_name="Shortlist")
    df["Date"]       = pd.to_datetime(df["Date"], errors="coerce")
    df["Time Start"] = df["Time Start"].astype(str)
    df["Time End"]   = df["Time End"].astype(str)
    return df


def file_age(folder: Path, pattern: str) -> str:
    f = _latest(folder, pattern)
    if not f: return "—"
    ts = datetime.fromtimestamp(f.stat().st_mtime)
    delta = datetime.now() - ts
    mins = int(delta.total_seconds() / 60)
    if mins < 60:    return f"{mins} min ago"
    if mins < 1440:  return f"{mins // 60} hr ago"
    return f"{mins // 1440} d ago"


# ISG-style phase colours shared by Daily Plan + PA Coverage Plan
PHASE_COLOURS = {
    "Final":         VICTORY,
    "Semi Final":    ENABLER,
    "Quarter Final": "#76b6d8",
    "Qualification": STAMINA,
    "Preliminary":   STAMINA,
    "Heats":         STAMINA,
    "Heat":          STAMINA,
    "Group Stage":   ELITE,
    "Group":         ELITE,
    "Round of 16":   LAVENDER,
    "Round of 32":   LAVENDER,
    "Round of 64":   LAVENDER,
    "Knockout":      LAVENDER,
    "Training":      "#cccccc",
}

ISG_CSS = f"""
<style>
.isg-schedule {{ border-collapse:collapse; width:100%; font-size:0.85rem; font-family:inherit; }}
.isg-schedule th {{ background:#f5f5f5; text-align:left; padding:6px 10px; font-weight:600;
                    border-bottom:2px solid #ddd; color:{DISCIPLINE}; }}
.isg-schedule td {{ padding:5px 10px; vertical-align:middle; border-bottom:1px solid #eee; }}
.athlete-cell {{ font-weight:600; color:{DISCIPLINE}; }}
.sotc-cell    {{ color:{ENABLER}; font-size:0.75rem; font-weight:700; }}
.time-cell    {{ color:#555; font-variant-numeric:tabular-nums; }}
.cam-cell     {{ font-weight:600; color:{ELITE}; }}
.cam-cell.uncov {{ color:#c53030; }}
.bar-cell {{ width:45%; }}
.bar-track {{ position:relative; width:100%; height:14px; background:#fafafa; border-radius:3px; }}
.bar-fill  {{ position:absolute; top:0; bottom:0; border-radius:3px; }}
.bar-axis  {{ position:relative; width:100%; height:18px; color:#666; font-size:0.7rem; }}
.bar-tick  {{ position:absolute; transform:translateX(-50%); top:0; }}
.axis-row td {{ border-bottom:none; padding-top:0; }}
</style>
"""


def build_daily_gantt_fig(df: pd.DataFrame, title: str = ""):
    """Plotly Gantt chart of one day's schedule, Sport+Athlete rows coloured by Phase.

    Mirrors the ISG-style HTML table for PPT export (kaleido renders to PNG).
    Collapses team-sport rows to 'KSA TEAM (vs OPP)' for consistency with the
    on-screen table.
    """
    if df.empty:
        return None
    work = df.copy()
    is_team = work["Match_Type"].astype(str).str.lower() == "team"
    if is_team.any():
        team_rows = work[is_team].drop_duplicates(
            subset=["Sport", "Event_ID", "Phase", "Date"], keep="first").copy()
        team_rows["Athlete"] = team_rows.apply(
            lambda r: f"KSA TEAM (vs {r['Opponent']})" if r.get("Opponent") else "KSA TEAM",
            axis=1)
        team_rows["Family Name"] = "Team"; team_rows["Given Name"] = "KSA"
        work = pd.concat([work[~is_team], team_rows], ignore_index=True)
    work = work.sort_values(["Sport", "Family Name", "Given Name", "TS"])
    work["Row"]   = work["Sport"] + " · " + work["Athlete"]
    work["Label"] = work["Event"] + " (" + work["Phase"] + ")"

    fig = px.timeline(
        work, x_start="TS", x_end="TE", y="Row",
        color="Phase",
        color_discrete_map=PHASE_COLOURS,
        text="Label",
        hover_data={"Athlete": True, "Phase": True, "Venue": True,
                    "Time Start": True, "Time End": True,
                    "TS": False, "TE": False, "Row": False},
    )
    fig.update_yaxes(autorange="reversed", title="", tickfont=dict(size=11))
    fig.update_xaxes(title="", tickformat="%H:%M", dtick=2*60*60*1000)
    fig.update_traces(textposition="inside", textfont_size=10, insidetextanchor="start")
    fig.update_layout(
        title=title or None,
        height=max(360, 24 * work["Row"].nunique() + 80),
        margin=dict(t=40 if title else 10, b=10, l=10, r=10),
        plot_bgcolor="white",
        legend=dict(orientation="h", y=1.06, yanchor="bottom"),
    )
    return fig


def render_isg_schedule(df: pd.DataFrame, include_camera: bool = False,
                        title: str | None = None):
    """Render an ISG-style HTML schedule for one day's events.

    Team-sport rows (Match_Type == 'team') are collapsed: one row per match,
    labelled 'KSA Team', so the 16-athlete handball squad doesn't fill the
    schedule with 16 identical rows per match.

    df expected to have: Sport, Family Name, Given Name, Athlete, Gender,
    SOTC, Phase, Event, Time Start, Time End, TS, TE, Match_Type, Opponent,
    Venue. If include_camera=True, also expects a 'Camera' column.
    """
    if df.empty:
        return

    df = df.copy()
    is_team = df["Match_Type"].astype(str).str.lower() == "team"
    if is_team.any():
        team_rows  = df[is_team].drop_duplicates(
            subset=["Sport", "Event_ID", "Phase", "Date"], keep="first").copy()
        team_rows["Athlete"]     = team_rows.apply(
            lambda r: f"KSA TEAM (vs {r['Opponent']})" if r.get("Opponent") else "KSA TEAM", axis=1)
        team_rows["Given Name"]  = "KSA"
        team_rows["Family Name"] = "Team"
        team_rows["Gender"]      = team_rows["Gender"].fillna("").replace("", "Team")
        df = pd.concat([df[~is_team], team_rows], ignore_index=True)

    ath_view = df.sort_values(["Sport","Family Name","Given Name","TS"]).copy()
    day_min = ath_view["TS"].min(); day_max = ath_view["TE"].max()
    start_h = max(7,  int(day_min.hour))
    end_h   = min(23, int(day_max.hour) + (1 if day_max.minute else 0))
    if end_h - start_h < 6: end_h = start_h + 6
    total_h = end_h - start_h

    cols = ["Sport","Athlete","Gender","SOTC","Phase","Event","Start","End"]
    if include_camera:
        cols.append("Camera")
    cols.append("Schedule")
    rows_html = ['<table class="isg-schedule"><thead><tr>']
    for c in cols:
        rows_html.append(f'<th>{c}</th>')
    rows_html.append('</tr></thead><tbody>')

    prev_sport, prev_ath = None, None
    for _, r in ath_view.iterrows():
        ts, te = r["TS"], r["TE"]
        if pd.isna(ts) or pd.isna(te): continue
        ts_h = ts.hour + ts.minute/60
        te_h = te.hour + te.minute/60
        left  = max(0, min(100, (ts_h - start_h) / total_h * 100))
        width = max(0.8, (te_h - ts_h) / total_h * 100)
        colour = PHASE_COLOURS.get(str(r["Phase"]).strip(), "#888")

        sport_disp = r["Sport"] if r["Sport"] != prev_sport else ""
        ath_name = r["Athlete"] or f"{r['Given Name']} {r['Family Name']}".strip()
        ath_disp = ath_name if (r["Sport"] != prev_sport or ath_name != prev_ath) else ""
        ath_disp = ath_disp.upper()
        # ⭐ for ISG 2025 medallists — spot them at a glance
        isg_medals = str(r.get("ISG_2025_Medals", "")).strip()
        if ath_disp and isg_medals:
            ath_disp = f"⭐ {ath_disp}"

        # Cancellation / postponement override — strikethrough + red flag
        is_cancelled = str(r.get("Status", "")).lower() in ("cancelled", "canceled")
        is_postponed = str(r.get("Status", "")).lower() == "postponed"
        row_style    = ""
        if is_cancelled:
            row_style = "opacity:0.55;text-decoration:line-through;color:#c53030;"
        elif is_postponed:
            row_style = "color:#d97706;font-style:italic;"
        event_disp = r["Event"]
        if r.get("Match_Type") == "team" and r.get("Opponent"):
            event_disp = f"{r['Event']} (KSA vs {r['Opponent']})"
        sotc_disp = "SOTC" if str(r["SOTC"]).upper() == "YES" else ""
        bar_html = (
            f'<div class="bar-track">'
            f'<div class="bar-fill" style="left:{left:.1f}%;width:{width:.1f}%;background:{colour};" '
            f'title="{r["Phase"]} · {event_disp} · {r.get("Venue","")}"></div></div>'
        )
        cam_html = ""
        if include_camera:
            cam_val = r.get("Camera", 0)
            cam_class = "cam-cell uncov" if cam_val == 0 else "cam-cell"
            cam_text  = "UNCOVERED" if cam_val == 0 else f"Cam {int(cam_val)}"
            cam_html = f'<td class="{cam_class}">{cam_text}</td>'

        border_style = "border-top:2px solid #ccc;" if sport_disp else ""
        if row_style:
            border_style = f"{border_style}{row_style}"
        rows_html.append(
            f'<tr style="{border_style}">'
            f'<td><b>{sport_disp}</b></td>'
            f'<td class="athlete-cell">{ath_disp}</td>'
            f'<td>{r.get("Gender","")}</td>'
            f'<td class="sotc-cell">{sotc_disp}</td>'
            f'<td>{r["Phase"]}</td>'
            f'<td>{event_disp}</td>'
            f'<td class="time-cell">{fmt_time(r["Time Start"])}</td>'
            f'<td class="time-cell">{fmt_time(r["Time End"])}</td>'
            f'{cam_html}'
            f'<td class="bar-cell">{bar_html}</td>'
            f'</tr>'
        )
        prev_sport, prev_ath = r["Sport"], ath_name

    # axis row
    n_pre = len(cols) - 1   # cols before the Schedule column
    hour_ticks = list(range(start_h, end_h + 1, 2))
    tick_html = '<div class="bar-axis">'
    for h in hour_ticks:
        pos = (h - start_h) / total_h * 100
        tick_html += f'<span class="bar-tick" style="left:{pos:.1f}%;">{h:02d}:00</span>'
    tick_html += '</div>'
    rows_html.append(f'<tr class="axis-row"><td colspan="{n_pre}"></td>'
                     f'<td class="bar-cell">{tick_html}</td></tr>')
    rows_html.append("</tbody></table>")

    if title:
        st.markdown(f"#### {title}")
    st.markdown(ISG_CSS + "".join(rows_html), unsafe_allow_html=True)


def csv_download_button(label: str, df: pd.DataFrame, key: str):
    """Render a Streamlit download button that emits a CSV of `df`."""
    if df is None or df.empty:
        return
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        f"⬇ Download {label} CSV", csv,
        file_name=f"GCC2026_{label.replace(' ','_')}_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
        key=key,
    )


def ppt_download_button(label: str, deck_title: str, sections: list,
                        subtitle: str = "", key: str = "ppt_dl"):
    """Render a Streamlit download button that emits a PPT built from `sections`."""
    if not PPTX_OK:
        st.caption(f"📥 PPT export for **{label}** unavailable — `python-pptx` not installed yet.")
        return
    logo = ASSETS_DIR / "ts_horizontal.png"
    if st.button(f"📥 Export {label} to PowerPoint", key=key + "_btn"):
        try:
            data = build_pptx(deck_title, sections, subtitle=subtitle,
                              logo_path=logo if logo.exists() else None)
            st.download_button(
                "⬇ Download PPT", data,
                file_name=f"GCC2026_{label.replace(' ','_')}_{datetime.now():%Y%m%d_%H%M}.pptx",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                key=key + "_dl",
            )
        except Exception as e:
            st.error(f"PPT build failed: {e}")


def fmt_time(t: str | None) -> str:
    """Normalise '9:30:00' / '09:30:00' -> '09:30'. Tolerates blanks."""
    if not t:
        return ""
    s = str(t).strip()
    parts = s.split(":")
    if len(parts) >= 2:
        h, m = parts[0].zfill(2), parts[1][:2]
        return f"{h}:{m}"
    return s[:5]


def _pad_time(t):
    """Normalise time strings to 'HH:MM:SS' so mixed input formats parse uniformly.
    Some rows arrive as 'HH:MM' (manual overrides), others as 'HH:MM:SS' (API).
    Mixed columns cause pandas to lock the wrong format and emit NaT on misses."""
    if t is None or (isinstance(t, float) and pd.isna(t)):
        return ""
    s = str(t).strip()
    if not s:
        return ""
    parts = s.split(":")
    if len(parts) == 2:
        return f"{parts[0].zfill(2)}:{parts[1][:2]}:00"
    if len(parts) >= 3:
        return f"{parts[0].zfill(2)}:{parts[1][:2]}:{parts[2][:2]}"
    return s


def fmt_date(d) -> str:
    """Render any date-like value as 'Mon 12 May'."""
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return ""
    try:
        return pd.Timestamp(d).strftime("%a %d %b")
    except Exception:
        return str(d)[:10]


def gender_from_event(event: str) -> str:
    e = (event or "").lower()
    if "women" in e:    return "Female"
    if "mixed" in e:    return "Mixed"
    if "men"   in e:    return "Male"
    return "Mixed"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
sched_df      = load_athlete_schedule()
medals_df     = load_medals()
results_df    = load_results_ksa()
shortlist_raw = load_shortlist_raw()

ksa_medals  = medals_df[medals_df["NOC"] == "KSA"].iloc[0] if not medals_df.empty else None
gold        = int(ksa_medals["Gold"])   if ksa_medals is not None else 0
silver      = int(ksa_medals["Silver"]) if ksa_medals is not None else 0
bronze      = int(ksa_medals["Bronze"]) if ksa_medals is not None else 0
ksa_rank    = int(ksa_medals["Rank"])   if ksa_medals is not None else "—"
today       = pd.Timestamp.today().normalize()
today_events = sched_df[sched_df["Date"] == today] if not sched_df.empty else pd.DataFrame()

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def _logo_b64() -> str:
    import base64
    p = ASSETS_DIR / "ts_horizontal.png"
    if p.exists():
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    return ""


_logo = _logo_b64()
_logo_html = (
    f'<img src="{_logo}" style="height:60px;margin-right:1.2rem;">' if _logo else "🇸🇦"
)
st.markdown(f"""
<div class="header-bar" style="display:flex;align-items:center;gap:1.2rem;">
  {_logo_html}
  <div style="flex:1;">
    <h1>GCC Games Doha 2026</h1>
    <div class="subline">Team Saudi · Performance Analysis · Last refresh: {file_age(RESULTS_DIR, 'KSA_ATHLETE_SCHEDULE_*.csv')}</div>
  </div>
</div>
""", unsafe_allow_html=True)

tab_overview, tab_summary, tab_medals, tab_daily, tab_plan, tab_history, tab_audit, tab_fix = st.tabs([
    "📊 Overview", "📰 Day Summary", "🏅 Medal Report",
    "📆 Daily Plan", "📅 PA Coverage Plan",
    "📈 vs 2022", "🔍 Audit", "🛠 Fix List",
])


# ---------------------------------------------------------------------------
# Medal change alert (across all KSA scrapes today)
# ---------------------------------------------------------------------------
medal_changes = detect_medal_changes()
ksa_changes   = [c for c in medal_changes if c["NOC"] == "KSA" and c["Delta"] > 0]
if ksa_changes:
    for ch in ksa_changes:
        st.toast(f"🇸🇦 New {ch['Medal']} medal for KSA! ({ch['Before']} → {ch['After']})", icon="🎉")
    msg = " · ".join(f"+{c['Delta']} {c['Medal']}" for c in ksa_changes)
    st.markdown(f"""
    <div style="background:{VICTORY};color:{DISCIPLINE};padding:1rem 1.5rem;border-radius:6px;
                margin-bottom:1rem;font-weight:700;font-size:1.2rem;border-left:6px solid #d4af37;">
        🏅 NEW MEDAL{('S' if len(ksa_changes)>1 else '')} for Team Saudi: {msg}
    </div>
    """, unsafe_allow_html=True)


def render_medal_card(row) -> str:
    """Return inline HTML for one medal moment card."""
    medal       = str(row.get("Medal", "")).strip().upper()[:1]
    colour      = MEDAL_COLOURS.get(medal, ELITE)
    medal_name  = MEDAL_NAMES.get(medal, "")
    athlete     = str(row.get("Athlete", ""))
    sport       = str(row.get("Sport", ""))
    event       = str(row.get("Discipline", "")) or str(row.get("Event", ""))
    phase       = str(row.get("Phase", ""))
    result      = str(row.get("Result", "")).strip() or "—"
    date_obj    = row.get("Date")
    date_str    = pd.Timestamp(date_obj).strftime("%a %d %b") if pd.notna(date_obj) else ""

    is_team = "Team" in athlete or "Saudi" in athlete
    if is_team:
        # large flag emoji
        avatar = "<div style='font-size:62px;line-height:1;'>🇸🇦</div>"
    else:
        pk = lookup_person_key(athlete)
        photo = athlete_photo_path(pk)
        if photo:
            import base64
            data = base64.b64encode(photo.read_bytes()).decode()
            avatar = f'<img src="data:image/jpeg;base64,{data}" style="width:72px;height:72px;border-radius:50%;object-fit:cover;border:3px solid {colour};"/>'
        else:
            avatar = f'<img src="{initials_svg(athlete, ELITE)}" style="width:72px;height:72px;border-radius:50%;border:3px solid {colour};"/>'

    # IMPORTANT: no leading indentation on any line. Streamlit's markdown
    # parser treats any HTML indented by 4+ spaces as a code block and shows
    # it as literal text instead of rendering it.
    return (
        f'<div style="background:white;border-radius:8px;padding:1rem;'
        f'box-shadow:0 2px 6px rgba(0,0,0,0.08);border-top:4px solid {colour};'
        f'display:flex;gap:0.9rem;align-items:center;height:100%;">'
        f'{avatar}'
        f'<div style="flex:1;min-width:0;">'
        f'<div style="font-size:0.75rem;font-weight:700;color:{colour};letter-spacing:1px;">{medal_name} · {date_str}</div>'
        f'<div style="font-size:1.05rem;font-weight:700;color:{DISCIPLINE};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{athlete}</div>'
        f'<div style="font-size:0.85rem;color:#666;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{sport} — {event}</div>'
        f'<div style="font-size:0.9rem;color:{ELITE};font-weight:600;margin-top:0.2rem;">{result} <span style="color:#999;font-weight:400;font-size:0.8rem;">{phase}</span></div>'
        f'</div>'
        f'</div>'
    )


# ===========================================================================
# TAB 1: OVERVIEW
# ===========================================================================
with tab_overview:
    # ---- PPT export ----
    overview_sections = []
    if not medals_df.empty:
        overview_sections.append({"title": "Medal Table",
                                  "kind": "metric",
                                  "metrics": [("Gold", str(gold)), ("Silver", str(silver)),
                                              ("Bronze", str(bronze)), ("Rank", f"#{ksa_rank}")]})
        overview_sections.append({"title": "Medal Table — All Nations",
                                  "kind": "table", "df": medals_df})
    if not sched_df.empty:
        unique_ath = sched_df.groupby(["Given Name","Family Name"]).first().reset_index()
        by_sport_count = unique_ath.groupby("Sport").size().reset_index(name="Athletes").sort_values("Athletes", ascending=False)
        overview_sections.append({"title": "KSA Athletes by Sport", "kind": "table", "df": by_sport_count})
    ppt_download_button("Overview", "Team Saudi · Overview",
                        overview_sections,
                        subtitle=f"Live snapshot — {datetime.now():%a %d %b %Y · %H:%M}",
                        key="ppt_ov")
    # Data downloads
    dl1, dl2, dl3 = st.columns(3)
    with dl1: csv_download_button("Medals",       medals_df,     key="csv_medals")
    with dl2: csv_download_button("KSA Results",  results_df,    key="csv_ksa_results")
    with dl3: csv_download_button("Full Schedule",sched_df,      key="csv_full_sched")

    # Medal moments — KSA podium finishes (deduped: 1 medal per match, not per squad member)
    if not results_df.empty and "Medal" in results_df.columns:
        ksa_medals_rows = results_df[
            results_df["Medal"].astype(str).str.strip().str.upper().isin(["G","S","B","GOLD","SILVER","BRONZE"])
        ].copy()
        if not ksa_medals_rows.empty:
            # Safety dedupe: 1 row per (Sport, Discipline/Event_ID, Medal) so a team gold
            # never appears multiple times even if the source file has squad-level rows.
            dedupe_key = ["Sport", "Discipline", "Medal"]
            ksa_medals_rows = ksa_medals_rows.drop_duplicates(subset=dedupe_key, keep="first")
            ksa_medals_rows = ksa_medals_rows.sort_values("Date", ascending=False).head(6)

            # Team vs individual breakdown — both come from the same API source
            # (MEDALS_*.csv is the NOC-level total; this is just transparency)
            is_team = ksa_medals_rows["Athlete"].astype(str).str.contains(r"\bTeam\b|Saudi Arabia", regex=True)
            n_team = int(is_team.sum())
            n_ind  = int((~is_team).sum())

            st.subheader("🏅 Medal Moments")
            st.caption(f"Showing the most recent {len(ksa_medals_rows)} medals · "
                       f"{n_team} team · {n_ind} individual. Counts come from the API's NOC-level "
                       f"medal table (team medals count once, not per squad member).")
            cards_html = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-bottom:1.5rem;">'
            for _, r in ksa_medals_rows.iterrows():
                cards_html += render_medal_card(r)
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.markdown(f"<div class='metric-card'><div class='label'>Gold</div><div class='value'>🥇 {gold}</div></div>", unsafe_allow_html=True)
    m2.markdown(f"<div class='metric-card'><div class='label'>Silver</div><div class='value'>🥈 {silver}</div></div>", unsafe_allow_html=True)
    m3.markdown(f"<div class='metric-card'><div class='label'>Bronze</div><div class='value'>🥉 {bronze}</div></div>", unsafe_allow_html=True)
    m4.markdown(f"<div class='metric-card'><div class='label'>Medal Rank</div><div class='value'>#{ksa_rank}</div></div>", unsafe_allow_html=True)
    n_today_ath = len(today_events.groupby(['Given Name','Family Name'])) if not today_events.empty else 0
    m5.markdown(f"<div class='metric-card'><div class='label'>Athletes / Events Today</div><div class='value'>{n_today_ath} / {len(today_events)}</div></div>", unsafe_allow_html=True)
    st.write("")

    c1, c2, c3 = st.columns([1, 1.2, 1.4])
    with c1:
        st.subheader("Athletes")
        if not sched_df.empty:
            unique = sched_df.groupby(["Given Name", "Family Name"]).first().reset_index()
            unique["Gender"] = unique["Event"].apply(gender_from_event)
            counts = unique["Gender"].value_counts()
            m, f = int(counts.get("Male", 0)), int(counts.get("Female", 0))
            total = m + f
            fig = go.Figure(go.Pie(labels=["Male","Female"], values=[m, f], hole=0.65,
                                   marker=dict(colors=[MALE_COL, FEMALE_COL]), sort=False,
                                   textinfo="label+value", textfont=dict(size=14, color="white")))
            fig.update_layout(showlegend=False,
                              annotations=[dict(text=f"<b>{total}</b><br>Athletes", x=0.5, y=0.5, font_size=18, showarrow=False)],
                              margin=dict(t=10, b=10, l=10, r=10), height=260)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"M {m} ({m/max(total,1)*100:.0f}%) · F {f} ({f/max(total,1)*100:.0f}%)")

    with c2:
        st.subheader("Medal Table")
        if not medals_df.empty:
            st.dataframe(medals_df[["Rank","NOC","Country","Gold","Silver","Bronze","Total"]],
                         hide_index=True, use_container_width=True, height=260)

    with c3:
        st.subheader("Today's Events")
        if not today_events.empty:
            show = today_events.sort_values("Time Start")[
                ["Time Start","Time End","Sport","Event","Phase","Athlete","Venue"]
            ].head(20).rename(columns={"Time Start":"Start","Time End":"End"})
            show["Start"] = show["Start"].apply(fmt_time)
            show["End"]   = show["End"].apply(fmt_time)
            st.dataframe(show, hide_index=True, use_container_width=True, height=260)
        else:
            nxt = sched_df[sched_df["Date"] >= today].sort_values(["Date","Time Start"])
            if not nxt.empty:
                r0 = nxt.iloc[0]
                days = (r0["Date"] - today).days
                st.info(f"**Next event in {days}d**: {r0['Sport']} — {r0['Event']} ({r0['Athlete']}) on {r0['Date'].strftime('%a %d %b')}")
            else:
                st.info("No upcoming events.")
    st.divider()

    st.subheader("KSA Athletes by Sport")
    if not sched_df.empty:
        unique = sched_df.groupby(["Given Name","Family Name"]).first().reset_index()
        unique["Gender"] = unique["Event"].apply(gender_from_event)
        by = unique.groupby(["Sport","Gender"]).size().reset_index(name="n")
        pv = by.pivot(index="Sport", columns="Gender", values="n").fillna(0)
        pv["Total"] = pv.sum(axis=1)
        pv = pv.sort_values("Total", ascending=True)
        fig = go.Figure()
        if "Male" in pv.columns:
            fig.add_trace(go.Bar(y=pv.index, x=pv["Male"], name="Male", orientation="h",
                                  marker_color=MALE_COL, text=pv["Male"].astype(int), textposition="inside"))
        if "Female" in pv.columns:
            fig.add_trace(go.Bar(y=pv.index, x=pv["Female"], name="Female", orientation="h",
                                  marker_color=FEMALE_COL, text=pv["Female"].astype(int), textposition="inside"))
        fig.update_layout(barmode="stack", height=420, margin=dict(t=10, b=20, l=10, r=10),
                          legend=dict(orientation="h", y=1.08), plot_bgcolor="white")
        st.plotly_chart(fig, use_container_width=True)

    # SOTC panels
    if not sched_df.empty and "SOTC" in sched_df.columns and (sched_df["SOTC"]!="").any():
        unique = sched_df.groupby(["Given Name","Family Name"]).first().reset_index()
        unique["SOTC_norm"] = unique["SOTC"].astype(str).str.upper().eq("YES")
        n_sotc = int(unique["SOTC_norm"].sum()); n_non = int((~unique["SOTC_norm"]).sum())
        total = n_sotc + n_non
        c4, c5 = st.columns([1, 2])
        with c4:
            st.subheader("SOTC vs Non-SOTC")
            fig = go.Figure(go.Pie(labels=["SOTC","Non-SOTC"], values=[n_sotc, n_non], hole=0.65,
                                   marker=dict(colors=[ENABLER, ELITE]), sort=False,
                                   textinfo="label+value", textfont=dict(size=14, color="white")))
            fig.update_layout(showlegend=False,
                              annotations=[dict(text=f"<b>{total}</b><br>Athletes", x=0.5, y=0.5, font_size=18, showarrow=False)],
                              margin=dict(t=10, b=10, l=10, r=10), height=320)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"SOTC {n_sotc} ({n_sotc/max(total,1)*100:.0f}%) · Non-SOTC {n_non} ({n_non/max(total,1)*100:.0f}%)")
        with c5:
            st.subheader("SOTC Athletes by Sport")
            by = unique.groupby(["Sport","SOTC_norm"]).size().reset_index(name="n")
            pv = by.pivot(index="Sport", columns="SOTC_norm", values="n").fillna(0)
            pv.columns = ["Non-SOTC" if c is False else "SOTC" for c in pv.columns]
            pv["Total"] = pv.sum(axis=1)
            pv = pv[pv.get("SOTC", 0) > 0].sort_values("Total", ascending=True)
            if not pv.empty:
                fig = go.Figure()
                fig.add_trace(go.Bar(y=pv.index, x=pv["SOTC"], name="SOTC", orientation="h",
                                      marker_color=ENABLER, text=pv["SOTC"].astype(int), textposition="inside"))
                if "Non-SOTC" in pv.columns:
                    fig.add_trace(go.Bar(y=pv.index, x=pv["Non-SOTC"], name="Non-SOTC", orientation="h",
                                          marker_color=ELITE, text=pv["Non-SOTC"].astype(int), textposition="inside"))
                fig.update_layout(barmode="stack", height=320, margin=dict(t=10, b=10, l=10, r=10),
                                  legend=dict(orientation="h", y=1.08), plot_bgcolor="white")
                st.plotly_chart(fig, use_container_width=True)
    st.divider()

    st.subheader("KSA Schedule — Sport × Date")
    st.caption("Cell value = number of **distinct events** (matches/heats/finals) for KSA in that sport on that date. "
               "Not athlete count — Handball with 1 match shows 1, not 16 squad members.")
    if not sched_df.empty:
        grid = (sched_df.groupby(["Sport","Date"])["Event_ID"]
                .nunique().reset_index(name="n"))
        dates = sorted(sched_df["Date"].dropna().unique())
        sports = sorted(sched_df["Sport"].unique())
        z = [[int(grid[(grid["Sport"]==s)&(grid["Date"]==d)]["n"].iloc[0])
              if not grid[(grid["Sport"]==s)&(grid["Date"]==d)].empty else 0
              for d in dates] for s in sports]
        fig = go.Figure(go.Heatmap(z=z,
            x=[pd.Timestamp(d).strftime("%a %d %b") for d in dates], y=sports,
            colorscale=[[0,"#f4f7f5"],[0.2,STAMINA],[0.6,ENABLER],[1,ELITE]], showscale=False,
            text=[[str(v) if v>0 else "" for v in row] for row in z],
            texttemplate="%{text}", textfont={"color":"white","size":12},
            hovertemplate="<b>%{y}</b><br>%{x}<br>%{z} events<extra></extra>"))
        fig.update_layout(height=max(280, 28*len(sports)), margin=dict(t=10, b=10, l=10, r=10), xaxis_side="top")
        st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# TAB: DAY SUMMARY (Tableau-style "Daily Results" view)
# ===========================================================================
with tab_summary:
    results_all_df = load_results_all()

    if sched_df.empty:
        st.info("No schedule data.")
    else:
        comp_dates = sorted(sched_df["Date"].dropna().unique())
        if not comp_dates:
            st.info("No competition dates available.")
        else:
            min_d, max_d = pd.Timestamp(comp_dates[0]).date(), pd.Timestamp(comp_dates[-1]).date()
            today_d   = pd.Timestamp.today().date()
            default_d = today_d if min_d <= today_d <= max_d else min_d
            date_options = [pd.Timestamp(d).date() for d in comp_dates]
            date_labels  = [d.strftime("%a %d %b") for d in date_options]
            default_idx  = date_options.index(default_d) if default_d in date_options else 0
            pick_label = st.radio(
                "Pick a day", date_labels, index=default_idx,
                horizontal=True, key="summary_day_radio",
            )
            pick_day = date_options[date_labels.index(pick_label)]
            pick_dt  = pd.Timestamp(pick_day)
            date_pretty = pick_dt.strftime("%A %d-%b-%y")

            # ---- PPT export for Day Summary ----
            day_summary_sections = []
            _day_results = results_df[results_df["Date"].dt.date == pick_day] if not results_df.empty else pd.DataFrame()
            _day_medals = _day_results[
                _day_results["Medal"].astype(str).str.strip().str.upper().isin(["G","S","B","GOLD","SILVER","BRONZE"])
            ] if not _day_results.empty and "Medal" in _day_results.columns else pd.DataFrame()
            if not _day_medals.empty:
                day_summary_sections.append({
                    "title": f"KSA Medals — {pick_dt:%a %d %b}",
                    "kind": "table",
                    "df": _day_medals[["Sport","Discipline","Athlete","Medal","Result"]].rename(
                        columns={"Discipline": "Event"}),
                })
            if not _day_results.empty:
                day_summary_sections.append({
                    "title": f"All KSA Results — {pick_dt:%a %d %b}",
                    "kind": "table",
                    "df": _day_results[["Sport","Discipline","Athlete","Phase","Result","Medal"]].rename(
                        columns={"Discipline": "Event"}),
                })
            ppt_download_button(
                f"Day Summary {pick_dt:%d %b}", f"Team Saudi · Day Summary — {date_pretty}",
                day_summary_sections,
                subtitle=f"4th GCC Games Doha 2026 · Updated {datetime.now():%H:%M}",
                key=f"ppt_day_sum_{pick_dt:%Y%m%d}",
            )

            # Big title bar matching the example deck
            st.markdown(f"""
            <div style="background:#365a89; color:white; padding:0.8rem 1.2rem;
                        font-size:1.4rem; font-weight:700; border-radius:6px;
                        margin-top:0.5rem; margin-bottom:1rem;">
                GCC 2026 Daily Results — {date_pretty}
                <span style="float:right;font-size:0.9rem;font-weight:400;">
                    <span style="background:#2da06a;padding:2px 8px;border-radius:3px;">Win</span>&nbsp;
                    <span style="background:#c33;     padding:2px 8px;border-radius:3px;">Loss</span>
                </span>
            </div>
            """, unsafe_allow_html=True)

            # ---- Slice today's KSA results ----
            day_results = results_df[results_df["Date"].dt.date == pick_day].copy() if not results_df.empty else pd.DataFrame()
            # Build opponent-score lookup from RESULTS_ALL for this day
            opp_scores: dict = {}
            if not results_all_df.empty:
                day_all = results_all_df[results_all_df["Date"].dt.date == pick_day]
                for _, r in day_all.iterrows():
                    eid = r.get("Source_URL", "").split("/")[-1] or r.get("Event_ID", "")
                    opp_scores.setdefault(eid, []).append({
                        "Country": r.get("Country", ""),
                        "Result":  r.get("Result", ""),
                        "Rank":    r.get("Rank", ""),
                        "Medal":   r.get("Medal", ""),
                    })

            # Join with athlete-schedule for Age / SOTC / Opponent
            sched_today = sched_df[sched_df["Date"].dt.date == pick_day]

            # ---- Render results table + side panels ----
            col_main, col_side = st.columns([2.7, 1.3])

            with col_main:
                # Group by Sport, then per-event
                rendered_html = ['<table style="border-collapse:collapse;width:100%;font-size:0.85rem;">']
                rendered_html.append(
                    '<thead><tr style="background:#f5f5f5;border-bottom:2px solid #ddd;text-align:left;">'
                    '<th style="padding:6px 10px;">Sport</th>'
                    '<th style="padding:6px 10px;">Event</th>'
                    '<th style="padding:6px 10px;">Gender</th>'
                    '<th style="padding:6px 10px;">SOTC</th>'
                    '<th style="padding:6px 10px;">Full Name</th>'
                    '<th style="padding:6px 10px;">Age</th>'
                    '<th style="padding:6px 10px;">Stage</th>'
                    '<th style="padding:6px 10px;">Result</th>'
                    '<th style="padding:6px 10px;">Medal</th>'
                    '</tr></thead><tbody>'
                )

                # Build per-row from athlete-schedule rows on this day.
                # Collapse team events to one row per match — otherwise a
                # 4-fencer Men's Team Foil shows up as 4 duplicate rows here.
                day_rows_sched = sched_today.copy()
                if not day_rows_sched.empty and "Match_Type" in day_rows_sched.columns:
                    _is_team = day_rows_sched["Match_Type"].astype(str).str.lower() == "team"
                    _ind = day_rows_sched[~_is_team]
                    _team = day_rows_sched[_is_team]
                    if not _team.empty:
                        _team_collapsed = []
                        for (_eid, _phase), g in _team.groupby(["Event_ID","Phase"], dropna=False):
                            rep = g.iloc[0].copy()
                            opp = str(rep.get("Opponent") or "").strip()
                            rep["Given Name"]  = "KSA"
                            rep["Family Name"] = f"Team vs {opp}" if opp else "Team"
                            _team_collapsed.append(rep)
                        day_rows_sched = pd.concat([_ind, pd.DataFrame(_team_collapsed)], ignore_index=True)
                day_rows_sched = day_rows_sched.sort_values(["Sport","Family Name","TS"] if "TS" in day_rows_sched.columns
                                                            else ["Sport","Family Name"])

                # Merge in results by (Family Name + Sport + Discipline + Phase)
                def find_result_row(ath_row):
                    if day_results.empty:
                        return None
                    name_match = (day_results["Athlete"].str.contains(ath_row["Family Name"], case=False, na=False)
                                  & day_results["Sport"].eq(ath_row["Sport"]))
                    candidates = day_results[name_match]
                    if candidates.empty:
                        return None
                    # Match by Event_ID when available — most reliable for
                    # disambiguating QF / SF / Final for the same athlete.
                    eid = (ath_row.get("Event_ID") or "").strip()
                    if eid:
                        eid_hit = candidates[candidates["Source_URL"].str.endswith(eid, na=False)]
                        if not eid_hit.empty:
                            return eid_hit.iloc[0]
                    target_phase = (ath_row.get("Phase") or "").strip().lower()
                    event_lower = ath_row.get("Event", "").lower()
                    best = None
                    best_score = -1
                    for _, c in candidates.iterrows():
                        disc  = (c.get("Discipline","") or "").lower()
                        phase = (c.get("Phase","") or "").strip().lower()
                        score = sum(1 for w in event_lower.split() if w and w in disc)
                        if target_phase and phase == target_phase:
                            score += 10  # heavy weight on phase agreement
                        if score > best_score:
                            best, best_score = c, score
                    return best

                prev_sport = ""
                for _, r in day_rows_sched.iterrows():
                    res = find_result_row(r)
                    medal_raw = (res.get("Medal","") if res is not None else "").strip().upper()[:1]
                    medal_tag = {"G":"Gold", "S":"Silver", "B":"Bronze"}.get(medal_raw, "")
                    medal_colour = {"Gold":"#d4af37","Silver":"#bfbfbf","Bronze":"#cd7f32"}.get(medal_tag, "transparent")
                    # Decide win/loss + score string
                    result_str = (res.get("Result","") if res is not None else "").strip()
                    rank_v     = (res.get("Rank","") if res is not None else "").strip()
                    sport_kind = r["Sport"]
                    is_match_sport = sport_kind in ("Basketball 3x3","Basketball 5x5","Handball","Padel","Volleyball",
                                                     "Boxing","Taekwondo","Karate","Fencing","Table Tennis")
                    cell_bg = "transparent"; cell_text = result_str
                    if res is not None:
                        # Win/Loss colour for match sports — compare KSA score vs opponent
                        # (Rank from BORNAN API is NaN for match sports, so the previous
                        # rank-based check never fired.)
                        if is_match_sport:
                            eid = res.get("Source_URL","").split("/")[-1]
                            parts_here = opp_scores.get(eid, [])
                            ksa_score = None; opp_score = None
                            for p in parts_here:
                                if p["Country"].upper() == "KSA":
                                    ksa_score = p["Result"]
                                elif p["Country"]:
                                    opp_score = p["Result"]
                            try:
                                ksa_f = float(ksa_score) if ksa_score not in (None, "") else None
                                opp_f = float(opp_score) if opp_score not in (None, "") else None
                            except (TypeError, ValueError):
                                ksa_f = opp_f = None
                            if ksa_f is not None and opp_f is not None and ksa_f != opp_f:
                                if ksa_f > opp_f:
                                    cell_bg = "#a5d99f"   # green = win
                                    cell_text = f"{int(ksa_f)}-{int(opp_f)} W"
                                else:
                                    cell_bg = "#e8a3a3"   # red = loss
                                    cell_text = f"{int(ksa_f)}-{int(opp_f)} L"
                            else:
                                # Scores tied (BORNAN often reports 0-0 for losing
                                # rounds of a knockout final) or only one side
                                # known. Decide via the Medal column:
                                #   G in Final = Win, S/B in Final = Loss.
                                if medal_raw == "G":
                                    cell_bg = "#a5d99f"
                                    cell_text = "Win"
                                elif medal_raw in ("S", "B"):
                                    cell_bg = "#e8a3a3"
                                    cell_text = "Loss"
                                elif ksa_f is not None and rank_v in ("1","2"):
                                    cell_bg = "#a5d99f" if rank_v == "1" else "#e8a3a3"
                                    cell_text = "Win" if rank_v == "1" else "Loss"
                        if medal_tag:
                            cell_bg = medal_colour
                            cell_text = result_str
                    sport_disp = r["Sport"] if r["Sport"] != prev_sport else ""
                    age_disp = r.get("Age","") or ""
                    sotc_disp = "Yes" if str(r.get("SOTC","")).upper() == "YES" else "No"
                    athlete_full = (r.get("Given Name","") + " " + r.get("Family Name","")).strip().upper()
                    # Event description: use Opponent for team match to read "KSA vs OPP"
                    event_label = r.get("Event","")
                    if r.get("Match_Type") == "team" and r.get("Opponent"):
                        event_label = f"{r['Event']} KSA vs {r['Opponent']}"
                    rendered_html.append(
                        '<tr style="border-bottom:1px solid #eee;">'
                        f'<td style="padding:5px 10px;font-weight:600;color:#235036;">{sport_disp}</td>'
                        f'<td style="padding:5px 10px;">{event_label}</td>'
                        f'<td style="padding:5px 10px;">{r.get("Gender","")}</td>'
                        f'<td style="padding:5px 10px;color:{ELITE if sotc_disp=="Yes" else "#888"};font-weight:{ "700" if sotc_disp=="Yes" else "400"};">{sotc_disp}</td>'
                        f'<td style="padding:5px 10px;">{athlete_full}</td>'
                        f'<td style="padding:5px 10px;">{age_disp}</td>'
                        f'<td style="padding:5px 10px;">{r.get("Phase","")}</td>'
                        f'<td style="padding:5px 10px;background:{cell_bg};">{cell_text}</td>'
                        f'<td style="padding:5px 10px;background:{medal_colour};color:{"white" if medal_tag else "inherit"};font-weight:{"700" if medal_tag else "400"};">{medal_tag}</td>'
                        '</tr>'
                    )
                    prev_sport = r["Sport"]
                rendered_html.append("</tbody></table>")
                st.markdown("".join(rendered_html), unsafe_allow_html=True)

            with col_side:
                # ---- Medals of the Day: stacked bar by sport ----
                st.markdown(f"""
                <div style="background:#365a89;color:white;padding:0.5rem 1rem;
                            font-size:1rem;font-weight:700;border-radius:6px;margin-bottom:0.5rem;">
                    Medals of the Day
                </div>
                """, unsafe_allow_html=True)
                if not day_results.empty:
                    dd = day_results[day_results["Medal"].str.upper().isin(["G","S","B"])].copy()
                    # Dedupe team medals — one per match
                    dd = dd.drop_duplicates(subset=["Sport","Discipline","Medal"])
                    if not dd.empty:
                        agg = dd.groupby(["Sport","Medal"]).size().unstack(fill_value=0).reindex(columns=["G","S","B"], fill_value=0)
                        agg["Total"] = agg.sum(axis=1)
                        agg = agg.sort_values("Total", ascending=True)
                        fig = go.Figure()
                        fig.add_trace(go.Bar(y=agg.index, x=agg["G"], name="Gold",   orientation="h", marker_color="#d4af37", text=agg["G"], textposition="inside"))
                        fig.add_trace(go.Bar(y=agg.index, x=agg["S"], name="Silver", orientation="h", marker_color="#bfbfbf", text=agg["S"], textposition="inside"))
                        fig.add_trace(go.Bar(y=agg.index, x=agg["B"], name="Bronze", orientation="h", marker_color="#cd7f32", text=agg["B"], textposition="inside"))
                        fig.update_layout(barmode="stack", height=200,
                                          margin=dict(t=10,b=10,l=10,r=10),
                                          showlegend=False, plot_bgcolor="white",
                                          xaxis_title="")
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.caption("No medals today.")

                # ---- Total medals donut ----
                st.markdown(f"""
                <div style="background:#365a89;color:white;padding:0.5rem 1rem;
                            font-size:1rem;font-weight:700;border-radius:6px;margin:0.6rem 0 0.5rem 0;">
                    Total Medals Today
                </div>
                """, unsafe_allow_html=True)
                if not day_results.empty:
                    dd = day_results[day_results["Medal"].str.upper().isin(["G","S","B"])].copy()
                    dd = dd.drop_duplicates(subset=["Sport","Discipline","Medal"])
                    if not dd.empty:
                        gc = (dd["Medal"].str.upper() == "G").sum()
                        sc = (dd["Medal"].str.upper() == "S").sum()
                        bc = (dd["Medal"].str.upper() == "B").sum()
                        total = gc + sc + bc
                        fig = go.Figure(go.Pie(
                            labels=["Gold","Silver","Bronze"],
                            values=[gc, sc, bc],
                            marker=dict(colors=["#d4af37","#bfbfbf","#cd7f32"]),
                            hole=0.6, sort=False, textinfo="value+label",
                        ))
                        fig.update_layout(
                            showlegend=False, height=240,
                            margin=dict(t=10,b=10,l=10,r=10),
                            annotations=[dict(text=f"<b>{total}</b><br>Medals",
                                              x=0.5, y=0.5, font_size=20, showarrow=False)],
                        )
                        st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# TAB: MEDAL REPORT (cumulative — all medals + benchmark vs 2022)
# ===========================================================================
with tab_medals:
    # Tab colours used in the reference deck
    HEADER_DARK = "#2b2b2b"
    BENCH_GREEN = "#1f7b4d"
    GOLD_DARK   = "#b8860b"
    SILVER_GREY = "#bfbfbf"
    BRONZE_BRN  = "#9a6731"
    BLUE_TOT    = "#365a89"

    def panel_header(title: str, accent: str = "#c98c40") -> str:
        return f"""
        <div style="background:{HEADER_DARK};color:white;padding:0.55rem 1rem;
                    font-weight:700;border-radius:5px 5px 0 0;margin-bottom:0;">
            <span>{title.split(' ',1)[0]}</span>
            <span style="color:{accent};margin-left:0.4rem;">{title.split(' ',1)[1] if ' ' in title else ''}</span>
        </div>
        """

    # ---- collect all KSA medals cumulatively ----
    ksa_medal_rows = pd.DataFrame()
    if not results_df.empty and "Medal" in results_df.columns:
        m = results_df.copy()
        m["MedalU"] = m["Medal"].astype(str).str.strip().str.upper().str[:1]
        m = m[m["MedalU"].isin(["G","S","B"])]
        # Dedupe team-sport medals: 1 per (Sport, Discipline, Medal)
        ksa_medal_rows = m.drop_duplicates(subset=["Sport","Discipline","MedalU"])
        ksa_medal_rows = ksa_medal_rows.sort_values(["Sport","Discipline"])

    # Join with athlete-schedule for Age + SOTC + Gender
    if not ksa_medal_rows.empty and not sched_df.empty:
        sl = sched_df[["Given Name","Family Name","Age","SOTC","Sport","Event"]].drop_duplicates(subset=["Given Name","Family Name","Sport"])
        sl["join_name"] = (sl["Given Name"] + " " + sl["Family Name"]).str.lower().str.strip()
        ksa_medal_rows["join_name"] = ksa_medal_rows["Athlete"].str.lower().str.strip()
        ksa_medal_rows = ksa_medal_rows.merge(
            sl[["join_name","Sport","Age","SOTC"]],
            on=["join_name","Sport"], how="left", suffixes=("","_sl"),
        )

    # ---- PPT export for Medal Report ----
    _medal_sections = []
    gold_n   = int((ksa_medal_rows["MedalU"]=="G").sum()) if not ksa_medal_rows.empty else 0
    silver_n = int((ksa_medal_rows["MedalU"]=="S").sum()) if not ksa_medal_rows.empty else 0
    bronze_n = int((ksa_medal_rows["MedalU"]=="B").sum()) if not ksa_medal_rows.empty else 0
    _h22_for_ppt = load_history_medal_table()
    _ksa22 = _h22_for_ppt.query("NOC == 'KSA'") if not _h22_for_ppt.empty else pd.DataFrame()
    if not _ksa22.empty:
        g22, s22, b22 = int(_ksa22.iloc[0]["Gold"]), int(_ksa22.iloc[0]["Silver"]), int(_ksa22.iloc[0]["Bronze"])
        t22 = g22 + s22 + b22
    else:
        g22 = s22 = b22 = t22 = 0
    _medal_sections.append({
        "title": "Medal Tally — Live",
        "kind": "metric",
        "metrics": [("Gold", str(gold_n)), ("Silver", str(silver_n)),
                    ("Bronze", str(bronze_n)), ("Total", str(gold_n+silver_n+bronze_n))],
    })
    if not ksa_medal_rows.empty:
        ppt_table = ksa_medal_rows.copy()
        ppt_table["Medal"] = ppt_table["MedalU"].map({"G":"Gold","S":"Silver","B":"Bronze"})
        cols_keep = [c for c in ["Sport","Discipline","Athlete","Age","SOTC","Medal","Result"] if c in ppt_table.columns]
        _medal_sections.append({
            "title": "KSA Medals — Detail",
            "kind": "table",
            "df": ppt_table[cols_keep].rename(columns={"Discipline": "Event"}),
        })
    if t22 > 0:
        _medal_sections.append({
            "title": "Benchmark vs GCC 2022",
            "kind": "metric",
            "metrics": [("2026 Gold", f"{gold_n} / {g22}"),
                        ("2026 Silver", f"{silver_n} / {s22}"),
                        ("2026 Bronze", f"{bronze_n} / {b22}"),
                        ("2026 Total", f"{gold_n+silver_n+bronze_n} / {t22}")],
        })
    ppt_download_button(
        "Medal Report", "Team Saudi · Medal Report",
        _medal_sections,
        subtitle=f"4th GCC Games Doha 2026 · Updated {datetime.now():%a %d %b %H:%M}",
        key="ppt_medal_report",
    )

    # ---- TOP ROW: 3 panels ----
    c1, c2, c3 = st.columns([2.0, 1.0, 0.9])

    # Panel 1 — Medals detail table
    with c1:
        st.markdown(panel_header("GCC 2026 Medals"), unsafe_allow_html=True)
        if ksa_medal_rows.empty:
            st.info("No KSA medals yet.")
        else:
            tbl = ['<table style="border-collapse:collapse;width:100%;font-size:0.85rem;background:white;border-radius:0 0 5px 5px;">']
            tbl.append('<thead><tr style="background:#f5f5f5;border-bottom:2px solid #ddd;">'
                       '<th style="text-align:left;padding:5px 10px;">Sport</th>'
                       '<th style="text-align:left;padding:5px 10px;">Events</th>'
                       '<th style="text-align:left;padding:5px 10px;">SOTC</th>'
                       '<th style="text-align:left;padding:5px 10px;">Full Name</th>'
                       '<th style="text-align:left;padding:5px 10px;">Gender</th>'
                       '<th style="text-align:left;padding:5px 10px;">Age</th>'
                       '<th style="text-align:center;padding:5px 10px;">Medal</th>'
                       '</tr></thead><tbody>')
            prev_sport = ""
            colour = {"G":GOLD_DARK, "S":SILVER_GREY, "B":BRONZE_BRN}
            label  = {"G":"Gold",    "S":"Silver",    "B":"Bronze"}
            for _, r in ksa_medal_rows.iterrows():
                sp_disp = r["Sport"] if r["Sport"] != prev_sport else ""
                m_key   = r["MedalU"]
                gender  = "Female" if "women" in str(r.get("Discipline","")).lower() else "Male"
                athlete = str(r.get("Athlete","")).upper()
                age     = str(r.get("Age","") or "")
                sotc    = "Yes" if str(r.get("SOTC","")).upper() == "YES" else "No"
                ev      = str(r.get("Discipline","") or r.get("Event","")).replace("Heat 1","").strip()
                tbl.append(f'<tr style="border-bottom:1px solid #eee;">'
                           f'<td style="padding:4px 10px;font-weight:600;">{sp_disp}</td>'
                           f'<td style="padding:4px 10px;">{ev}</td>'
                           f'<td style="padding:4px 10px;color:{ELITE if sotc=="Yes" else "#888"};font-weight:{"700" if sotc=="Yes" else "400"};">{sotc}</td>'
                           f'<td style="padding:4px 10px;">{athlete}</td>'
                           f'<td style="padding:4px 10px;">{gender}</td>'
                           f'<td style="padding:4px 10px;">{age}</td>'
                           f'<td style="padding:4px 10px;background:{colour[m_key]};color:white;font-weight:700;text-align:center;">{label[m_key]}</td>'
                           f'</tr>')
                prev_sport = r["Sport"]
            tbl.append("</tbody></table>")
            st.markdown("".join(tbl), unsafe_allow_html=True)

    # Panel 2 — Medals by Sport (stacked bar)
    with c2:
        st.markdown(panel_header("GCC 2026 Medals by Sport"), unsafe_allow_html=True)
        if not ksa_medal_rows.empty:
            agg = ksa_medal_rows.groupby(["Sport","MedalU"]).size().unstack(fill_value=0).reindex(columns=["G","S","B"], fill_value=0)
            agg["Total"] = agg.sum(axis=1)
            agg = agg.sort_values("Total", ascending=True)
            fig = go.Figure()
            fig.add_trace(go.Bar(y=agg.index, x=agg["G"], name="Gold",   orientation="h", marker_color=GOLD_DARK,   text=agg["G"], textposition="inside"))
            fig.add_trace(go.Bar(y=agg.index, x=agg["S"], name="Silver", orientation="h", marker_color=SILVER_GREY, text=agg["S"], textposition="inside"))
            fig.add_trace(go.Bar(y=agg.index, x=agg["B"], name="Bronze", orientation="h", marker_color=BRONZE_BRN,  text=agg["B"], textposition="inside"))
            # Total label at end of bar
            for i, sp in enumerate(agg.index):
                fig.add_annotation(x=agg.loc[sp,"Total"]+0.25, y=sp, text=str(agg.loc[sp,"Total"]),
                                    showarrow=False, font=dict(size=11, color="#333"))
            fig.update_layout(barmode="stack", height=220, showlegend=False,
                              margin=dict(t=8, b=8, l=8, r=20),
                              plot_bgcolor="white", xaxis_visible=False)
            st.plotly_chart(fig, use_container_width=True)

    # Panel 3 — Total Medals donut
    with c3:
        st.markdown(panel_header("GCC 2026 Total Medals"), unsafe_allow_html=True)
        if not ksa_medal_rows.empty:
            gc = int((ksa_medal_rows["MedalU"]=="G").sum())
            sc = int((ksa_medal_rows["MedalU"]=="S").sum())
            bc = int((ksa_medal_rows["MedalU"]=="B").sum())
            total = gc + sc + bc
            fig = go.Figure(go.Pie(
                labels=["Gold","Silver","Bronze"],
                values=[gc, sc, bc],
                marker=dict(colors=[GOLD_DARK, SILVER_GREY, BRONZE_BRN]),
                hole=0.62, sort=False, textinfo="value",
                direction="clockwise",
            ))
            fig.update_layout(
                showlegend=False, height=240,
                margin=dict(t=8, b=8, l=8, r=8),
                annotations=[dict(text=f"<b>{total}</b><br><span style='font-size:0.7em'>Medals</span>",
                                  x=0.5, y=0.5, font_size=22, showarrow=False)],
            )
            st.plotly_chart(fig, use_container_width=True)

    st.write("")

    # ---- BENCHMARK BANNER ----
    st.markdown(f"""
    <div style="background:{BENCH_GREEN};color:white;padding:0.6rem 1rem;
                font-size:1.1rem;font-weight:700;text-align:center;border-radius:5px;
                margin-bottom:0.8rem;">
        BENCHMARK GCC 2026 - 2022
    </div>
    """, unsafe_allow_html=True)

    # ---- BOTTOM ROW: Medals by Sport (2026 vs 2022)  +  Medal Target ----
    b1, b2 = st.columns([1.4, 1.6])

    with b1:
        st.markdown("**Medals by Sport**")
        # 2026 stacked bar
        a1, a2 = st.columns(2)
        with a1:
            if not ksa_medal_rows.empty:
                agg = ksa_medal_rows.groupby(["Sport","MedalU"]).size().unstack(fill_value=0).reindex(columns=["G","S","B"], fill_value=0)
                agg["Total"] = agg.sum(axis=1)
                agg = agg.sort_values("Total", ascending=True)
                fig = go.Figure()
                fig.add_trace(go.Bar(y=agg.index, x=agg["G"], name="Gold",   orientation="h", marker_color=GOLD_DARK,   text=agg["G"], textposition="inside"))
                fig.add_trace(go.Bar(y=agg.index, x=agg["S"], name="Silver", orientation="h", marker_color=SILVER_GREY, text=agg["S"], textposition="inside"))
                fig.add_trace(go.Bar(y=agg.index, x=agg["B"], name="Bronze", orientation="h", marker_color=BRONZE_BRN,  text=agg["B"], textposition="inside"))
                for sp in agg.index:
                    fig.add_annotation(x=agg.loc[sp,"Total"]+0.25, y=sp, text=str(agg.loc[sp,"Total"]),
                                        showarrow=False, font=dict(size=11, color="#333"))
                fig.update_layout(barmode="stack", height=180, showlegend=False,
                                  margin=dict(t=4,b=30,l=4,r=14), plot_bgcolor="white",
                                  xaxis_visible=False, title=dict(text="GCC 2026", y=0.0, x=0.5,
                                                                     font=dict(size=12)))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("(no medals yet)")
        with a2:
            # GCC 2022 — same sports filtered
            hist_ksa_local = load_history_ksa_sport()
            if not hist_ksa_local.empty:
                # show only sports KSA has medalled in at 2026 (parity with left bar)
                if not ksa_medal_rows.empty:
                    sports_2026 = ksa_medal_rows["Sport"].unique()
                    h = hist_ksa_local[hist_ksa_local["Sport"].isin(sports_2026)].copy()
                else:
                    h = hist_ksa_local.copy()
                h["Total"] = h["Gold"] + h["Silver"] + h["Bronze"]
                h = h.sort_values("Total", ascending=True)
                fig = go.Figure()
                fig.add_trace(go.Bar(y=h["Sport"], x=h["Gold"],   name="Gold",   orientation="h", marker_color=GOLD_DARK,   text=h["Gold"], textposition="inside"))
                fig.add_trace(go.Bar(y=h["Sport"], x=h["Silver"], name="Silver", orientation="h", marker_color=SILVER_GREY, text=h["Silver"], textposition="inside"))
                fig.add_trace(go.Bar(y=h["Sport"], x=h["Bronze"], name="Bronze", orientation="h", marker_color=BRONZE_BRN,  text=h["Bronze"], textposition="inside"))
                for _, row in h.iterrows():
                    fig.add_annotation(x=row["Total"]+0.25, y=row["Sport"], text=str(int(row["Total"])),
                                        showarrow=False, font=dict(size=11, color="#333"))
                fig.update_layout(barmode="stack", height=180, showlegend=False,
                                  margin=dict(t=4,b=30,l=4,r=14), plot_bgcolor="white",
                                  xaxis_visible=False, title=dict(text="GCC 2022", y=0.0, x=0.5,
                                                                     font=dict(size=12)))
                st.plotly_chart(fig, use_container_width=True)

    with b2:
        st.markdown("**Medal Target**")
        # Build 4-bar grouped chart: Gold, Silver, Bronze, Total
        # Bottom bar = current (filled), top bar (gray) = target = 2022 totals
        _h22 = load_history_medal_table()
        ksa_22 = _h22.query("NOC == 'KSA'") if not _h22.empty else pd.DataFrame()
        targets = {"Gold": 16, "Silver": 22, "Bronze": 29, "Total": 67}
        if not ksa_22.empty:
            targets = {
                "Gold":   int(ksa_22.iloc[0]["Gold"]),
                "Silver": int(ksa_22.iloc[0]["Silver"]),
                "Bronze": int(ksa_22.iloc[0]["Bronze"]),
                "Total":  int(ksa_22.iloc[0]["Total"]),
            }
        gc = int((ksa_medal_rows["MedalU"]=="G").sum()) if not ksa_medal_rows.empty else 0
        sc = int((ksa_medal_rows["MedalU"]=="S").sum()) if not ksa_medal_rows.empty else 0
        bc = int((ksa_medal_rows["MedalU"]=="B").sum()) if not ksa_medal_rows.empty else 0
        actuals = {"Gold": gc, "Silver": sc, "Bronze": bc, "Total": gc + sc + bc}
        cols = ["Gold","Silver","Bronze","Total"]
        col_colours = [GOLD_DARK, SILVER_GREY, BRONZE_BRN, BLUE_TOT]
        # Target backdrop + actual on top
        fig = go.Figure()
        fig.add_trace(go.Bar(x=cols, y=[targets[c] for c in cols], name="2022 target",
                              marker=dict(color="#e5e5e5", line=dict(color="#bbb", width=1)),
                              text=[targets[c] for c in cols], textposition="outside",
                              textfont=dict(size=14, color="#666"),
                              hovertemplate="%{x}: target %{y}<extra></extra>"))
        fig.add_trace(go.Bar(x=cols, y=[actuals[c] for c in cols], name="2026 actual",
                              marker=dict(color=col_colours),
                              text=[actuals[c] for c in cols], textposition="inside",
                              textfont=dict(size=14, color="white"),
                              hovertemplate="%{x}: actual %{y}<extra></extra>"))
        fig.update_layout(barmode="overlay", height=320,
                          showlegend=False, plot_bgcolor="white",
                          margin=dict(t=18,b=10,l=10,r=10),
                          yaxis=dict(visible=False))
        st.plotly_chart(fig, use_container_width=True)

    st.caption("Medal Report & GCC 2026-2022 benchmark and target")


# ===========================================================================
# TAB: DAILY PLAN
# ===========================================================================
with tab_daily:
    st.subheader("Daily Plan")
    if sched_df.empty:
        st.info("No schedule data.")
    else:
        # Date picker: limit to competition window
        comp_dates = sorted(sched_df["Date"].dropna().unique())
        if not comp_dates:
            st.info("No competition dates in schedule.")
        else:
            min_d, max_d = pd.Timestamp(comp_dates[0]).date(), pd.Timestamp(comp_dates[-1]).date()
            today_d = pd.Timestamp.today().date()
            default_d = today_d if min_d <= today_d <= max_d else min_d

            # Horizontal date pill row — one button per competition day
            date_options = [pd.Timestamp(d).date() for d in comp_dates]
            date_labels  = [d.strftime("%a %d %b") for d in date_options]
            default_idx  = date_options.index(default_d) if default_d in date_options else 0
            pick_label = st.radio(
                "Pick a day", date_labels, index=default_idx,
                horizontal=True, key="daily_pick_radio",
                label_visibility="visible",
            )
            pick = date_options[date_labels.index(pick_label)]

            f1, f2 = st.columns([3, 1])
            with f1:
                _all_sports = sorted(sched_df["Sport"].unique())
                _targets = [s for s in ["Athletics","Swimming","Taekwondo","Karate"] if s in _all_sports]
                # key bumped so the new target-sport default replaces any cached selection
                daily_sports = st.multiselect(
                    "Sports filter",
                    options=_all_sports,
                    default=_targets or _all_sports,
                    key="daily_sports_v2",
                )
            with f2:
                sotc_filter = st.checkbox("SOTC athletes only", value=False, key="daily_sotc")

            day_df = sched_df[
                (sched_df["Date"].dt.date == pick)
                & (sched_df["Sport"].isin(daily_sports))
            ].copy()
            if sotc_filter:
                day_df = day_df[day_df["SOTC"].astype(str).str.upper() == "YES"]

            if day_df.empty:
                st.info(f"No events on {fmt_date(pick)} matching the filters.")
            else:
                # Compute time columns BEFORE building the PPT export sections
                day_df["TS"] = pd.to_datetime(day_df["Date"].dt.strftime("%Y-%m-%d") + " " + day_df["Time Start"].apply(_pad_time), errors="coerce")
                day_df["TE"] = pd.to_datetime(day_df["Date"].dt.strftime("%Y-%m-%d") + " " + day_df["Time End"].apply(_pad_time),   errors="coerce")
                miss = day_df["TE"].isna() & day_df["TS"].notna()
                day_df.loc[miss, "TE"] = day_df.loc[miss, "TS"] + pd.to_timedelta(day_df.loc[miss, "Duration_Min"], unit="min")
                day_df = day_df.dropna(subset=["TS","TE"]).sort_values("TS")

                # Build the de-duplicated view used by the per-athlete table
                # AND the Gantt: collapse team events (one row per match
                # rather than per squad member) so 4 fencers in a Men's
                # Team Foil match no longer appear as 4 separate rows.
                def collapse_team_rows(df: pd.DataFrame) -> pd.DataFrame:
                    if df.empty:
                        return df
                    is_team = df["Match_Type"].astype(str).str.lower() == "team"
                    ind_rows = df[~is_team]
                    team_rows = df[is_team]
                    if team_rows.empty:
                        return ind_rows
                    # One row per (Event_ID, Phase) — keep first row's metadata
                    # and replace the athlete with "KSA (Team) vs OPP"
                    grouped = []
                    for (_eid, _phase), g in team_rows.groupby(["Event_ID","Phase"], dropna=False):
                        rep = g.iloc[0].copy()
                        opp = str(rep.get("Opponent") or "").strip()
                        rep["Athlete"] = f"KSA (Team) vs {opp}" if opp else "KSA (Team)"
                        rep["Given Name"] = "KSA"
                        rep["Family Name"] = "Team"
                        grouped.append(rep)
                    team_collapsed = pd.DataFrame(grouped)
                    return pd.concat([ind_rows, team_collapsed], ignore_index=True).sort_values("TS")

                day_df_dedup = collapse_team_rows(day_df)

                # ---- PPT export for this day ----
                daily_sections = []
                # Slide 1: Gantt of the daily athlete schedule
                gantt_fig = build_daily_gantt_fig(day_df_dedup, title=f"Daily Athlete Schedule — {fmt_date(pick)}")
                if gantt_fig is not None:
                    daily_sections.append({"title": f"Schedule Gantt — {fmt_date(pick)}",
                                            "kind": "chart", "fig": gantt_fig})

                # Slide 2: venues map
                venue_coords = load_venues()
                vt_rows = []
                for v in sorted(set(v.strip() for v in day_df["Venue"].dropna().astype(str) if v.strip())):
                    coord = venue_coords.get(v) or next((vv for kk, vv in venue_coords.items() if kk.lower().strip()==v.lower().strip()), None)
                    if coord:
                        vt_rows.append({"Venue": v, "District": coord.get("district",""),
                                        "lat": coord["lat"], "lon": coord["lon"],
                                        "Events": int((day_df["Venue"]==v).sum())})
                if vt_rows:
                    vt_df = pd.DataFrame(vt_rows)
                    venue_fig = px.scatter_mapbox(
                        vt_df, lat="lat", lon="lon", hover_name="Venue",
                        hover_data={"District": True, "Events": True, "lat": False, "lon": False},
                        size="Events", size_max=28,
                        color_discrete_sequence=[ELITE], zoom=10, height=500,
                    )
                    venue_fig.update_layout(mapbox_style="open-street-map",
                                             title=f"Venues — {fmt_date(pick)}",
                                             margin=dict(t=40, b=10, l=10, r=10))
                    daily_sections.append({"title": f"Venues — {fmt_date(pick)}",
                                            "kind": "chart", "fig": venue_fig})
                    daily_sections.append({"title": "Venue summary", "kind": "table",
                                            "df": vt_df[["Venue","District","Events"]]})

                # Slide 3+: team matches (collapsed) and full athlete table
                team_matches_today = day_df[day_df["Match_Type"]=="team"].copy()
                if not team_matches_today.empty:
                    team_show = (team_matches_today
                                 .groupby(["Sport","Event_ID","Phase","Discipline_API","Opponent","Venue","Time Start","Time End"])
                                 .agg(Squad_Size=("Athlete","nunique"))
                                 .reset_index())
                    team_show["Match"] = "KSA vs " + team_show["Opponent"].replace("", "?")
                    team_show["Time Start"] = team_show["Time Start"].apply(fmt_time)
                    team_show["Time End"]   = team_show["Time End"].apply(fmt_time)
                    daily_sections.append({"title": "Team Matches Today", "kind": "table",
                                            "df": team_show[["Time Start","Time End","Sport","Match","Phase","Venue","Squad_Size"]]})
                sched_show = day_df.sort_values(["Sport","Family Name","TS"])[
                    ["Sport","Athlete","Gender","SOTC","Phase","Event","Time Start","Time End","Venue"]
                ].copy()
                sched_show["Time Start"] = sched_show["Time Start"].apply(fmt_time)
                sched_show["Time End"]   = sched_show["Time End"].apply(fmt_time)
                daily_sections.append({"title": f"Daily Athlete Schedule (table) — {fmt_date(pick)}",
                                        "kind": "table", "df": sched_show, "max_rows": 30})
                ppt_download_button(f"Daily Plan {fmt_date(pick)}",
                                    f"Team Saudi · Daily Plan · {fmt_date(pick)}",
                                    daily_sections,
                                    subtitle="Athletes, events and venues for this competition day",
                                    key="ppt_daily")
                # CSV download — the filtered day data including SOTC + Opponent
                csv_download_button(f"Daily Plan {fmt_date(pick)}",
                                    day_df[[c for c in day_df.columns if not c.startswith("_")]],
                                    key="csv_daily")

                # ---- Full-Schedule PPT (one slide per competition day) ----
                full_sections = []
                # Apply the same target-sport / SOTC filters across all days
                all_df = sched_df[sched_df["Sport"].isin(daily_sports)].copy()
                if sotc_filter:
                    all_df = all_df[all_df["SOTC"].astype(str).str.upper() == "YES"]
                all_df["TS"] = pd.to_datetime(all_df["Date"].dt.strftime("%Y-%m-%d") + " " + all_df["Time Start"].apply(_pad_time), errors="coerce")
                all_df["TE"] = pd.to_datetime(all_df["Date"].dt.strftime("%Y-%m-%d") + " " + all_df["Time End"].apply(_pad_time),   errors="coerce")
                miss = all_df["TE"].isna() & all_df["TS"].notna()
                all_df.loc[miss, "TE"] = all_df.loc[miss, "TS"] + pd.to_timedelta(all_df.loc[miss, "Duration_Min"], unit="min")
                all_df = all_df.dropna(subset=["TS","TE"])

                for d, gd in all_df.groupby("Date"):
                    gd = gd.copy()
                    g_fig = build_daily_gantt_fig(gd, title=f"{fmt_date(d)} — Team Saudi Schedule")
                    if g_fig is not None:
                        full_sections.append({"title": f"Schedule — {fmt_date(d)}",
                                               "kind": "chart", "fig": g_fig})

                ppt_download_button("Full Schedule (all days)",
                                    "Team Saudi · GCC Doha 2026 · Schedule",
                                    full_sections,
                                    subtitle=f"One slide per competition day · {len(full_sections)} days",
                                    key="ppt_full_sched")

                day_df["Priority"] = day_df.apply(event_priority, axis=1)

                # Tile row (athlete-focused, no camera concerns here)
                t1, t2, t3, t4 = st.columns(4)
                t1.markdown(f"<div class='metric-card'><div class='label'>Date</div><div class='value' style='font-size:1.1rem;'>{fmt_date(pick)}</div></div>", unsafe_allow_html=True)
                t2.markdown(f"<div class='metric-card'><div class='label'>Events</div><div class='value'>{len(day_df)}</div></div>", unsafe_allow_html=True)
                n_athletes_today = day_df.groupby(["Given Name","Family Name"]).ngroups
                t3.markdown(f"<div class='metric-card'><div class='label'>Athletes</div><div class='value'>{n_athletes_today}</div></div>", unsafe_allow_html=True)
                n_sotc_today = day_df[day_df["SOTC"].astype(str).str.upper()=="YES"].groupby(["Given Name","Family Name"]).ngroups
                t4.markdown(f"<div class='metric-card'><div class='label'>SOTC athletes</div><div class='value'>{n_sotc_today}</div></div>", unsafe_allow_html=True)
                st.write("")

                # ---- Team matches summary (1 row per match, not per squad member) ----
                team_rows = day_df[day_df["Match_Type"] == "team"].copy()
                if not team_rows.empty:
                    st.markdown("### Team matches today")
                    # Collapse: 1 row per (Sport, Event_ID, Phase) - the actual match
                    match_summary = (team_rows
                                     .groupby(["Sport", "Event_ID", "Phase", "Discipline_API",
                                               "Time Start", "Time End", "Venue", "Opponent", "Date"])
                                     .agg(Squad_Size=("Athlete", "nunique"))
                                     .reset_index())
                    match_summary["Match"] = "KSA vs " + match_summary["Opponent"].replace("", "?")
                    match_summary["Time Start"] = match_summary["Time Start"].apply(fmt_time)
                    match_summary["Time End"]   = match_summary["Time End"].apply(fmt_time)
                    show_match = match_summary[["Time Start","Time End","Sport","Match","Phase","Discipline_API","Venue","Squad_Size"]]
                    show_match = show_match.rename(columns={"Discipline_API":"Event"}).sort_values("Time Start")
                    st.dataframe(show_match, hide_index=True, use_container_width=True)
                    st.caption(f"{len(match_summary)} team match{'es' if len(match_summary)!=1 else ''} today — one row per match (medal counted once, not per squad member).")

                # ---- Athlete-grouped daily schedule (ISG-style HTML table) ----
                st.markdown("### Daily athlete schedule")

                phase_colours = {
                    "Final":         VICTORY,    # gold
                    "Semi Final":    ENABLER,    # accent green
                    "Quarter Final": "#76b6d8",  # light blue
                    "Qualification": STAMINA,    # light Team Saudi green
                    "Preliminary":   STAMINA,
                    "Heats":         STAMINA,
                    "Heat":          STAMINA,
                    "Group Stage":   ELITE,      # primary dark green
                    "Group":         ELITE,
                    "Round of 16":   LAVENDER,
                    "Round of 32":   LAVENDER,
                    "Round of 64":   LAVENDER,
                    "Knockout":      LAVENDER,
                    "Training":      "#cccccc",
                }

                # Per-athlete schedule shows individual events + 1 row per team
                # match. The "Team matches today" panel above already covers
                # team events, but keeping them in this view too is useful for
                # the per-day Gantt visualisation. Each team match = 1 row.
                ath_view = day_df_dedup.copy().sort_values(["Sport","Family Name","Given Name","TS"])
                # Decide the time window for the day's bars
                day_min = ath_view["TS"].min()
                day_max = ath_view["TE"].max()
                # Round to whole hours, with a 30-min pad
                start_h = max(7,  int(day_min.hour))
                end_h   = min(23, int(day_max.hour) + (1 if day_max.minute else 0))
                if end_h - start_h < 6:
                    end_h = start_h + 6   # always show at least a 6h span
                total_h = end_h - start_h

                # Build the HTML table
                hdr_cols = ["Sport","Athlete","Gender","SOTC","Phase","Event","Start","End","Schedule"]
                rows_html = ['<table class="isg-schedule"><thead><tr>']
                for c in hdr_cols:
                    rows_html.append(f'<th>{c}</th>')
                rows_html.append('</tr></thead><tbody>')

                prev_sport, prev_ath = None, None
                for _, r in ath_view.iterrows():
                    ts, te = r["TS"], r["TE"]
                    if pd.isna(ts) or pd.isna(te):
                        continue
                    ts_h = ts.hour + ts.minute/60
                    te_h = te.hour + te.minute/60
                    left = max(0, min(100, (ts_h - start_h) / total_h * 100))
                    width = max(0.8, (te_h - ts_h) / total_h * 100)
                    colour = phase_colours.get(str(r["Phase"]).strip(), "#888")

                    sport = r["Sport"] if r["Sport"] != prev_sport else ""
                    sport_class = "sport-cell" if sport else "blank-cell"
                    ath_name = r["Athlete"] or f"{r['Given Name']} {r['Family Name']}".strip()
                    ath_disp = ath_name if (r["Sport"] != prev_sport or ath_name != prev_ath) else ""
                    ath_disp = ath_disp.upper()
                    # For team-sport rows, append (vs OPP) to event
                    event_disp = r["Event"]
                    if r["Match_Type"] == "team" and r["Opponent"]:
                        event_disp = f"{r['Event']} (KSA vs {r['Opponent']})"
                    sotc_disp = "SOTC" if str(r["SOTC"]).upper() == "YES" else ""
                    bar_html = (
                        f'<div class="bar-row">'
                        f'<span class="bar-time">{fmt_time(r["Time Start"])}</span>'
                        f'<div class="bar-track">'
                        f'<div class="bar-fill" style="left:{left:.1f}%;width:{width:.1f}%;background:{colour};" '
                        f'title="{r["Phase"]} · {event_disp} · {r.get("Venue","")}"></div>'
                        f'</div></div>'
                    )

                    border_style = "border-top:2px solid #ccc;" if sport else ""
                    rows_html.append(
                        f'<tr style="{border_style}">'
                        f'<td class="{sport_class}"><b>{sport}</b></td>'
                        f'<td class="athlete-cell">{ath_disp}</td>'
                        f'<td>{r.get("Gender","")}</td>'
                        f'<td class="sotc-cell">{sotc_disp}</td>'
                        f'<td>{r["Phase"]}</td>'
                        f'<td>{event_disp}</td>'
                        f'<td class="time-cell">{fmt_time(r["Time Start"])}</td>'
                        f'<td class="time-cell">{fmt_time(r["Time End"])}</td>'
                        f'<td class="bar-cell">{bar_html}</td>'
                        f'</tr>'
                    )
                    prev_sport, prev_ath = r["Sport"], ath_name

                # Time-axis footer row
                hour_ticks = list(range(start_h, end_h + 1, 2))
                tick_html = '<div class="bar-axis">'
                for h in hour_ticks:
                    pos = (h - start_h) / total_h * 100
                    label = pd.Timestamp(f"2026-05-12 {h:02d}:00").strftime("%-I %p") if False else f"{h:02d}:00"
                    tick_html += f'<span class="bar-tick" style="left:{pos:.1f}%;">{label}</span>'
                tick_html += '</div>'
                rows_html.append(
                    f'<tr class="axis-row"><td colspan="8"></td>'
                    f'<td class="bar-cell">{tick_html}</td></tr>'
                )
                rows_html.append("</tbody></table>")

                st.markdown(f"""
                <style>
                .isg-schedule {{
                  border-collapse:collapse; width:100%; font-size:0.85rem; font-family:inherit;
                }}
                .isg-schedule th {{
                  background:#f5f5f5; text-align:left; padding:6px 10px; font-weight:600;
                  border-bottom:2px solid #ddd; color:{DISCIPLINE};
                }}
                .isg-schedule td {{
                  padding:5px 10px; vertical-align:middle; border-bottom:1px solid #eee;
                }}
                .athlete-cell {{ font-weight:600; color:{DISCIPLINE}; }}
                .sotc-cell {{ color:{ENABLER}; font-size:0.75rem; font-weight:700; }}
                .time-cell {{ color:#555; font-variant-numeric:tabular-nums; }}
                .bar-cell {{ width:45%; }}
                .bar-row {{ position:relative; display:flex; align-items:center; height:22px; }}
                .bar-track {{ position:relative; width:100%; height:14px; background:#fafafa; border-radius:3px; }}
                .bar-fill {{ position:absolute; top:0; bottom:0; border-radius:3px; }}
                .bar-time {{ display:none; }}
                .bar-axis {{ position:relative; width:100%; height:18px; color:#666; font-size:0.7rem; }}
                .bar-tick {{ position:absolute; transform:translateX(-50%); top:0; }}
                .axis-row td {{ border-bottom:none; padding-top:0; }}
                </style>
                """ + "".join(rows_html), unsafe_allow_html=True)

                # legend
                legend_items = [
                    ("Final", VICTORY),
                    ("Semi Final", ENABLER),
                    ("Quarter Final", "#76b6d8"),
                    ("Qualification / Heats", STAMINA),
                    ("Group Stage", ELITE),
                    ("Knockout / R16/R32", LAVENDER),
                    ("Training", "#cccccc"),
                ]
                leg_html = '<div style="display:flex;gap:1rem;font-size:0.8rem;color:#555;margin-top:0.4rem;">'
                for name, col in legend_items:
                    leg_html += (f'<span><span style="display:inline-block;width:14px;height:10px;'
                                 f'background:{col};border-radius:2px;margin-right:4px;vertical-align:middle;"></span>{name}</span>')
                leg_html += '</div>'
                st.markdown(leg_html, unsafe_allow_html=True)

                # ---- Venue map ----
                venue_coords = load_venues()
                venues_today = sorted(set(v.strip() for v in ath_view["Venue"].dropna().astype(str) if v.strip()))
                event_count_by_venue = ath_view.groupby("Venue").size().to_dict()

                map_rows = []
                for v in venues_today:
                    coord = venue_coords.get(v)
                    if not coord:
                        # try case-insensitive lookup
                        for k, val in venue_coords.items():
                            if k.lower().strip() == v.lower().strip():
                                coord = val; break
                    if coord:
                        map_rows.append({
                            "Venue": v, "District": coord.get("district", ""),
                            "lat": coord["lat"], "lon": coord["lon"],
                            "Events": int(event_count_by_venue.get(v, 0)),
                        })

                if map_rows:
                    map_df = pd.DataFrame(map_rows)
                    st.markdown("### Venues today")
                    m_left, m_right = st.columns([2, 1])
                    with m_left:
                        fig_map = px.scatter_mapbox(
                            map_df, lat="lat", lon="lon",
                            hover_name="Venue",
                            hover_data={"District": True, "Events": True, "lat": False, "lon": False},
                            size="Events", size_max=28,
                            color_discrete_sequence=[ELITE],
                            zoom=10, height=380,
                        )
                        fig_map.update_layout(mapbox_style="open-street-map",
                                               margin=dict(t=0, b=0, l=0, r=0))
                        st.plotly_chart(fig_map, use_container_width=True)
                    with m_right:
                        show_venues = map_df[["Venue","District","Events"]].sort_values("Events", ascending=False)
                        st.dataframe(show_venues, hide_index=True, use_container_width=True, height=380)
                else:
                    if venues_today:
                        st.caption(f"📍 **Venues today:** {' · '.join(venues_today)} (no coordinates wired)")

                # Per-sport summary table for this day
                st.markdown("### Sport summary")
                summ = []
                for sp, g in day_df.groupby("Sport"):
                    summ.append({
                        "Sport":       sp,
                        "Events":      len(g),
                        "Athletes":    g.groupby(["Given Name","Family Name"]).ngroups,
                        "SOTC":        int((g["SOTC"].astype(str).str.upper()=="YES").sum() and
                                            g[g["SOTC"].astype(str).str.upper()=="YES"].groupby(["Given Name","Family Name"]).ngroups),
                        "First":       fmt_time(g.sort_values("TS").iloc[0]["Time Start"]),
                        "Last":        fmt_time(g.sort_values("TS").iloc[-1]["Time End"]),
                        "Phases":      ", ".join(sorted(set(g["Phase"].astype(str)))),
                        "Venues":      ", ".join(sorted(set(g["Venue"].astype(str).str.strip()))),
                    })
                st.dataframe(pd.DataFrame(summ), hide_index=True, use_container_width=True)



# ===========================================================================
# TAB 2: PA COVERAGE PLAN
# ===========================================================================
with tab_plan:
    st.subheader("Performance Analysis — Coverage Plan")
    st.caption("Target: SOTC athletes in Athletics, Swimming, Taekwondo and Karate. "
               "Baseline 2 cameras (Luke + Alanoud); 3rd only when help is available.")
    # PPT export (built once plan_df exists below — placeholder)

    # Settings row
    s1, s2, s3 = st.columns(3)
    target_sports = s1.multiselect(
        "Target sports",
        options=sorted(sched_df["Sport"].unique()) if not sched_df.empty else [],
        default=[s for s in ["Athletics","Swimming","Taekwondo","Karate"]
                 if not sched_df.empty and s in sched_df["Sport"].unique()],
    )
    sotc_only = s2.checkbox("SOTC athletes only", value=True)
    use_3rd_cam = s3.checkbox("Plan with 3rd camera (help available)", value=False,
                              help="Off = 2 cameras every day. On = 2 cameras until 13 May, 3 from 14 May (when equipment + help land).")

    # Staff config
    staff = st.text_input(
        "PA Team (comma-separated)",
        value="Luke (Lead), Alanoud, Coach (if available)",
    ).split(",")
    staff = [s.strip() for s in staff if s.strip()]

    # Build coverage dataframe
    if not sched_df.empty and target_sports:
        plan_df = sched_df[sched_df["Sport"].isin(target_sports)].copy()
        if sotc_only:
            plan_df = plan_df[plan_df["SOTC"].astype(str).str.upper() == "YES"]

        # Time columns: prefer manual Shortlist times if available, fall back to API+duration.
        # Apply _pad_time to normalise mixed HH:MM and HH:MM:SS formats before parsing.
        plan_df["TS"] = pd.to_datetime(plan_df["Date"].dt.strftime("%Y-%m-%d") + " " + plan_df["Time Start"].apply(_pad_time),
                                       errors="coerce")
        plan_df["TE"] = pd.to_datetime(plan_df["Date"].dt.strftime("%Y-%m-%d") + " " + plan_df["Time End"].apply(_pad_time),
                                       errors="coerce")
        # If Time End missing, fall back to start + Duration_Min
        miss = plan_df["TE"].isna() & plan_df["TS"].notna()
        plan_df.loc[miss, "TE"] = plan_df.loc[miss, "TS"] + pd.to_timedelta(plan_df.loc[miss, "Duration_Min"], unit="min")

        plan_df = plan_df.dropna(subset=["TS","TE"]).sort_values(["Date","TS"])

        # ---- Summary tiles
        c1, c2, c3, c4 = st.columns(4)
        n_athletes = plan_df.groupby(["Given Name","Family Name"]).ngroups
        n_events   = len(plan_df)
        n_days     = plan_df["Date"].nunique()
        # crude conflict count: pairs of overlapping rows per day
        conflicts = 0
        for d, g in plan_df.groupby("Date"):
            arr = g[["TS","TE"]].values
            for i in range(len(arr)):
                for j in range(i+1, len(arr)):
                    if arr[i][0] < arr[j][1] and arr[j][0] < arr[i][1]:
                        conflicts += 1
        c1.markdown(f"<div class='metric-card'><div class='label'>Athletes</div><div class='value'>{n_athletes}</div></div>", unsafe_allow_html=True)
        c2.markdown(f"<div class='metric-card'><div class='label'>Events</div><div class='value'>{n_events}</div></div>", unsafe_allow_html=True)
        c3.markdown(f"<div class='metric-card'><div class='label'>Competition days</div><div class='value'>{n_days}</div></div>", unsafe_allow_html=True)
        c4.markdown(f"<div class='metric-card'><div class='label'>Time conflicts</div><div class='value'>{conflicts}</div></div>", unsafe_allow_html=True)

        # Manual vs estimated times indicator
        if "Time_Source" in plan_df.columns:
            n_manual    = int((plan_df["Time_Source"] == "Manual (Shortlist)").sum())
            n_estimated = int((plan_df["Time_Source"] != "Manual (Shortlist)").sum())
            st.caption(
                f"⏱ **{n_manual}** of {len(plan_df)} events use your verified times from the Shortlist · "
                f"{n_estimated} still use API + duration estimate. Fill more Time Start/End cells in the Shortlist to improve accuracy."
            )
        st.write("")

        # ---- Gantt chart
        plan_df["Label"]  = plan_df["Sport"] + " — " + plan_df["Athlete"] + " (" + plan_df["Phase"] + ")"
        plan_df["DayStr"] = plan_df["Date"].dt.strftime("%a %d %b")

        # Priority score per event (SOTC + phase + target sport)
        plan_df["Priority"] = plan_df.apply(event_priority, axis=1)

        # Session-aware allocator (per day): one camera covers a whole session
        # at the same venue. Real shortage = UNCOVERED.
        cam_series = pd.Series(dtype=int)
        for d, g in plan_df.groupby("Date"):
            if use_3rd_cam:
                cams_available = 3 if d >= pd.Timestamp("2026-05-14") else 2
            else:
                cams_available = 2
            cam_series = pd.concat([cam_series, allocate_cameras_by_session(g, cams_available)])
        plan_df["Camera"]   = cam_series
        plan_df["Overflow"] = plan_df["Camera"] == 0

        # ---- ISG-style schedule per day (same layout as Daily Plan tab) ----
        st.markdown("### Coverage schedule by day")
        st.caption("Same ISG layout as the Daily Plan tab, but spanning every competition day. Camera column shows the allocator's decision; UNCOVERED rows are real shortages.")
        for d, g in plan_df.groupby("Date"):
            cams_today = 3 if (use_3rd_cam and d >= pd.Timestamp("2026-05-14")) else 2
            n_un_day = int((g["Camera"]==0).sum())
            head_extra = "" if n_un_day == 0 else f"  ·  ⚠ {n_un_day} uncovered"
            render_isg_schedule(g, include_camera=True,
                                title=f"{fmt_date(d)}  ·  {len(g)} events  ·  {cams_today} cameras{head_extra}")

        # Uncovered events — real shortage of cameras at that moment
        overflows = plan_df[plan_df["Overflow"] == True]
        if not overflows.empty:
            st.error(
                f"⚠ {len(overflows)} events **cannot be covered** with the available cameras "
                f"(2 until 14 May, 3 from 14 May). Lowest-priority events listed first — "
                f"these are candidates to skip or ask the coach to record manually on a phone."
            )
            ov_show = overflows[["Date","Time Start","Sport","Event","Phase","Athlete","SOTC","Priority"]].copy()
            ov_show = ov_show.sort_values(["Date","Priority"], ascending=[True, True])
            ov_show["Recommendation"] = ov_show.apply(
                lambda r: "Drop / coach to film" if r["Priority"] < 60 else "Negotiate (close call)",
                axis=1)
            st.dataframe(ov_show, hide_index=True, use_container_width=True)
            st.caption(
                "Priority = SOTC (+50) + Phase weight (Final=100, Semi=80, QF=60, Qual=20) "
                "+ Target sport (Athletics/Swimming/Taekwondo/Karate, +10)."
            )
        else:
            cam_summary = "2 throughout" if not use_3rd_cam else "2 until 13 May → 3 from 14 May"
            st.success(f"✓ All {len(plan_df)} events fit within available cameras ({cam_summary}).")

        st.divider()
        st.subheader("Day-by-day schedule")
        # Daily breakdown
        for d, g in plan_df.groupby("Date"):
            cams_today = 3 if d >= pd.Timestamp("2026-05-14") else 2
            with st.expander(f"**{fmt_date(d)}** — {len(g)} events · {cams_today} cameras"):
                show = g.sort_values("TS")[["Time Start","Time End","Sport","Phase","Athlete","Venue","Camera","SOTC"]].copy()
                show["Time Start"] = show["Time Start"].apply(fmt_time)
                show["Time End"]   = show["Time End"].apply(fmt_time)
                show["Camera"]     = show["Camera"].apply(lambda c: "UNCOVERED" if c == 0 else f"Cam {c}")
                st.dataframe(show, hide_index=True, use_container_width=True)

        # ---- PPT export for PA Plan ----
        plan_sections = []
        n_un_total = int((plan_df["Camera"]==0).sum()) if "Camera" in plan_df.columns else 0
        plan_sections.append({"title": "Coverage Summary", "kind": "metric",
                              "metrics": [("Athletes", str(n_athletes)),
                                          ("Events", str(n_events)),
                                          ("Days", str(n_days)),
                                          ("Uncovered", str(n_un_total))]})
        for d_grp, g_grp in plan_df.groupby("Date"):
            show = g_grp.sort_values("TS")[
                ["Time Start","Time End","Sport","Phase","Athlete","Venue","Camera"]
            ].copy()
            show["Time Start"] = show["Time Start"].apply(fmt_time)
            show["Time End"]   = show["Time End"].apply(fmt_time)
            show["Camera"]     = show["Camera"].apply(lambda c: "UNCOVERED" if c == 0 else f"Cam {c}")
            plan_sections.append({"title": fmt_date(d_grp), "kind": "table", "df": show, "max_rows": 20})
        ppt_download_button("PA Coverage Plan",
                            "Team Saudi · PA Coverage Plan",
                            plan_sections,
                            subtitle=f"Cameras: {'2 throughout' if not use_3rd_cam else '2 → 3 from 14 May'} · SOTC priority",
                            key="ppt_pa")
        csv_download_button("PA Coverage Plan",
                            plan_df[[c for c in plan_df.columns if not c.startswith("_")]],
                            key="csv_pa_plan")

        st.divider()
        st.subheader("Athlete coverage matrix")
        # Sport + Athlete (rows) × Date (cols), sorted by sport then name
        mat = plan_df.groupby(["Sport", "Athlete", "Date"]).size().reset_index(name="n")
        pv = mat.pivot_table(index=["Sport", "Athlete"], columns="Date",
                             values="n", fill_value=0).astype(int)
        pv.columns = [pd.Timestamp(c).strftime("%a %d") for c in pv.columns]
        pv = pv.reset_index().sort_values(["Sport", "Athlete"])
        st.dataframe(pv, use_container_width=True, hide_index=True)
        st.caption("Each cell = number of phases (e.g. 3 = Qual + Semi + Final on the same day).")
    else:
        st.info("Select at least one target sport to build the plan.")


# ===========================================================================
# TAB: vs 2022
# ===========================================================================
with tab_history:
    st.subheader("Performance vs GCC Games 2022 (Kuwait)")
    st.caption("Reference baseline: KSA finished 4th at Kuwait 2022 with 67 medals (16G · 22S · 29B).")

    hist_table = load_history_medal_table()
    hist_ksa   = load_history_ksa_sport()

    # PPT export — each comparison section is its own slide
    hist_sections = []

    # Slide: target-tracker tiles (computed below in this tab; build a snapshot now)
    _g_live = silver_h = bronze_h = total_live_h = 0
    if not medals_df.empty:
        _kr = medals_df[medals_df["NOC"]=="KSA"]
        if not _kr.empty:
            _g_live = int(_kr.iloc[0]["Gold"]); silver_h = int(_kr.iloc[0]["Silver"])
            bronze_h = int(_kr.iloc[0]["Bronze"]); total_live_h = _g_live + silver_h + bronze_h
    target_h = int(hist_ksa[hist_ksa["In_2026"]!="no"]["Total"].sum()) if not hist_ksa.empty else 51
    hist_sections.append({"title": "Target Tracker", "kind": "metric",
                          "metrics": [("Live medals", str(total_live_h)),
                                      ("2022 total",  "67"),
                                      ("Like-for-like target", str(target_h)),
                                      ("Gap to target",
                                       f"{total_live_h - target_h:+d}")]})

    # Slide: live medal table
    if not medals_df.empty:
        hist_sections.append({"title": "GCC 2026 Live Medal Table",
                              "kind": "table", "df": medals_df})

    # Slide: 2022 medal table reference
    if not hist_table.empty:
        hist_sections.append({"title": "GCC 2022 Medal Table — Reference",
                              "kind": "table", "df": hist_table})

    # Slide: KSA donut comparison (chart)
    if not hist_ksa.empty:
        donut_fig = go.Figure()
        donut_fig.add_trace(go.Pie(labels=["Gold","Silver","Bronze"],
                                    values=[_g_live, silver_h, bronze_h], hole=0.55,
                                    name="Live 2026", domain=dict(x=[0,0.48]),
                                    marker=dict(colors=[MEDAL_COLOURS["G"], MEDAL_COLOURS["S"], MEDAL_COLOURS["B"]]),
                                    sort=False, textinfo="label+value"))
        donut_fig.add_trace(go.Pie(labels=["Gold","Silver","Bronze"],
                                    values=[16, 22, 29], hole=0.55,
                                    name="Kuwait 2022", domain=dict(x=[0.52,1.0]),
                                    marker=dict(colors=[MEDAL_COLOURS["G"], MEDAL_COLOURS["S"], MEDAL_COLOURS["B"]]),
                                    sort=False, textinfo="label+value"))
        donut_fig.update_layout(title="KSA Medals — Live 2026 vs Kuwait 2022",
                                annotations=[
                                    dict(text=f"<b>{total_live_h}</b><br>2026", x=0.21, y=0.5, font_size=22, showarrow=False),
                                    dict(text="<b>67</b><br>2022",                  x=0.79, y=0.5, font_size=22, showarrow=False),
                                ],
                                height=500, margin=dict(t=60, b=10, l=10, r=10),
                                showlegend=False)
        hist_sections.append({"title": "KSA Totals — 2026 vs 2022",
                              "kind": "chart", "fig": donut_fig})

    # Slide: KSA medals by sport in 2022 (chart)
    if not hist_ksa.empty:
        h = hist_ksa.sort_values("Total")
        bar_fig = go.Figure()
        bar_fig.add_trace(go.Bar(y=h["Sport"], x=h["Gold"],   name="Gold",
                                  orientation="h", marker_color=MEDAL_COLOURS["G"],
                                  text=h["Gold"], textposition="inside"))
        bar_fig.add_trace(go.Bar(y=h["Sport"], x=h["Silver"], name="Silver",
                                  orientation="h", marker_color=MEDAL_COLOURS["S"],
                                  text=h["Silver"], textposition="inside"))
        bar_fig.add_trace(go.Bar(y=h["Sport"], x=h["Bronze"], name="Bronze",
                                  orientation="h", marker_color=MEDAL_COLOURS["B"],
                                  text=h["Bronze"], textposition="inside"))
        bar_fig.update_layout(title="KSA Medals by Sport — 2022 baseline",
                              barmode="stack", height=500, margin=dict(t=60, b=10, l=10, r=10),
                              plot_bgcolor="white", xaxis_title="Medals",
                              legend=dict(orientation="h", y=1.1))
        hist_sections.append({"title": "KSA Medals by Sport — 2022",
                              "kind": "chart", "fig": bar_fig})

    # Slide: 2022 by-sport table (data behind the chart)
    if not hist_ksa.empty:
        hist_sections.append({"title": "KSA Medals by Sport (table)",
                              "kind": "table", "df": hist_ksa})

    # Slide: sports flagged as not in 2026 + target note
    if not hist_ksa.empty:
        not_in_2026 = hist_ksa[hist_ksa["In_2026"]=="no"]
        lost_total = int(not_in_2026["Total"].sum()) if not not_in_2026.empty else 0
        body = (
            f"At Kuwait 2022 KSA won {lost_total} medals in sports NOT on the 2026 programme:\n"
            + ", ".join(f"{r['Sport']} ({r['Total']})" for _, r in not_in_2026.iterrows())
            + f"\n\nLike-for-like 2026 target (sports in both games): {target_h} medals."
        )
        hist_sections.append({"title": "Sports off the 2026 programme",
                              "kind": "text", "body": body})

    ppt_download_button("vs 2022 Comparison",
                        "Team Saudi · 2022 vs 2026",
                        hist_sections,
                        subtitle=f"Snapshot — {datetime.now():%a %d %b · %H:%M}",
                        key="ppt_hist")

    # ---- TARGET TRACKER (linear extrapolation) ----
    games_start  = pd.Timestamp("2026-05-12").date()
    games_end    = pd.Timestamp("2026-05-22").date()
    games_total  = (games_end - games_start).days + 1
    today_d      = pd.Timestamp.today().date()

    g_live = silver = bronze = total_live = 0
    if not medals_df.empty:
        kr = medals_df[medals_df["NOC"]=="KSA"]
        if not kr.empty:
            g_live   = int(kr.iloc[0]["Gold"])
            silver   = int(kr.iloc[0]["Silver"])
            bronze   = int(kr.iloc[0]["Bronze"])
            total_live = g_live + silver + bronze

    if today_d < games_start:
        elapsed = 0
        days_until = (games_start - today_d).days
        track_status = f"Games begin in {days_until} day{'s' if days_until!=1 else ''}"
        projection = "—"
    elif today_d > games_end:
        elapsed = games_total
        track_status = "Games complete"
        projection = total_live
    else:
        elapsed = (today_d - games_start).days + 1
        projection = int(round(total_live / elapsed * games_total)) if elapsed else 0
        track_status = f"Day {elapsed} of {games_total}"

    target_2022_like_for_like = int(hist_ksa[hist_ksa["In_2026"]!="no"]["Total"].sum()) if not hist_ksa.empty else 51

    tt1, tt2, tt3, tt4 = st.columns(4)
    tt1.markdown(f"<div class='metric-card'><div class='label'>Status</div><div class='value' style='font-size:1.05rem;'>{track_status}</div></div>", unsafe_allow_html=True)
    tt2.markdown(f"<div class='metric-card'><div class='label'>Medals so far (2026)</div><div class='value'>{total_live}</div></div>", unsafe_allow_html=True)
    proj_colour = "#235036" if isinstance(projection, int) and projection >= target_2022_like_for_like else "#c53030"
    tt3.markdown(f"<div class='metric-card'><div class='label'>Projected total at this pace</div><div class='value' style='color:{proj_colour};'>{projection}</div></div>", unsafe_allow_html=True)
    tt4.markdown(f"<div class='metric-card'><div class='label'>Like-for-like 2022 target</div><div class='value'>{target_2022_like_for_like}</div></div>", unsafe_allow_html=True)
    st.write("")

    # ---- ISG 2025 medallists competing today ----
    if not sched_df.empty and "ISG_2025_Medals" in sched_df.columns:
        isg_today = sched_df[
            (sched_df["Date"] == today)
            & (sched_df["ISG_2025_Medals"].astype(str).str.strip() != "")
        ]
        isg_unique_today = isg_today.groupby(["Given Name", "Family Name"]).first().reset_index()
        n_isg_today = len(isg_unique_today)
        # Count gold/silver/bronze totals across these athletes' ISG history
        g_sum = s_sum = b_sum = 0
        for _, r in isg_unique_today.iterrows():
            mstr = r.get("ISG_2025_Medals", "")
            for tok in str(mstr).split("/"):
                t = tok.strip().upper()
                if t.endswith("G"): g_sum += int(t[:-1] or 0)
                if t.endswith("S"): s_sum += int(t[:-1] or 0)
                if t.endswith("B"): b_sum += int(t[:-1] or 0)

        i1, i2 = st.columns([1, 2])
        with i1:
            st.markdown(f"""
            <div class='metric-card' style='border-left:4px solid {VICTORY};'>
                <div class='label'>⭐ ISG 2025 medallists competing today</div>
                <div class='value'>{n_isg_today}</div>
                <div style='font-size:0.85rem;color:#555;margin-top:0.3rem;'>
                    Their ISG history: {g_sum}G · {s_sum}S · {b_sum}B
                </div>
            </div>
            """, unsafe_allow_html=True)
        with i2:
            if n_isg_today > 0:
                show = isg_unique_today[
                    ["Given Name", "Family Name", "Sport", "Event", "ISG_2025_Medals", "ISG_2025_Sports"]
                ].copy()
                show["Athlete"] = (show["Given Name"] + " " + show["Family Name"]).str.strip()
                show = show[["Athlete", "Sport", "Event", "ISG_2025_Medals", "ISG_2025_Sports"]]
                show = show.rename(columns={"ISG_2025_Medals": "ISG '25", "ISG_2025_Sports": "ISG sport"})
                st.dataframe(show, hide_index=True, use_container_width=True, height=240)
            else:
                st.caption("No ISG-2025 medallists in today's schedule. Check earlier or later days.")
        st.write("")

    # ---- Medal table comparison ----
    if not medals_df.empty and not hist_table.empty:
        live = medals_df.set_index("NOC")[["Gold","Silver","Bronze","Total","Rank"]]
        hist = hist_table.set_index("NOC")[["Gold","Silver","Bronze","Total","Rank"]].rename(
            columns=lambda c: c + "_2022")

        compare = hist.join(live, how="outer").reset_index()
        # Add country names
        compare = compare.merge(hist_table[["NOC","Country"]], on="NOC", how="left")
        # Compute deltas (2026 vs 2022)
        for col in ("Gold","Silver","Bronze","Total"):
            compare[f"Δ {col}"] = (compare[col].fillna(0).astype(int) - compare[f"{col}_2022"].fillna(0).astype(int))

        cols_show = ["Rank","NOC","Country",
                     "Gold","Silver","Bronze","Total",
                     "Gold_2022","Silver_2022","Bronze_2022","Total_2022",
                     "Δ Total"]
        st.markdown("### Medal table — live vs 2022")
        st.dataframe(
            compare[cols_show].rename(columns={"Rank":"Rank_2026","Total":"Total_2026",
                                                "Gold":"G_26","Silver":"S_26","Bronze":"B_26",
                                                "Gold_2022":"G_22","Silver_2022":"S_22","Bronze_2022":"B_22",
                                                "Total_2022":"Total_22"}),
            hide_index=True, use_container_width=True,
        )

    # ---- KSA total vs 2022 (donut comparison) ----
    st.divider()
    st.markdown("### KSA totals — live vs 2022")
    c_live, c_22, c_delta = st.columns(3)

    g_live = silver = bronze = total_live = 0
    if not medals_df.empty:
        kr = medals_df[medals_df["NOC"]=="KSA"]
        if not kr.empty:
            g_live   = int(kr.iloc[0]["Gold"])
            silver   = int(kr.iloc[0]["Silver"])
            bronze   = int(kr.iloc[0]["Bronze"])
            total_live = g_live + silver + bronze

    with c_live:
        st.markdown("**Live 2026**")
        if total_live > 0:
            fig = go.Figure(go.Pie(labels=["Gold","Silver","Bronze"],
                                    values=[g_live,silver,bronze], hole=0.65,
                                    marker=dict(colors=[MEDAL_COLOURS["G"], MEDAL_COLOURS["S"], MEDAL_COLOURS["B"]]),
                                    sort=False, textinfo="label+value"))
            fig.update_layout(showlegend=False, height=240,
                              annotations=[dict(text=f"<b>{total_live}</b><br>Medals", x=0.5, y=0.5, font_size=18, showarrow=False)],
                              margin=dict(t=10,b=10,l=10,r=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("0 medals · games not yet started")

    with c_22:
        st.markdown("**Kuwait 2022**")
        fig = go.Figure(go.Pie(labels=["Gold","Silver","Bronze"],
                                values=[16, 22, 29], hole=0.65,
                                marker=dict(colors=[MEDAL_COLOURS["G"], MEDAL_COLOURS["S"], MEDAL_COLOURS["B"]]),
                                sort=False, textinfo="label+value"))
        fig.update_layout(showlegend=False, height=240,
                          annotations=[dict(text="<b>67</b><br>Medals", x=0.5, y=0.5, font_size=18, showarrow=False)],
                          margin=dict(t=10,b=10,l=10,r=10))
        st.plotly_chart(fig, use_container_width=True)

    with c_delta:
        st.markdown("**Delta**")
        st.metric("Gold",    g_live,   delta=g_live - 16)
        st.metric("Silver",  silver,   delta=silver - 22)
        st.metric("Bronze",  bronze,   delta=bronze - 29)
        st.metric("Total",   total_live, delta=total_live - 67)

    # ---- KSA medals by sport: 2022 baseline vs 2026 live, with delta ----
    st.divider()
    st.markdown("### KSA medals by sport — 2022 vs live 2026")

    # Build 2026 per-sport KSA medals from results_df (deduped at team level)
    live_by_sport = pd.DataFrame(columns=["Sport", "G_26", "S_26", "B_26", "Total_26"])
    if not results_df.empty and "Medal" in results_df.columns:
        live = results_df.copy()
        live["Medal"] = live["Medal"].astype(str).str.strip().str.upper().str[:1]
        live = live[live["Medal"].isin(["G","S","B"])]
        # Dedupe team rows: 1 per (Sport, Discipline, Medal)
        live = live.drop_duplicates(subset=["Sport","Discipline","Medal"], keep="first")
        if not live.empty:
            live_by_sport = (live.groupby(["Sport","Medal"]).size().unstack(fill_value=0)
                             .reindex(columns=["G","S","B"], fill_value=0)
                             .rename(columns={"G":"G_26","S":"S_26","B":"B_26"})
                             .reset_index())
            live_by_sport["Total_26"] = live_by_sport[["G_26","S_26","B_26"]].sum(axis=1)

    if not hist_ksa.empty:
        # Merge 2022 ↔ 2026 by Sport
        compare_sport = hist_ksa.rename(columns={"Gold":"G_22","Silver":"S_22","Bronze":"B_22","Total":"Total_22"})
        compare_sport = compare_sport.merge(live_by_sport, on="Sport", how="left").fillna(0)
        for c in ("G_26","S_26","B_26","Total_26"):
            compare_sport[c] = compare_sport[c].astype(int)
        compare_sport["Δ Total"] = compare_sport["Total_26"] - compare_sport["Total_22"]

        cols_show = ["Sport","In_2026",
                     "G_22","S_22","B_22","Total_22",
                     "G_26","S_26","B_26","Total_26","Δ Total"]
        st.dataframe(compare_sport[cols_show], hide_index=True, use_container_width=True,
                     column_config={
                         "Δ Total": st.column_config.NumberColumn("Δ Total", format="%+d"),
                         "In_2026": st.column_config.TextColumn("In 2026?"),
                     })

        # ---- 2022 bar (kept for visual)
        st.markdown("**2022 KSA medals by sport (visual)**")
        fig_sport = go.Figure()
        h = hist_ksa.sort_values("Total")
        fig_sport.add_trace(go.Bar(y=h["Sport"], x=h["Gold"],
                                    name="Gold",   orientation="h",
                                    marker_color=MEDAL_COLOURS["G"],
                                    text=h["Gold"], textposition="inside"))
        fig_sport.add_trace(go.Bar(y=h["Sport"], x=h["Silver"],
                                    name="Silver", orientation="h",
                                    marker_color=MEDAL_COLOURS["S"],
                                    text=h["Silver"], textposition="inside"))
        fig_sport.add_trace(go.Bar(y=h["Sport"], x=h["Bronze"],
                                    name="Bronze", orientation="h",
                                    marker_color=MEDAL_COLOURS["B"],
                                    text=h["Bronze"], textposition="inside"))
        fig_sport.update_layout(barmode="stack", height=max(280, 22 * len(h)),
                                margin=dict(t=10,b=10,l=10,r=10),
                                plot_bgcolor="white", xaxis_title="Medals (2022)",
                                legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig_sport, use_container_width=True)

        # ---- Sports flagged: in 2022 KSA medal book but NOT in this Games ----
        not_in_2026 = hist_ksa[hist_ksa["In_2026"]=="no"]
        if not not_in_2026.empty:
            lost_total = int(not_in_2026["Total"].sum())
            st.warning(
                f"⚠ **{lost_total} medals from sports not in this Games**: "
                + ", ".join(f"{r['Sport']} ({r['Total']})" for _, r in not_in_2026.iterrows())
                + ". Realistic 2026 target should account for these."
            )

        # ---- Realistic 2026 baseline calculation ----
        target = int(hist_ksa[hist_ksa["In_2026"]!="no"]["Total"].sum())
        st.info(f"💡 Like-for-like 2022 → 2026 baseline (sports in both games): **{target} medals**.")


# ===========================================================================
# TAB: AUDIT
# ===========================================================================
with tab_audit:
    st.subheader("Per-sport audit")
    st.caption("Cross-checks our KSA data against the live GCC API. Run sport-by-sport "
               "to find missing entries, name mismatches, phase oddities. "
               "Re-run after each scrape to track drift.")

    AUDIT_DIR = HERE / "data" / "audit"
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    cA, cB = st.columns([1, 3])
    with cA:
        if st.button("🔄 Run audit now", key="run_audit_btn"):
            try:
                from audit_sports import run_audit
                out = run_audit()
                st.success(f"Audit saved → {out.name}")
            except Exception as e:
                st.error(f"Audit failed: {e}")

    audit_files = sorted(AUDIT_DIR.glob("AUDIT_*.csv"))
    if not audit_files:
        st.info("No audits yet — click 'Run audit now' to generate one.")
    else:
        latest = audit_files[-1]
        with cB:
            st.caption(f"Latest: **{latest.name}** ({file_age(AUDIT_DIR, 'AUDIT_*.csv')})")

        df_audit = pd.read_csv(latest, encoding="utf-8-sig").fillna("")

        # Summary tile row
        sev_counts = df_audit["Severity"].value_counts().to_dict()
        s1, s2, s3, s4 = st.columns(4)
        s1.markdown(f"<div class='metric-card'><div class='label'>HIGH</div><div class='value' style='color:#c53030;'>{sev_counts.get('HIGH',0)}</div></div>", unsafe_allow_html=True)
        s2.markdown(f"<div class='metric-card'><div class='label'>MEDIUM</div><div class='value' style='color:#d97706;'>{sev_counts.get('MEDIUM',0)}</div></div>", unsafe_allow_html=True)
        s3.markdown(f"<div class='metric-card'><div class='label'>LOW</div><div class='value' style='color:#6b7280;'>{sev_counts.get('LOW',0)}</div></div>", unsafe_allow_html=True)
        s4.markdown(f"<div class='metric-card'><div class='label'>INFO (sports)</div><div class='value'>{sev_counts.get('INFO',0)}</div></div>", unsafe_allow_html=True)
        st.write("")

        # Sport-by-sport expanders
        st.markdown("### Sport-by-sport breakdown")
        sport_filter = st.selectbox("Filter to sport",
                                     options=["(all sports)"] + sorted(df_audit["Sport"].unique()),
                                     key="audit_sport_filter")
        view_df = df_audit if sport_filter == "(all sports)" else df_audit[df_audit["Sport"] == sport_filter]

        # Stats row first
        info_rows = view_df[view_df["Severity"] == "INFO"]
        if not info_rows.empty:
            st.markdown("**Coverage**")
            st.dataframe(info_rows[["Sport","Detail"]], hide_index=True, use_container_width=True)

        # Then findings by severity
        for sev, colour in [("HIGH","#c53030"), ("MEDIUM","#d97706"), ("LOW","#6b7280")]:
            sev_rows = view_df[view_df["Severity"] == sev]
            if sev_rows.empty: continue
            st.markdown(f"<h4 style='color:{colour}'>{sev} ({len(sev_rows)})</h4>", unsafe_allow_html=True)
            st.dataframe(sev_rows[["Sport","Issue","Detail","Suggested_action"]],
                         hide_index=True, use_container_width=True)

        csv_download_button("Audit", df_audit, key="csv_audit")


# ===========================================================================
# TAB 3: FIX LIST
# ===========================================================================
with tab_fix:
    st.subheader("Athletes Needing Manual Reconciliation")
    st.caption("Cross-check between the BORNAN events export and the Athletes Details Shortlist. "
               "Most issues are DoB format or transliteration of Arabic names.")

    if sched_df.empty or shortlist_raw.empty:
        st.info("Need both KSA_ATHLETE_SCHEDULE and Athletes Details*.xlsx to detect mismatches.")
    else:
        # 1) Roster athletes with no SOTC value
        roster_unique = sched_df.groupby(["Given Name","Family Name"]).agg({
            "Date of Birth":"first","Sport":"first","SOTC":"first"
        }).reset_index()
        roster_no_sotc = roster_unique[roster_unique["SOTC"].astype(str) == ""]

        st.markdown(f"### A. In our roster, no SOTC match  ({len(roster_no_sotc)} athletes)")
        if not roster_no_sotc.empty:
            st.caption("These athletes are entered in events but don't have a corresponding row in the Shortlist. "
                       "Most likely: DoB mismatch or name spelling differs.")
            st.dataframe(roster_no_sotc[["Given Name","Family Name","Date of Birth","Sport"]],
                         hide_index=True, use_container_width=True)

        # 2) Shortlist SOTC athletes missing from roster
        sl = shortlist_raw.copy()
        sl_unique = sl.groupby("Full Name").agg({"SOTC":"first","Date Of Birth":"first","Sport":"first"}).reset_index()
        sl_sotc   = sl_unique[sl_unique["SOTC"].astype(str).str.upper() == "YES"].copy()

        # Match by DoB OR by name token superset
        roster_dobs   = set(roster_unique["Date of Birth"].astype(str).str[:10])
        roster_tokens = set()
        for _, r in roster_unique.iterrows():
            nm = f"{r['Given Name']} {r['Family Name']}".lower()
            roster_tokens.add(frozenset(w for w in nm.replace(",", " ").split() if len(w) > 1))

        def is_unmatched(row):
            dob = str(row.get("Date Of Birth", ""))[:10]
            if dob in roster_dobs: return False
            nm = str(row.get("Full Name", "")).lower()
            tokens = frozenset(w for w in nm.replace(",", " ").split() if len(w) > 1 and w.isalpha())
            return not any(tokens and tokens.issubset(rt) or rt.issubset(tokens) for rt in roster_tokens if rt)

        missing_sotc = sl_sotc[sl_sotc.apply(is_unmatched, axis=1)].copy()
        # Sanitise messy DoB (Excel serials)
        def fmt_dob(v):
            try:
                if isinstance(v, (int, float)) and not pd.isna(v):
                    return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(v))).strftime("%Y-%m-%d")
                return str(v)[:10]
            except Exception:
                return str(v)[:10]
        missing_sotc["Date Of Birth"] = missing_sotc["Date Of Birth"].apply(fmt_dob)

        st.markdown(f"### B. SOTC athletes from Shortlist missing from roster  ({len(missing_sotc)} athletes)")
        if not missing_sotc.empty:
            st.caption("These athletes appear in your SOTC Shortlist but aren't in the BORNAN events export. "
                       "Likely BORNAN hasn't picked them up yet, or there's a name/DoB typo in one file.")
            st.dataframe(missing_sotc[["Full Name","Date Of Birth","Sport"]],
                         hide_index=True, use_container_width=True)

        # ---- C. Events on roster but missing from the API schedule ----
        unm_files = sorted((RESULTS_DIR).glob("UNMATCHED_EVENTS_*.csv"))
        if unm_files:
            unm = pd.read_csv(unm_files[-1], encoding="utf-8-sig").fillna("")
            if not unm.empty:
                st.markdown(f"### C. KSA-entered events NOT on the GCC API schedule  ({len(unm)} rows)")
                st.caption("These athletes are entered for an event that the organisers haven't loaded onto the GCC API. "
                           "Either: (1) the event is genuinely not on the programme, or (2) BORNAN hasn't published it yet.")
                # Group by (Sport, Event) to give counts
                summary = unm.groupby(["Sport", "Event"]).agg(
                    Athletes=("Family Name", "count"),
                    Names=("Family Name", lambda s: ", ".join(sorted(set(f"{g} {f}".strip() for g, f in zip(unm.loc[s.index, "Given Name"], s)))[:5]))
                ).reset_index()
                st.dataframe(summary[["Sport", "Event", "Athletes", "Names"]],
                             hide_index=True, use_container_width=True)
                st.warning(
                    "**Action**: ask team management to escalate these to BORNAN / Doha 2026 organisers. "
                    "Most likely candidate is Men's Pole Vault (currently only Women's is on the API)."
                )

        st.divider()
        st.markdown("### How to fix")
        st.markdown("""
        For each row in section **A**: search the Shortlist for that DoB. If you find them, the names differ — note both spellings and we'll add a manual override.

        For each row in section **B**: check whether they actually have a BORNAN registration. If yes, the DoB or name in either file is wrong — fix the source.

        After fixing, just save the Excel file and re-run `python match_athletes.py` (or wait for the next hourly Actions run).
        """)


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.caption(
    f"Schedule {file_age(RESULTS_DIR, 'KSA_ATHLETE_SCHEDULE_*.csv')} · "
    f"Results {file_age(RESULTS_DIR, 'RESULTS_KSA_*.csv')} · "
    f"Medals {file_age(RESULTS_DIR, 'MEDALS_*.csv')}"
)
