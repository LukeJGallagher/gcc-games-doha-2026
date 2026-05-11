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
FEMALE_COL = "#e69aaa"

SPORT_COLOURS = {
    "Athletics": ELITE,
    "Swimming":  "#2a76b8",
    "Taekwondo": LAVENDER,
    "Karate":    VICTORY,
}

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
# CSS
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
[data-baseweb="tab"] {{font-weight: 600;}}
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
def load_results_ksa() -> pd.DataFrame:
    f = _latest(RESULTS_DIR, "RESULTS_KSA_*.csv")
    if not f: return pd.DataFrame()
    df = pd.read_csv(f, encoding="utf-8-sig", dtype=str).fillna("")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df


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
st.markdown(f"""
<div class="header-bar">
  <h1>🇸🇦 GCC Games Doha 2026 — Team Saudi</h1>
  <div style="opacity:0.9;margin-top:0.2rem;">Last data refresh: {file_age(RESULTS_DIR, 'KSA_ATHLETE_SCHEDULE_*.csv')}</div>
</div>
""", unsafe_allow_html=True)

tab_overview, tab_plan, tab_fix = st.tabs(["📊 Overview", "📅 PA Coverage Plan", "🛠 Fix List"])


# ===========================================================================
# TAB 1: OVERVIEW
# ===========================================================================
with tab_overview:
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
    if not sched_df.empty:
        grid = sched_df.groupby(["Sport","Date"]).size().reset_index(name="n")
        dates = sorted(sched_df["Date"].dropna().unique())
        sports = sorted(sched_df["Sport"].unique())
        z = [[int(grid[(grid["Sport"]==s)&(grid["Date"]==d)]["n"].iloc[0])
              if not grid[(grid["Sport"]==s)&(grid["Date"]==d)].empty else 0
              for d in dates] for s in sports]
        fig = go.Figure(go.Heatmap(z=z,
            x=[pd.Timestamp(d).strftime("%a %d %b") for d in dates], y=sports,
            colorscale=[[0,"#f4f7f5"],[0.2,STAMINA],[0.6,ENABLER],[1,ELITE]], showscale=False,
            text=[[str(v) if v>0 else "" for v in row] for row in z],
            texttemplate="%{text}", textfont={"color":"white","size":12}))
        fig.update_layout(height=max(280, 28*len(sports)), margin=dict(t=10, b=10, l=10, r=10), xaxis_side="top")
        st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# TAB 2: PA COVERAGE PLAN
# ===========================================================================
with tab_plan:
    st.subheader("Performance Analysis — Coverage Plan")
    st.caption("Target: SOTC athletes in Athletics, Swimming, Taekwondo and Karate. "
               "Cameras: 2 fixed + 3rd camera arriving **2026-05-14**.")

    # Settings row
    s1, s2, s3 = st.columns(3)
    target_sports = s1.multiselect(
        "Target sports",
        options=sorted(sched_df["Sport"].unique()) if not sched_df.empty else [],
        default=[s for s in ["Athletics","Swimming","Taekwondo","Karate"]
                 if not sched_df.empty and s in sched_df["Sport"].unique()],
    )
    sotc_only = s2.checkbox("SOTC athletes only", value=True)
    show_staff = s3.checkbox("Show staff allocation", value=True)

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

        # Time columns: prefer manual Shortlist times if available, fall back to API+duration
        plan_df["TS"] = pd.to_datetime(plan_df["Date"].dt.strftime("%Y-%m-%d") + " " + plan_df["Time Start"],
                                       errors="coerce")
        plan_df["TE"] = pd.to_datetime(plan_df["Date"].dt.strftime("%Y-%m-%d") + " " + plan_df["Time End"],
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

        # Allocate to "Camera 1/2/3" greedily per day so the Gantt rows = camera rows
        assignments = []
        for d, g in plan_df.groupby("Date"):
            ends = {}  # camera_id -> end time
            cams_available = 3 if d >= pd.Timestamp("2026-05-14") else 2
            for _, ev in g.sort_values("TS").iterrows():
                slot = None
                for cam in range(1, cams_available + 1):
                    if cam not in ends or ends[cam] <= ev["TS"]:
                        slot = cam
                        break
                if slot is None:
                    # overflow - new "virtual" camera (flag)
                    slot = max(ends.keys(), default=0) + 1
                ends[slot] = ev["TE"]
                assignments.append((ev.name, slot, slot > cams_available))
        plan_df["Camera"] = pd.Series({i: s for i, s, _ in assignments})
        plan_df["Overflow"] = pd.Series({i: o for i, _, o in assignments})

        # Gantt: one row per camera per day → visual rows = "Day · Cam N"
        plan_df["GanttRow"] = plan_df["DayStr"] + " · Cam " + plan_df["Camera"].astype(str)

        fig = px.timeline(
            plan_df,
            x_start="TS", x_end="TE",
            y="GanttRow",
            color="Sport",
            text="Label",
            color_discrete_map=SPORT_COLOURS,
            hover_data={"Athlete": True, "Phase": True, "Venue": True, "Time Start": True, "Time End": True,
                        "Time_Source": True, "TS": False, "TE": False, "GanttRow": False},
        )
        fig.update_yaxes(autorange="reversed", title="")
        fig.update_xaxes(title="")
        fig.update_traces(textposition="inside", textfont_size=10)
        fig.update_layout(height=max(400, 22 * plan_df["GanttRow"].nunique()),
                          margin=dict(t=10, b=10, l=10, r=10), plot_bgcolor="white",
                          legend=dict(orientation="h", y=1.05))
        st.plotly_chart(fig, use_container_width=True)

        # Overflow warning
        overflows = plan_df[plan_df["Overflow"] == True]
        if not overflows.empty:
            st.error(f"⚠ {len(overflows)} events overflow the available camera count for that day. "
                     f"Manual prioritisation needed:")
            st.dataframe(overflows[["Date","Time Start","Sport","Event","Phase","Athlete"]],
                         hide_index=True, use_container_width=True)

        st.divider()
        st.subheader("Day-by-day schedule")
        # Daily breakdown
        for d, g in plan_df.groupby("Date"):
            cams_today = 3 if d >= pd.Timestamp("2026-05-14") else 2
            with st.expander(f"**{d.strftime('%a %d %b %Y')}** — {len(g)} events · {cams_today} cameras"):
                show = g.sort_values("TS")[["Time Start","Time End","Sport","Phase","Athlete","Venue","Camera","SOTC"]]
                st.dataframe(show, hide_index=True, use_container_width=True)

        st.divider()
        st.subheader("Athlete coverage matrix")
        # athlete × date count
        mat = plan_df.groupby(["Athlete","Date"]).size().reset_index(name="n")
        pv = mat.pivot(index="Athlete", columns="Date", values="n").fillna(0).astype(int)
        pv.columns = [pd.Timestamp(c).strftime("%a %d") for c in pv.columns]
        st.dataframe(pv, use_container_width=True)
    else:
        st.info("Select at least one target sport to build the plan.")


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
