"""
GCC Games Doha 2026 - Team Saudi competition dashboard.

Streamlit one-pager. Auto-reads the latest CSVs in data/ folders, so the
display always reflects the most recent scrape (manual or cloud-routine).

Run locally:
    streamlit run dashboard.py

Deploy:
    Push the gcc-games-doha-2026 repo to Streamlit Cloud (one click,
    point at dashboard.py).
"""
from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Team Saudi palette
# ---------------------------------------------------------------------------
ELITE     = "#235036"   # primary
ENABLER   = "#69c399"   # accent
DISCIPLINE = "#18342a"  # darkest
STAMINA   = "#c3d9d1"   # light
VICTORY   = "#ebce83"   # gold
LAVENDER  = "#9263aa"   # secondary
MALE_COL   = ELITE
FEMALE_COL = "#e69aaa"  # blush pink to match user's reference dashboard

HERE         = Path(__file__).parent
DATA         = HERE / "data"
RESULTS_DIR  = DATA / "results"
SCHEDULE_DIR = DATA / "schedule"
PHOTOS_DIR   = HERE / "photos"

st.set_page_config(
    page_title="GCC Games Doha 2026 — Team Saudi",
    page_icon="🇸🇦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# CSS — green header bar, tidy section spacing
# ---------------------------------------------------------------------------
st.markdown(f"""
<style>
.block-container {{padding-top: 1rem; padding-bottom: 1rem; max-width: 1600px;}}
h1, h2, h3 {{color: {DISCIPLINE};}}
.header-bar {{
    background: {ELITE}; color: white; padding: 1rem 1.5rem;
    border-radius: 6px; margin-bottom: 1rem;
}}
.header-bar h1 {{color: white; margin: 0; font-size: 1.6rem;}}
.metric-card {{
    background: #f8f9f8; padding: 0.8rem 1rem; border-radius: 6px;
    border-left: 4px solid {ENABLER};
}}
.metric-card .label {{font-size: 0.8rem; color: #555;}}
.metric-card .value {{font-size: 1.5rem; color: {DISCIPLINE}; font-weight: 600;}}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data loaders (cached)
# ---------------------------------------------------------------------------
def _latest(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern))
    return files[-1] if files else None


@st.cache_data(ttl=60)
def load_athlete_schedule() -> pd.DataFrame:
    f = _latest(RESULTS_DIR, "KSA_ATHLETE_SCHEDULE_*.csv")
    if not f: return pd.DataFrame()
    df = pd.read_csv(f, encoding="utf-8-sig", dtype=str).fillna("")
    df["Athlete"] = (df["Given Name"] + " " + df["Family Name"]).str.strip()
    df["Date"]    = pd.to_datetime(df["Date"], errors="coerce")
    return df


@st.cache_data(ttl=60)
def load_medals() -> pd.DataFrame:
    f = _latest(RESULTS_DIR, "MEDALS_*.csv")
    if not f: return pd.DataFrame()
    return pd.read_csv(f, encoding="utf-8-sig")


@st.cache_data(ttl=60)
def load_results_ksa() -> pd.DataFrame:
    f = _latest(RESULTS_DIR, "RESULTS_KSA_*.csv")
    if not f: return pd.DataFrame()
    df = pd.read_csv(f, encoding="utf-8-sig", dtype=str).fillna("")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df


@st.cache_data(ttl=60)
def load_schedule() -> pd.DataFrame:
    f = _latest(SCHEDULE_DIR, "SCHEDULE_*.csv")
    if not f: return pd.DataFrame()
    df = pd.read_csv(f, encoding="utf-8-sig", dtype=str).fillna("")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
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


def gender_from_event(event: str) -> str:
    e = (event or "").lower()
    if "women" in e:       return "Female"
    if "mixed" in e:       return "Mixed"
    if "men"   in e:       return "Male"
    return "Mixed"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
sched_df  = load_athlete_schedule()
medals_df = load_medals()
results_df = load_results_ksa()

ksa_medals = medals_df[medals_df["NOC"] == "KSA"].iloc[0] if not medals_df.empty else None
total_gold   = int(ksa_medals["Gold"])   if ksa_medals is not None else 0
total_silver = int(ksa_medals["Silver"]) if ksa_medals is not None else 0
total_bronze = int(ksa_medals["Bronze"]) if ksa_medals is not None else 0
ksa_rank     = int(ksa_medals["Rank"])   if ksa_medals is not None else "—"

today = pd.Timestamp.today().normalize()
today_events = sched_df[sched_df["Date"] == today] if not sched_df.empty else pd.DataFrame()
next_events  = sched_df[sched_df["Date"] >= today].sort_values(["Date", "Time Start"]) if not sched_df.empty else pd.DataFrame()

st.markdown(f"""
<div class="header-bar">
  <h1>🇸🇦 GCC Games Doha 2026 — Team Saudi</h1>
  <div style="opacity:0.9;margin-top:0.2rem;">Live competition dashboard · Last data refresh: {file_age(RESULTS_DIR, 'KSA_ATHLETE_SCHEDULE_*.csv')}</div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Top metrics
# ---------------------------------------------------------------------------
m1, m2, m3, m4, m5 = st.columns(5)
m1.markdown(f"<div class='metric-card'><div class='label'>Gold</div><div class='value'>🥇 {total_gold}</div></div>", unsafe_allow_html=True)
m2.markdown(f"<div class='metric-card'><div class='label'>Silver</div><div class='value'>🥈 {total_silver}</div></div>", unsafe_allow_html=True)
m3.markdown(f"<div class='metric-card'><div class='label'>Bronze</div><div class='value'>🥉 {total_bronze}</div></div>", unsafe_allow_html=True)
m4.markdown(f"<div class='metric-card'><div class='label'>Medal Table Rank</div><div class='value'>#{ksa_rank}</div></div>", unsafe_allow_html=True)

n_athletes = sched_df.groupby(["Given Name", "Family Name"]).ngroups if not sched_df.empty else 0
n_events   = sched_df["Event_ID"].nunique() if not sched_df.empty else 0
m5.markdown(f"<div class='metric-card'><div class='label'>Athletes / Events Today</div><div class='value'>{len(today_events.groupby(['Given Name','Family Name'])) if not today_events.empty else 0} / {len(today_events)}</div></div>", unsafe_allow_html=True)

st.write("")

# ---------------------------------------------------------------------------
# Row 1: athlete gender split + medal table + today's events
# ---------------------------------------------------------------------------
c1, c2, c3 = st.columns([1, 1.2, 1.4])

with c1:
    st.subheader("Athletes")
    if not sched_df.empty:
        unique = sched_df.groupby(["Given Name", "Family Name"]).first().reset_index()
        unique["Gender"] = unique["Event"].apply(gender_from_event)
        counts = unique["Gender"].value_counts()
        m = int(counts.get("Male", 0)); f = int(counts.get("Female", 0))
        total = m + f
        fig = go.Figure(go.Pie(
            labels=["Male", "Female"],
            values=[m, f],
            hole=0.65,
            marker=dict(colors=[MALE_COL, FEMALE_COL]),
            sort=False,
            textinfo="label+value",
            textfont=dict(size=14, color="white"),
        ))
        fig.update_layout(
            showlegend=False,
            annotations=[dict(text=f"<b>{total}</b><br>Athletes", x=0.5, y=0.5,
                              font_size=18, showarrow=False)],
            margin=dict(t=10, b=10, l=10, r=10),
            height=260,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"M {m} ({m/max(total,1)*100:.0f}%) · F {f} ({f/max(total,1)*100:.0f}%)")
    else:
        st.info("No athlete data yet.")

with c2:
    st.subheader("Medal Table")
    if not medals_df.empty:
        m_show = medals_df.copy()
        m_show["Country"] = m_show.apply(
            lambda r: f"**{r['Country']}**" if r["NOC"] == "KSA" else r["Country"], axis=1)
        m_show = m_show[["Rank", "NOC", "Country", "Gold", "Silver", "Bronze", "Total"]]
        st.dataframe(m_show, hide_index=True, use_container_width=True, height=260)
    else:
        st.info("Medal table not loaded.")

with c3:
    st.subheader("Today's Events")
    if not today_events.empty:
        show = today_events.sort_values("Time Start")[
            ["Time Start", "Time End", "Sport", "Event", "Phase", "Athlete", "Venue"]
        ].head(20).rename(columns={"Time Start": "Start", "Time End": "End"})
        st.dataframe(show, hide_index=True, use_container_width=True, height=260)
    else:
        if not next_events.empty:
            nxt = next_events.iloc[0]
            days = (nxt["Date"] - today).days
            st.info(f"**Next event in {days}d**: {nxt['Sport']} — {nxt['Event']} ({nxt['Athlete']}) on {nxt['Date'].strftime('%a %d %b')}")
        else:
            st.info("No upcoming events.")

st.divider()

# ---------------------------------------------------------------------------
# Row 2: Athletes by sport, stacked M/F
# ---------------------------------------------------------------------------
st.subheader("KSA Athletes by Sport")
if not sched_df.empty:
    unique = sched_df.groupby(["Given Name", "Family Name"]).first().reset_index()
    unique["Gender"] = unique["Event"].apply(gender_from_event)
    by_sport = unique.groupby(["Sport", "Gender"]).size().reset_index(name="n")
    pv = by_sport.pivot(index="Sport", columns="Gender", values="n").fillna(0)
    pv["Total"] = pv.sum(axis=1)
    pv = pv.sort_values("Total", ascending=True)

    fig = go.Figure()
    if "Male" in pv.columns:
        fig.add_trace(go.Bar(y=pv.index, x=pv["Male"], name="Male",
                              orientation="h", marker_color=MALE_COL,
                              text=pv["Male"].astype(int), textposition="inside"))
    if "Female" in pv.columns:
        fig.add_trace(go.Bar(y=pv.index, x=pv["Female"], name="Female",
                              orientation="h", marker_color=FEMALE_COL,
                              text=pv["Female"].astype(int), textposition="inside"))
    fig.update_layout(
        barmode="stack", height=420, margin=dict(t=10, b=20, l=10, r=10),
        legend=dict(orientation="h", y=1.08),
        xaxis_title="", yaxis_title="",
        plot_bgcolor="white",
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No athlete data.")

# ---------------------------------------------------------------------------
# Row 3: schedule heatmap (Sport × Date)
# ---------------------------------------------------------------------------
st.subheader("KSA Schedule — Sport × Date")
if not sched_df.empty:
    grid = sched_df.groupby(["Sport", "Date"]).size().reset_index(name="n")
    dates = sorted(sched_df["Date"].dropna().unique())
    sports = sorted(sched_df["Sport"].unique())
    z = []
    for s in sports:
        row = []
        for d in dates:
            cell = grid[(grid["Sport"] == s) & (grid["Date"] == d)]
            row.append(int(cell["n"].iloc[0]) if not cell.empty else 0)
        z.append(row)

    fig = go.Figure(go.Heatmap(
        z=z,
        x=[pd.Timestamp(d).strftime("%a %d %b") for d in dates],
        y=sports,
        colorscale=[[0, "#f4f7f5"], [0.2, STAMINA], [0.6, ENABLER], [1, ELITE]],
        showscale=False,
        text=[[str(v) if v > 0 else "" for v in row] for row in z],
        texttemplate="%{text}", textfont={"color": "white", "size": 12},
    ))
    fig.update_layout(height=max(280, 28 * len(sports)),
                      margin=dict(t=10, b=10, l=10, r=10),
                      xaxis_side="top")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No schedule data.")

# ---------------------------------------------------------------------------
# Row 4: Recent KSA results
# ---------------------------------------------------------------------------
st.subheader("KSA Recent Results")
if not results_df.empty and "Athlete" in results_df.columns:
    df_r = results_df.copy()
    cols = [c for c in ["Date", "Sport", "Discipline", "Phase", "Athlete", "Rank", "Result", "Medal"] if c in df_r.columns]
    df_r = df_r[cols].sort_values("Date", ascending=False)
    st.dataframe(df_r, hide_index=True, use_container_width=True, height=320)
else:
    st.info("No KSA results recorded yet. The table will populate as competitions run.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.caption(
    f"Data refresh: KSA schedule {file_age(RESULTS_DIR, 'KSA_ATHLETE_SCHEDULE_*.csv')} · "
    f"Results {file_age(RESULTS_DIR, 'RESULTS_KSA_*.csv')} · "
    f"Medals {file_age(RESULTS_DIR, 'MEDALS_*.csv')}"
)
