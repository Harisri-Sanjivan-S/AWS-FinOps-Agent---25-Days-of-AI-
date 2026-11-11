"""Case report: summary card on the left; evidence (one expandable row per summary sentence), checks and audit on
the right."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import streamlit as st

from detective.sources import FixtureSource

me = ui.require_page("case")
pairs = ui.demo_cases(me.actor_id)
if not pairs:
    ui.header("Cases", "Cases")
    st.markdown("No cases for you right now.")
    st.stop()

by_id = {c.case_id: (s, c) for s, c in pairs}
ids = list(by_id)
chosen = st.session_state.get("case_id") if st.session_state.get("case_id") in ids else ids[0]
head, pick = st.columns([2, 1], vertical_alignment="bottom")
with pick:
    case_id = st.selectbox("Case", ids, index=ids.index(chosen), label_visibility="collapsed", format_func=lambda i: (
        f"{i} · {ui.day_short(by_id[i][1].scenario_id)} · {by_id[i][1].driver.title}"))
st.session_state["case_id"] = case_id
store = by_id[case_id][0]
case = store.load_case(case_id)
source = FixtureSource(case.scenario_id, exceptions_path=store.exceptions_path)
r = case.report
with head:
    ui.header(case.driver.title, "Cases", ui.day_short(case.scenario_id), case.case_id)
st.markdown(ui.case_status_line(case), unsafe_allow_html=True)
if not r:
    st.markdown("This case needs human review: " + "; ".join(case.validation_errors))
    st.stop()

fix = {"stop_instances": "Stop the instances (reversible)", "s3_gateway_endpoint": "Add an S3 gateway endpoint (pull request)"}
left, right = st.columns([11, 9])
with left, ui.card("Summary", fill="main"):
    ui.kv([("Increase", f"{ui.money(case.driver.daily_increase)} / day"),
           ("Since", r.cost_start[:16].replace("T", " ") + " UTC"),
           ("Already spent", ui.money(r.already_spent)),
           ("Avoidable", f"{ui.money(r.avoidable_per_day)} / day"),
           ("Proposed fix", fix.get(r.proposed_fix, "None")),
           ("Planned", "Yes" if r.planned else "No approval found"),
           ("Usage", r.usage_summary)])
    st.markdown("")
    for i, s_ in enumerate(case.explanation.sentences if case.explanation else [], 1):
        st.markdown(ui.md(f"**{i}.** {s_.text}"))
    if r.gaps:
        st.markdown("<div class='label' style='margin-top:.6rem'>Gaps</div>", unsafe_allow_html=True)
        for g in r.gaps:
            st.markdown(ui.md(f"- {g}"))

with right, ui.card("Evidence", fill="main"):
    tab_ev, tab_checks, tab_audit = st.tabs(["Evidence", "Checks", "Audit trail"])
    with tab_ev:
        for i, s_ in enumerate(case.explanation.sentences if case.explanation else [], 1):
            short = s_.text if len(s_.text) < 70 else s_.text[:67] + "…"
            with st.expander(ui.md(f"{i}. {short}  ({len(set(s_.evidence_ids))})")):
                ui.evidence_table(source, s_.evidence_ids)
        if r.primary_change_id:
            with st.expander("Linked change"):
                ui.evidence_table(source, [r.primary_change_id])
    with tab_checks:
        names = {"size": "Size matches the bill", "timing": "Change happened just before",
                 "exact_id": "Change names the resources", "owner": "Owner identified"}
        st.dataframe([{"Check": names[k], "Result": "Pass" if getattr(r.checks, k) else "Fail",
                       "Detail": r.checks.notes.get(k, "")} for k in names], hide_index=True, width="stretch")
        ui.facts_line([("Confidence", f"{r.confidence} of 4")])
    with tab_audit:
        st.dataframe([{"Time": a.at[:19].replace("T", " "),
                       "By": ui.actor_name(a.actor) if a.actor in ui.rbac.ACTORS else ui.model_label(a.actor),
                       "Event": a.event} for a in case.audit], hide_index=True, width="stretch")
        u = case.usage
        ui.facts_line([("Tool calls", str(len(case.tool_calls))),
                       ("Model", ", ".join(ui.model_label(m) for m in u.models)),
                       ("Tokens", f"{u.input_tokens + u.output_tokens:,}"), ("Cost", f"${u.cost_usd:.3f}"),
                       ("Time", f"{u.seconds:.0f} s")])
