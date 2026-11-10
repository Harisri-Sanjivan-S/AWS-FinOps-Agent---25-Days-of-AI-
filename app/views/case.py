"""Case report: status, key facts, the four checks, the cited explanation; evidence and audit one click away."""

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
case_id = st.selectbox("Case", ids, index=ids.index(chosen), label_visibility="collapsed", format_func=lambda i: (
    f"{i} · {ui.day_short(by_id[i][1].scenario_id)} · {by_id[i][1].driver.title}"))
st.session_state["case_id"] = case_id
store = by_id[case_id][0]
case = store.load_case(case_id)
source = FixtureSource(case.scenario_id, exceptions_path=store.exceptions_path)
r = case.report

ui.header(case.driver.title, "Cases", ui.day_short(case.scenario_id), case.case_id)
st.markdown(ui.case_status_line(case), unsafe_allow_html=True)
if not r:
    st.markdown("This case needs human review: " + "; ".join(case.validation_errors))
    st.stop()

fix = {"stop_instances": "Stop the instances (reversible)", "s3_gateway_endpoint": "Add an S3 gateway endpoint (pull request)"}
ui.kv([("Increase", f"{ui.money(case.driver.daily_increase)} / day"),
       ("Since", r.cost_start[:16].replace("T", " ") + " UTC"),
       ("Already spent", ui.money(r.already_spent)),
       ("Avoidable", f"{ui.money(r.avoidable_per_day)} / day"),
       ("Proposed fix", fix.get(r.proposed_fix, "None")),
       ("Planned", "Yes" if r.planned else "No approval found"),
       ("Usage", r.usage_summary)])

tab_summary, tab_checks, tab_evidence, tab_audit = st.tabs(["Summary", "Checks", "Evidence and gaps", "Audit trail"])
with tab_summary:
    for i, s_ in enumerate(case.explanation.sentences if case.explanation else []):
        st.markdown(ui.md(s_.text))
        ui.citations(source, s_.evidence_ids)
with tab_checks:
    names = {"size": "Size matches the bill", "timing": "Change happened just before", "exact_id": "Change names the resources",
             "owner": "Owner identified"}
    st.dataframe([{"Check": names[k], "Result": "Pass" if getattr(r.checks, k) else "Fail",
                   "Detail": r.checks.notes.get(k, "")} for k in names], hide_index=True, width="stretch")
    ui.muted(f"Confidence {r.confidence} of 4")
with tab_evidence:
    if r.budget_note:
        ui.kv([("Budget", r.budget_note)])
    if r.primary_change_id:
        st.markdown("Linked change")
        ui.citations(source, [r.primary_change_id])
    if r.gaps:
        st.markdown("Gaps")
        for g in r.gaps:
            st.markdown(ui.md(f"- {g}"))
    if case.findings and case.findings.claims:
        with st.expander("Agent's cited findings"):
            for c in case.findings.claims:
                st.markdown(ui.md(c.text))
                ui.citations(source, c.evidence_ids)
with tab_audit:
    st.dataframe([{"Time": a.at[:19].replace("T", " "), "By": ui.actor_name(a.actor) if a.actor in ui.rbac.ACTORS
                   else ui.model_label(a.actor), "Event": a.event} for a in case.audit], hide_index=True, width="stretch")
    with st.expander("Details"):
        u = case.usage
        ui.kv([("Run", case.run_id), ("Models", ", ".join(ui.model_label(m) for m in u.models)),
               ("Tool calls", str(len(case.tool_calls))), ("Tokens", f"{u.input_tokens + u.output_tokens:,}"),
               ("Cost", f"${u.cost_usd:.4f}"), ("Time", f"{u.seconds:.0f} s")])
