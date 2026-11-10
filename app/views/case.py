"""Case report: verdict, the four checks, money, the cited explanation, gaps and the audit trail."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import streamlit as st

from detective.sources import FixtureSource

me = ui.require_page("case")
st.title("Case report")
pairs = ui.demo_cases(me.actor_id)
if not pairs:
    st.info("No cases for you right now.")
    st.stop()

by_id = {c.case_id: (s, c) for s, c in pairs}
ids = list(by_id)
chosen = st.session_state.get("case_id") if st.session_state.get("case_id") in ids else ids[0]
case_id = st.selectbox("Case", ids, index=ids.index(chosen), format_func=lambda i: (
    f"{i} · {ui.scenario_label(by_id[i][1].scenario_id).split(' · ')[0]} · {by_id[i][1].driver.title} · "
    f"{ui.STATUS_LABELS[by_id[i][1].status]}"))
st.session_state["case_id"] = case_id
store = by_id[case_id][0]
case = store.load_case(case_id)
source = FixtureSource(case.scenario_id, exceptions_path=store.exceptions_path)
r = case.report

if not r:
    st.error("This case needs human review: " + "; ".join(case.validation_errors))
    st.stop()

st.markdown(f"## {ui.badge(r.verdict, big=True)} &nbsp; {case.driver.title}", unsafe_allow_html=True)
st.markdown(f"Confidence **{r.confidence} of 4**" + (" · **please confirm**" if r.needs_confirmation else "")
            + f" · status **{ui.STATUS_LABELS[case.status]}** · routed to **{ui.actor_name(case.assigned_to)}**")

cols = st.columns(4)
for col, (name, label) in zip(cols, [("size", "Size"), ("timing", "Timing"), ("exact_id", "Exact ID"), ("owner", "Owner")]):
    ok = getattr(r.checks, name)
    with col.container(border=True):
        st.markdown(f"**{label}** &nbsp; {':green[pass]' if ok else ':red[fail]'}")
        st.caption(r.checks.notes.get(name, ""))

m1, m2, m3, m4 = st.columns(4)
m1.metric("Increase", f"{ui.money(case.driver.daily_increase)}/day", f"{case.driver.share_pct:.0f}% of the spike", delta_color="off")
m2.metric("Already spent (code)", ui.money(r.already_spent), f"{r.hours_running:.0f} h since {r.cost_start[11:16]} UTC", delta_color="off")
m3.metric("Avoidable (code)", f"{ui.money(r.avoidable_per_day)}/day")
m4.metric("Planned / used", f"{'yes' if r.planned else 'no'} / {'unknown' if r.used is None else ('yes' if r.used else 'no')}")
st.caption("All numbers above are computed by code. The AI never does arithmetic and never decides the verdict.")

tab1, tab2, tab3, tab4 = st.tabs(["Explanation", "Evidence and gaps", "Audit trail", "Raw"])
with tab1:
    e = case.explanation
    st.markdown(f"**{e.headline}**")
    for i, s in enumerate(e.sentences):
        st.markdown(f"{i + 1}. {s.text}")
        ui.citations(source, s.evidence_ids, limit=8)
    st.caption("Every sentence was checked by code for valid evidence IDs and matching numbers.")
with tab2:
    st.markdown(f"**Usage:** {r.usage_summary}")
    if r.budget_note:
        st.markdown(f"**Budget:** {r.budget_note}")
    if r.primary_change_id:
        st.markdown(f"**Linked change:** {r.primary_change_id}")
    if case.findings and case.findings.ignored_change_ids:
        st.markdown(f"**Changes judged unrelated by the agent:** {', '.join(case.findings.ignored_change_ids)}")
    if r.proposed_fix:
        st.markdown(f"**Proposed reversible fix:** {r.proposed_fix.replace('_', ' ')}"
                    + (f" in `{r.fix_target['path']}` ({r.fix_target['team']})" if r.fix_target else ""))
    st.markdown("**Gaps**")
    for g in r.gaps or ["None"]:
        st.markdown(f"- {g}")
    if case.findings:
        st.markdown("**Agent's cited findings**")
        for i, c in enumerate(case.findings.claims):
            st.markdown(f"- {c.text}")
            ui.citations(source, c.evidence_ids, limit=8)
with tab3:
    st.dataframe([{"at": a.at, "actor": a.actor, "event": a.event, "detail": str(a.detail)[:160]} for a in case.audit],
                 hide_index=True, use_container_width=True)
with tab4:
    st.json(case.model_dump(exclude={"tool_calls"}), expanded=False)
