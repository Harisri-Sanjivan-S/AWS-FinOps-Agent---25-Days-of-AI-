"""Investigation: one row per driver with its status and result; the tool-call trace is one click away."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import streamlit as st

from detective.models import VERDICT_LABELS, Case, ToolCall
from detective.pipeline import investigate_driver, make_agents, start_run

ui.require_page("investigation")


def call_row(call: ToolCall) -> dict:
    ids = ", ".join(call.evidence_ids[:4]) + (f" +{len(call.evidence_ids) - 4}" if len(call.evidence_ids) > 4 else "")
    return {"#": call.seq, "Tool": call.name, "Result": call.summary if call.ok else f"Error: {call.error}",
            "Evidence": ids or "—"}


def trace(case: Case) -> None:
    if case.tool_calls:
        st.dataframe([call_row(c) for c in case.tool_calls], hide_index=True, width="stretch")
    u = case.usage
    ui.facts_line([("Tool calls", str(len(case.tool_calls))), ("Model", ", ".join(ui.model_label(m) for m in u.models)),
                   ("Tokens", f"{u.input_tokens + u.output_tokens:,}"), ("Cost", f"${u.cost_usd:.3f}"),
                   ("Time", f"{u.seconds:.0f} s")])
    if not case.report:
        st.markdown("Needs review: " + "; ".join(case.validation_errors[:2]))


def label(case: Case) -> str:
    r = case.report
    result = VERDICT_LABELS[r.verdict] if r else "Needs review"
    return f"{case.case_id} · {case.driver.title} · {ui.money(case.driver.daily_increase)}/day · {result}"


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
        with status:
            trace(case)
    st.success("Investigation finished.")
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Open cases"):
        st.switch_page("views/case.py")
    if c2.button("Open inbox"):
        st.switch_page("views/inbox.py")
else:
    scenario = st.selectbox("Day", ui.scenario_names(), format_func=ui.scenario_label, key="scenario",
                            label_visibility="collapsed")
    ui.header("Investigation", "Investigation", ui.day_short(scenario))
    store = ui.day_store(scenario)
    if not store:
        st.markdown("This day has not been investigated yet. Start it from the console.")
        st.stop()
    if not store.cases():
        st.markdown("Below the alert threshold. Nothing was investigated on this day.")
        st.stop()
    cases = {c.case_id: c for c in store.cases()}
    left, right = st.columns([2, 3])
    with left, ui.card("Cost drivers", fill="main"):
        for c in cases.values():
            with st.status(label(c), state="complete" if c.report else "error", expanded=False):
                st.markdown(ui.case_status_line(c), unsafe_allow_html=True)
        pick = st.radio("Show trace for", list(cases), horizontal=True, key="trace_case")
    with right, ui.card(f"Tool calls · {pick}", fill="main"):
        trace(cases[pick])
