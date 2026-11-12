"""Command Center (FinOps landing page): what is happening across the demo week, as of the selected day."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

from datetime import date, timedelta

import altair as alt
import streamlit as st

from detective.config import get_settings
from detective.detect import detect
from detective.ledger import estimate_usd
from detective.models import VERDICT_LABELS
from detective.sources import FixtureSource
from evals.score import proof, score_case, summarise

ui.require_page("console")
s = get_settings()
names = ui.scenario_names()


@st.cache_data(show_spinner=False)
def measure(day: str) -> dict:
    d = detect(FixtureSource(day))
    return {"day": day, "date": d.spike_date, "spend": d.spike_total, "expected": d.baseline_mean,
            "low": min(d.baseline_days.values()), "high": max(d.baseline_days.values()),
            "pct": d.increase_pct, "material": d.material, "drivers": [
                {"title": x.title, "usd": x.daily_increase, "share": x.share_pct} for x in d.drivers]}


by_date = {measure(d)["date"]: d for d in names}
first = min(date.fromisoformat(x) for x in by_date)
last = max(date.fromisoformat(x) for x in by_date)
first_monday, last_monday = first - timedelta(days=first.weekday()), last - timedelta(days=last.weekday())
selected_date = date.fromisoformat(measure(st.session_state["scenario"])["date"])
st.session_state.setdefault("week_start", (selected_date - timedelta(days=selected_date.weekday())).isoformat())
monday = date.fromisoformat(st.session_state["week_start"])
if not monday <= selected_date < monday + timedelta(days=7):  # the day was changed on another page
    monday = selected_date - timedelta(days=selected_date.weekday())
    st.session_state["week_start"] = monday.isoformat()


def move(days: int) -> None:
    st.session_state["week_start"] = (monday + timedelta(days=days)).isoformat()
    in_week = [by_date[k] for k in sorted(by_date) if monday + timedelta(days=days) <= date.fromisoformat(k)
               < monday + timedelta(days=days + 7)]
    if in_week:
        st.session_state["scenario"] = in_week[-1]


def pick(name: str) -> None:
    st.session_state["scenario"] = name


head, days_col, action = st.columns([1, 2.6, 0.75], vertical_alignment="bottom")
with days_col:
    cols = st.columns([0.55] + [1] * 7 + [0.55], gap="small", vertical_alignment="center")
    cols[0].button("◀", key="week_prev", disabled=monday <= first_monday, on_click=move, args=(-7,),
                   help="Previous week", use_container_width=True)
    for i in range(7):
        d = monday + timedelta(days=i)
        name = by_date.get(d.isoformat())
        cols[i + 1].button(f"{d:%a} {d:%d}", key=f"day_{d.isoformat()}", disabled=name is None,
                           type="primary" if name == st.session_state["scenario"] else "secondary",
                           on_click=pick if name else None, args=(name,) if name else None, use_container_width=True)
    cols[-1].button("▶", key="week_next", disabled=monday >= last_monday, on_click=move, args=(7,),
                    help="Next week", use_container_width=True)
    st.markdown(f"<div class='muted' style='text-align:center'>Week of {monday:%d %b} – "
                f"{monday + timedelta(days=6):%d %b %Y}</div>", unsafe_allow_html=True)
scenario = st.session_state["scenario"]
with head:
    ui.header("Command Center", "Command Center", ui.day_short(scenario))

all_days = [measure(d) for d in names]
today = next(m for m in all_days if m["day"] == scenario)
week = [m for m in all_days if monday <= date.fromisoformat(m["date"]) < monday + timedelta(days=7)]
upto = [m for m in all_days if m["date"] <= today["date"]]


def start() -> None:
    st.session_state["pending_run"] = {"scenario": scenario, "mode": ui.run_mode()}
    st.switch_page("views/investigation.py")


est = estimate_usd(s.model_fast, len(today["drivers"]))


@st.dialog("Start investigation")
def confirm() -> None:
    st.markdown(f"Claude on Amazon Bedrock will investigate {len(today['drivers'])} cost driver(s) for "
                f"{ui.day_short(scenario)}.")
    ui.kv([("Estimated cost", f"${est:.2f}"), ("Model", ui.model_label(s.model_fast)),
           ("Spending guard", f"${s.cost_cap_usd:.0f}")])
    if st.button("Start", type="primary", disabled=not st.checkbox(f"I approve spending about ${est:.2f}")):
        start()


with action:
    if today["material"]:
        if st.button("Investigate", type="primary", help=f"Estimated cost ${est:.2f}", use_container_width=True):
            confirm() if ui.run_mode() == "bedrock" else start()
        st.markdown(f"<div class='muted' style='text-align:center'>Estimated cost ${est:.2f}</div>",
                    unsafe_allow_html=True)
stores = {st_.meta()["scenario_id"]: st_ for st_ in ui.demo_stores()}
cases = [c for d in names if d in stores and measure(d)["date"] <= today["date"] for c in stores[d].cases()]
open_cases = [c for c in cases if c.status == "awaiting_owner"]
evidence = proof([stores[d] for d in stores if measure(d)["date"] <= today["date"]],
                 [h for h in ui.history_stores() if measure(h.meta()["scenario_id"])["date"] <= today["date"]])
summary = summarise([score_case(c) for c in cases]) if cases else {}

# --- headline tiles ------------------------------------------------------------------------------------------
per_day_open = [sum(1 for c in (stores[m["day"]].cases() if m["day"] in stores else []) if c.status == "awaiting_owner")
                for m in upto]
avoid = [sum(c.report.avoidable_per_day for c in (stores[m["day"]].cases() if m["day"] in stores else [])
             if c.report and c.status != "fixed") for m in upto]
tiles = [
    ("Spend today", ui.money(today["spend"]), f"{today['pct']:+.0f}% vs expected", [m["spend"] for m in upto], "inverse"),
    ("Open incidents", str(len(open_cases)), f"{per_day_open[-1]} new today", per_day_open, "inverse"),
    ("Avoidable per day", ui.money(sum(c.report.avoidable_per_day for c in open_cases if c.report)), None, avoid, "off"),
    ("Saving confirmed", f"{ui.money(evidence['proven_per_day'])}/day", f"≈ {ui.money(evidence['proven_per_day'] * 365)}/yr",
     [sum(x["saving_usd"] or 0 for x in evidence["savings"] if x["confirmed"] and measure(x["day"])["date"] <= m["date"])
      for m in upto], "normal"),
    ("Investigations", str(len(cases)),
     f"{summary.get('avg_seconds', 0):.0f} s · ${summary.get('avg_cost_usd', 0):.3f} each" if summary else None,
     [len(stores[m["day"]].cases()) if m["day"] in stores else 0 for m in upto], "off"),
]
def spark(values: list[float], color: str) -> alt.Chart:
    pts = [{"i": i, "v": v} for i, v in enumerate(values)]
    base = alt.Chart(alt.Data(values=pts)).encode(x=alt.X("i:Q", axis=None),
                                                  y=alt.Y("v:Q", axis=None, scale=alt.Scale(zero=False)))
    return (base.mark_area(color=color, opacity=.18, interpolate="monotone")
            + base.mark_line(color=color, strokeWidth=2, interpolate="monotone")
            + base.mark_circle(color=color, size=28).transform_filter(f"datum.i == {len(values) - 1}")).properties(
        height=58, width="container").configure_view(strokeWidth=0)


tone = {"inverse": "#D13212", "normal": "#037F0C", "off": ui.BLUE}
for i, (col, (label, value, delta, chart, color)) in enumerate(zip(st.columns(5), tiles)):
    with col.container(border=True, key=f"tint_slate_tile_{i}"):
        left, right = st.columns([1.25, 1], vertical_alignment="center")
        left.markdown(f"<div class='label'>{label}</div><div style='font-size:1.3rem;font-weight:800;color:#16191F'>"
                      f"{value}</div><div class='muted' style='font-size:.78rem'>{delta or '&nbsp;'}</div>",
                      unsafe_allow_html=True)
        if chart and len(chart) > 1:
            right.altair_chart(spark(chart, tone[color]), use_container_width=True)

# --- main row ------------------------------------------------------------------------------------------------
clock = date.fromisoformat(today["date"])
AVOID = {"likely_waste", "unbudgeted", "oversized"}
feed, candles, flow = st.columns([1.3, 1.25, 1.05])
with feed, ui.card("Incidents · last 24 hours", height=430, tint="sand"):
    recent = [c for c in cases if date.fromisoformat(measure(c.scenario_id)["date"]) >= clock - timedelta(days=1)]
    live = any(c.status == "awaiting_owner" for c in recent)
    st.markdown(f"<div class='muted'>{'<span class=live></span>' if live else ''}As of {ui.day_short(scenario)} · "
                f"{len(recent)} incident(s)</div>", unsafe_allow_html=True)
    rows = []
    for c in sorted(recent, key=lambda c: (c.report.cost_start if c.report else c.driver.start_date), reverse=True):
        r = c.report
        when = (r.cost_start if r else c.driver.start_date)
        rows.append(f"<tr><td class='t'>{when[5:10].replace('-', '/')} {when[11:16]}</td>"
                    f"<td>{ui.md(c.driver.title)}<span class='d'>{c.case_id} · {ui.actor_name(c.assigned_to)} · "
                    f"{ui.STATUS_LABELS[c.status]}</span></td>"
                    f"<td>{ui.pill(r.verdict) if r else 'Needs review'}</td>"
                    f"<td class='m'>{ui.money(c.driver.daily_increase)}/day</td></tr>")
    if scenario not in stores and today["material"]:
        for d in today["drivers"]:
            rows.insert(0, f"<tr><td class='t'>{today['date'][5:].replace('-', '/')}</td><td>{ui.md(d['title'])}"
                           f"<span class='d'>Detected · not investigated yet</span></td><td><span class='pill' "
                           f"style='background:#5F6B7A'>New</span></td><td class='m'>{ui.money(d['usd'])}/day</td></tr>")
    st.markdown(f"<table class='feed'>{''.join(rows) or '<tr><td>No incidents in the last 24 hours.</td></tr>'}</table>",
                unsafe_allow_html=True)

with candles, ui.card("Daily spend candles · this week", height=430, tint="blue"):
    data = [{"Day": ui.day_short(m["day"]), "o": i, "Low": m["low"], "High": max(m["high"], m["spend"]),
             "Expected": m["expected"], "Actual": m["spend"], "Up": m["spend"] > m["expected"],
             "Selected": m["day"] == scenario} for i, m in enumerate(week)]
    base = alt.Chart(alt.Data(values=data)).encode(
        x=alt.X("Day:N", sort=alt.SortField("o"), title=None, axis=alt.Axis(labelAngle=0, labelFontSize=10)))
    color = alt.condition("datum.Up", alt.value("#D13212"), alt.value("#037F0C"))
    wick = base.mark_rule(strokeWidth=1.5).encode(y=alt.Y("Low:Q", title="USD / day", scale=alt.Scale(zero=False)),
                                                  y2="High:Q", color=color)
    body = base.mark_bar(size=18, cornerRadius=2).encode(
        y="Expected:Q", y2="Actual:Q", color=color,
        tooltip=["Day:N", alt.Tooltip("Expected:Q", format="$,.0f"), alt.Tooltip("Actual:Q", format="$,.0f")])
    sel = base.mark_text(dy=-12, fontWeight="bold", color=ui.NAVY, text="▼").encode(y="High:Q").transform_filter(
        "datum.Selected")
    st.altair_chart((wick + body + sel).properties(height=335), use_container_width=True)
    ui.muted("Each candle runs from expected to actual spend; the thin line is the normal range of the last 4 weeks. "
             "Red: above expected.")

with flow, ui.card("Where the increase went", height=430, tint="orange"):
    lines, n = [], 0
    groups = {"avoid": [], "planned": [], "unclear": []}
    for c in cases:
        if c.report:
            g = "avoid" if c.report.verdict in AVOID else "planned" if c.report.verdict == "expected" else "unclear"
            groups[g].append(c)
    bands = {"avoid": (0.55, 1.0), "planned": (0.15, 0.48), "unclear": (0.0, 0.1)}
    totals = {g: sum(c.driver.daily_increase for c in cs) for g, cs in groups.items()}
    for g, cs in groups.items():
        strands = [(c, r) for c in cs for r in c.driver.resource_ids]
        lo, hi = bands[g]
        for k, (c, rid) in enumerate(strands):
            end = lo + (hi - lo) * ((k + 0.5) / max(len(strands), 1))
            w = c.driver.daily_increase / len(c.driver.resource_ids)
            for j in range(13):
                x = j / 12
                lines.append({"id": n, "x": x, "y": 0.5 + (end - 0.5) * (1 - (1 - x) ** 3), "g": g, "w": w})
            n += 1
    total = sum(totals.values()) or 1
    if lines:
        palette = alt.Scale(domain=["avoid", "planned", "unclear"], range=["#D13212", "#0972D3", "#879596"])
        fan = alt.Chart(alt.Data(values=lines)).mark_line(opacity=.35, interpolate="basis").encode(
            x=alt.X("x:Q", axis=None), y=alt.Y("y:Q", axis=None, scale=alt.Scale(domain=[-0.05, 1.05])),
            detail="id:N", color=alt.Color("g:N", scale=palette, legend=None),
            strokeWidth=alt.StrokeWidth("w:Q", legend=None, scale=alt.Scale(range=[1, 6])))
        origin = alt.Chart(alt.Data(values=[{"x": 0, "y": 0.5}])).mark_circle(size=80, color="#16191F").encode(
            x="x:Q", y="y:Q")
        names = {"avoid": "avoidable", "planned": "planned, no alert", "unclear": "unexplained"}
        labels = [{"x": 1.02, "y": sum(bands[g]) / 2, "g": g, "t": f"{totals[g] / total:.0%} {names[g]}"}
                  for g in groups if totals[g]]
        text = alt.Chart(alt.Data(values=labels)).mark_text(align="left", fontSize=13, fontWeight="bold").encode(
            x="x:Q", y="y:Q", text="t:N", color=alt.Color("g:N", scale=palette, legend=None))
        st.altair_chart((fan + origin + text).properties(height=335, padding={"right": 120}),
                        use_container_width=True)
        ui.muted(f"From the spikes to their outcome: one strand per resource, thicker = more $/day · "
                 f"{ui.money(total)}/day in total")
    else:
        st.markdown("No investigated incidents yet.")

# --- bottom row: plain-text briefing -----------------------------------------------------------------------
today_cases = stores[scenario].cases() if scenario in stores else []
counts = {}
for c in today_cases:
    if c.report:
        counts[c.report.verdict] = counts.get(c.report.verdict, 0) + 1
c1, c2, c3, c4 = st.columns(4)
with c1, ui.card("Today in brief", height=255, tint="sand"):
    lines = [(f"Spend is **{ui.money(today['spend'])}**, {today['pct']:+.0f}% against a normal "
             f"{ui.money(today['expected'])}.")]
    if not today["material"]:
        lines.append("That is within the normal range, so nothing was flagged and nobody was alerted.")
    elif not today_cases:
        lines.append(f"{len(today['drivers'])} cost driver(s) found, not investigated yet. Use **Investigate** above.")
    else:
        parts = [f"{n} {VERDICT_LABELS[v].lower()}" for v, n in counts.items()]
        lines.append(f"{len(today_cases)} cost driver(s) investigated: " + ", ".join(parts) + ".")
        quiet = [c for c in today_cases if c.status == "quiet"]
        if quiet:
            lines.append(f"{len(quiet)} planned change(s) stayed quiet, so no one was paged for approved work.")
    for line in lines:
        st.markdown(ui.md(line))
with c2, ui.card("Needs a decision", height=255, tint="orange"):
    if open_cases:
        for c in sorted(open_cases, key=lambda c: -c.driver.daily_increase)[:4]:
            fix = {"stop_instances": "approve a stop", "s3_gateway_endpoint": "approve a code fix"}.get(
                c.report.proposed_fix if c.report else None, "confirm or re-route")
            st.markdown(ui.md(f"**{ui.actor_name(c.assigned_to)}**: {fix} for {c.driver.title.lower()} "
                              f"({ui.money(c.driver.daily_increase)}/day)"))
    else:
        st.markdown("Nothing is waiting for a decision.")
with c3, ui.card("This week so far", height=255, tint="green"):
    prs = evidence["pull_requests"]
    for line in [f"**{ui.money(evidence['found_per_day'])}/day** of unplanned spend explained.",
                 f"**{ui.money(evidence['proven_per_day'])}/day** saving confirmed on the next bill.",
                 f"**{len(prs)}** code fix(es) written by Devin as pull requests.",
                 f"**{evidence['actions_without_approval']}** actions taken without an owner's approval."]:
        st.markdown(ui.md(line))
with c4, ui.card("Today's cost drivers", height=255, tint="blue"):
    if not today["material"]:
        st.markdown("Below the alert threshold. Nothing to investigate.")
    else:
        for d in today["drivers"][:4]:
            st.markdown(ui.md(f"**{ui.money(d['usd'])}/day** · {d['title']} ({d['share']:.0f}% of the increase)"))
