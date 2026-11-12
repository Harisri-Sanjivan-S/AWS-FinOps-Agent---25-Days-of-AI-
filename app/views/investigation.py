"""Investigation: progress, the incident timeline, an evidence map, the agent's notes beside each tool call, the
four checks and what was ignored on purpose."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import altair as alt
import streamlit as st

from detective.models import VERDICT_LABELS, Case
from detective.pipeline import investigate_driver, make_agents, start_run
from detective.sources import FixtureSource

ui.require_page("investigation")
STEP_NAMES = {"get_bill_lines": "Checked the bill", "lookup_change_events": "Looked for recent changes",
              "get_tags": "Checked who owns it", "find_ticket": "Looked for an approved request",
              "check_exception": "Checked for an agreed exception", "get_budget": "Checked the budget",
              "get_utilization": "Checked whether it is being used", "get_deployment": "Read the release record",
              "get_traffic_destinations": "Checked where the network traffic goes"}
EVENTS = {"RunInstances": "New servers launched", "UpdateService": "Service release", "AuthorizeSecurityGroupIngress":
          "Network security rule changed", "CreateDeployment": "Release", "UpdateFunctionCode": "Function update",
          "ModifyInstanceAttribute": "Server settings changed", "CreateTags": "Labels changed"}
METRICS = {"nvidia_smi_utilization_gpu": "GPU use (%)", "CPUUtilization": "Processor use (%)",
           "BytesOutToDestination": "Network traffic (GB per hour)", "BytesOutToSource": "Network traffic (GB per hour)"}


def plain_event(rec: dict) -> str:
    return f"{EVENTS.get(rec['EventName'], 'Change')} by {rec['Username']} at {rec['EventTime'][11:16]} UTC"


STEPS = [("Detect", "driver detected"), ("Investigate", "findings returned"), ("Verify", "findings returned"),
         ("Decide", "decision"), ("Write", "explanation returned")]


def label(case: Case) -> str:
    r = case.report
    return f"{case.case_id} · {case.driver.title} · {ui.money(case.driver.daily_increase)}/day · " + (
        VERDICT_LABELS[r.verdict] if r else "Needs review")


def progress(case: Case) -> str:
    events = {a.event for a in case.audit}
    done = [ev in events for _, ev in STEPS]
    if case.validation_errors:
        done[2] = False
    return "".join(f"<span class='chip {'on' if ok else 'off'}'>{name}</span>" for (name, _), ok in zip(STEPS, done))


def timeline(case: Case, source: FixtureSource) -> None:
    ids = (case.findings.usage_evidence_ids if case.findings else []) or \
        [e for c in case.tool_calls if c.name == "get_utilization" for e in c.evidence_ids]
    rec = next((source.record(i) for i in ids if source.record(i) and source.record(i).get("values")), None)
    if not rec:
        st.markdown("No usage series was retrieved for this case.")
        return
    unit = rec.get("unit", "")
    scale = 1e9 if unit == "Bytes" else 1
    series = [{"Time": t, "Value": v / scale} for t, v in zip(rec["timestamps"], rec["values"])]
    marks = []
    r = case.report
    if r and r.primary_change_id and source.record(r.primary_change_id):
        ev = source.record(r.primary_change_id)
        marks.append({"Time": ev["EventTime"], "What": EVENTS.get(ev["EventName"], "Change")})
    if r:
        marks.append({"Time": r.cost_start, "What": "Cost started"})
    y_title = METRICS.get(rec.get("metric_name", ""), "GB per hour" if unit == "Bytes" else "Use (%)")
    line = alt.Chart(alt.Data(values=series)).mark_area(
        line={"color": ui.BLUE, "strokeWidth": 2}, color=ui.BLUE, opacity=.15, interpolate="monotone").encode(
        x=alt.X("Time:T", title=None), y=alt.Y("Value:Q", title=y_title))
    rules = alt.Chart(alt.Data(values=marks)).mark_rule(color=ui.ORANGE, strokeWidth=2, strokeDash=[4, 3]).encode(
        x="Time:T", tooltip=["What:N", "Time:T"])
    text = alt.Chart(alt.Data(values=marks)).mark_text(align="left", dx=4, dy=-120, color=ui.ORANGE, fontWeight="bold",
                                                       fontSize=11).encode(x="Time:T", text="What:N")
    st.altair_chart((line + rules + text).properties(height=270), use_container_width=True)
    ui.muted(f"{y_title} · orange lines: the change that caused it and when the cost started")


def evidence_map(case: Case, source: FixtureSource) -> None:
    f, r = case.findings, case.report
    if not (f and r):
        st.markdown("No findings to map.")
        return

    def node(name, text, found):
        color, fill = ("#037F0C", "#E8F5E9") if found else ("#879596", "#F2F3F3")
        return f'"{name}" [label="{text}", color="{color}", fillcolor="{fill}", fontcolor="#16191F"];'

    change = source.record(r.primary_change_id) if r.primary_change_id else None
    lines = [('digraph { rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fontname="Helvetica",'
             ' fontsize=11]; edge [color="#879596"];'),
             (f'"cost" [label="{case.driver.title[:34]}\\n${case.driver.daily_increase:,.0f}/day", color="#232F3E", '
             'fillcolor="#232F3E", fontcolor="#FFFFFF"];'),
             node("change", f"{EVENTS.get(change['EventName'], 'Change')}\\nat {change['EventTime'][11:16]} UTC"
                  if change else "Change\\nnone found", bool(change)),
             node("owner", f"Owner\\n{ui.actor_name(r.route_to)}", r.checks.owner),
             node("planned", "Approved request or budget\\n" + ("found" if r.planned_evidence_ids else "none found"),
                  bool(r.planned_evidence_ids)),
             node("usage", f"Usage\\n{r.usage_summary[:30]}", bool(f.usage_evidence_ids)),
             '"cost" -> "change"; "change" -> "owner"; "cost" -> "planned"; "cost" -> "usage";']
    if f.deployment_evidence_ids or f.traffic_evidence_ids:
        lines += [node("deploy", "Release record", bool(f.deployment_evidence_ids)),
                  node("traffic", "Where traffic goes", bool(f.traffic_evidence_ids)),
                  '"change" -> "deploy"; "cost" -> "traffic";']
    st.graphviz_chart("\n".join(lines) + "}", use_container_width=True)
    ui.muted("Green: evidence found · grey: looked for, not found")


def notes_and_calls(case: Case) -> None:
    notes = list(case.agent_notes)
    rows = []
    for call in case.tool_calls:
        note = next((n for n in notes if n.get("tool") == call.name), None)
        if note:
            notes.remove(note)
        n = len(call.evidence_ids)
        found = ("Could not check" if not call.ok else "Nothing found" if n == 0 else
                 "Found 1 record" if n == 1 else f"Found {n} records")
        rows.append(f"<tr><td class='t'>Step {call.seq}</td><td><b>{STEP_NAMES.get(call.name, 'Checked the evidence')}</b>"
                    f"<span class='d'>{ui.md(note['text']) if note else ''}</span></td>"
                    f"<td class='m'>{found}</td></tr>")
    st.markdown(f"<table class='feed'>{''.join(rows)}</table>", unsafe_allow_html=True)


def checks_and_facts(case: Case, source: FixtureSource) -> None:
    r = case.report
    if not r:
        st.markdown("Needs review: " + "; ".join(case.validation_errors[:2]))
        return
    names = {"size": "Cost matches the usage", "timing": "Change came just before", "exact_id": "Change names the resources",
             "owner": "Owner identified"}
    st.markdown("".join(f"<span class='chip {'on' if getattr(r.checks, k) else 'off'}'>{v}</span>"
                        for k, v in names.items()) + f" &nbsp; {ui.pill(r.verdict)}", unsafe_allow_html=True)
    ui.facts_line([("Confidence", f"{r.confidence} of 4")])
    ignored = case.findings.ignored_change_ids if case.findings else []
    if ignored:
        st.markdown("<div class='label' style='margin-top:.5rem'>Ignored on purpose</div>", unsafe_allow_html=True)
        for eid in ignored[:3]:
            rec = source.record(eid)
            what = plain_event(rec) if rec and "EventName" in rec else "Another change"
            st.markdown(ui.md(f"- {what}: unrelated to this cost, so it was set aside"))
    u = case.usage
    st.markdown("<div class='label' style='margin-top:.5rem'>About this investigation</div>", unsafe_allow_html=True)
    model = "Claude on Amazon Bedrock" + (" (checked twice)" if any("sonnet" in m for m in u.models) else "")
    ui.facts_line([("Investigated by", model), ("Steps", str(len(case.tool_calls)))])
    ui.facts_line([("Time", f"{u.seconds:.0f} s"), ("AI cost", f"${u.cost_usd:.2f}")])


def show(case: Case, source: FixtureSource) -> None:
    st.markdown(f"<div class='factline'>{progress(case)}</div>", unsafe_allow_html=True)
    a, b = st.columns([1.25, 1])
    with a, ui.card("Incident timeline", height=360, tint="blue"):
        timeline(case, source)
    with b, ui.card("Evidence map", height=360, tint="sand"):
        evidence_map(case, source)
    c, d = st.columns([1.6, 1])
    with c, ui.card("What the AI checked, step by step", height=380, tint="slate"):
        notes_and_calls(case)
    with d, ui.card("The four checks", height=380, tint="green"):
        checks_and_facts(case, source)


pending = st.session_state.pop("pending_run", None)
if pending:
    st.session_state["scenario"] = pending["scenario"]
    ui.header("Investigation", "Investigation", ui.day_short(pending["scenario"]))
    store, source, det = start_run(pending["scenario"], pending["mode"])
    agents = make_agents(pending["mode"])
    for i, driver in enumerate(det.drivers, 1):
        status = st.status(f"{driver.title} · investigating", expanded=False)

        def on_event(kind, payload, status=status, driver=driver):
            if kind == "step":
                status.update(label=f"{driver.title} · {payload[1].lower()}")

        case = investigate_driver(store, source, driver, i, agents, on_event)
        status.update(label=label(case), state="complete" if case.report else "error")
    st.success("Investigation finished.")
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Show details"):
        st.rerun()
    if c2.button("Open inbox"):
        st.switch_page("views/inbox.py")
else:
    head, pick_day, pick_case = st.columns([1.2, 1.3, 1.5], vertical_alignment="bottom")
    with pick_day:
        scenario = st.selectbox("Day", ui.scenario_names(), format_func=ui.scenario_label, key="scenario",
                                label_visibility="collapsed")
    with head:
        ui.header("Investigation", "Investigation", ui.day_short(scenario))
    store = ui.day_store(scenario)
    if not store:
        st.markdown("This day has not been investigated yet. Start it from the Command Center.")
        st.stop()
    if not store.cases():
        st.markdown("Below the alert threshold. Nothing was investigated on this day.")
        st.stop()
    cases = {c.case_id: c for c in store.cases()}
    with pick_case:
        pick = st.selectbox("Case", list(cases), format_func=lambda i: label(cases[i]), key="trace_case",
                            label_visibility="collapsed")
    show(cases[pick], FixtureSource(scenario, exceptions_path=store.exceptions_path))
