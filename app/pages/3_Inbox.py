"""Owner inbox: what each person sees, and the only place actions can happen (after a human click)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

from datetime import date, timedelta

import streamlit as st

from detective.config import get_settings
from detective.fixers import make_fixer
from detective.fixers.base import gh_pr_status
from detective.fixers.devin import devin_status
from detective.pipeline import ActionError, approve, mark_planned, next_day, reassign, reject

ui.page("Inbox")
s = get_settings()
st.title("Owner inbox")
store = ui.current_store()
if not store or not store.cases():
    st.info("No cases yet. Start an investigation from the console.")
    st.stop()


@st.cache_data(ttl=120)
def devin_state(devin_bin: str) -> dict:
    return devin_status(devin_bin)


cases = store.cases()
people = list(dict.fromkeys(ui.PERSONAS + [c.assigned_to for c in cases if c.assigned_to]))
open_count = {p: sum(1 for c in cases if c.assigned_to == p and c.status == "awaiting_owner") for p in people}
persona = st.selectbox("View as", people, format_func=lambda p: f"{p} ({open_count[p]})" if open_count[p] else p,
                       key="persona", help="No real login in the POC: switch persona to see each owner's inbox.")
mine = cases if persona == "finops-team" else [c for c in cases if c.assigned_to == persona]
if persona == "finops-team":
    st.caption("FinOps view: every case, including quiet ones.")
if not mine:
    st.success(f"Nothing for {persona}.")
    st.stop()

for case in mine:
    r = case.report
    title = f"{case.case_id} · {case.driver.title} · {ui.money(case.driver.daily_increase)}/day · {ui.STATUS_LABELS[case.status]}"
    with st.expander(title, expanded=case.status in ("awaiting_owner", "fix_in_progress", "fixed")):
        if not r:
            st.error("Needs human review: " + "; ".join(case.validation_errors))
            continue
        if case.status == "quiet":
            st.markdown(f"{ui.badge(r.verdict)} Planned and in use, so no alert was sent.", unsafe_allow_html=True)
            st.caption(f"Planned by {', '.join(r.planned_evidence_ids)} · {r.usage_summary}")
            continue
        left, right = st.columns([3, 2])
        with left:
            ui.message_card(case)
            for sentence in (case.explanation.sentences if case.explanation else [])[:4]:
                st.markdown(f"- {sentence.text}")
        with right:
            if case.status == "awaiting_owner" and persona in (case.assigned_to, "finops-team"):
                reviewer = persona
                tabs = st.tabs(["Approve fix", "Planned", "Not mine", "Reject"])
                with tabs[0]:
                    if not r.proposed_fix:
                        st.caption("No reversible fix proposed for this case. Use Planned, Not mine or Reject.")
                    elif r.proposed_fix == "stop_instances":
                        st.markdown(f"Stop {len(case.driver.resource_ids)} instances (simulated `ec2:StopInstances`; "
                                    "reversible, disks are kept).")
                        if st.button("Approve stop", key=f"ap-{case.case_id}", type="primary"):
                            approve(store, case, reviewer=reviewer)
                            st.rerun()
                    else:
                        d = devin_state(s.devin_bin)
                        options = ["devin", "template"] if d["logged_in"] else ["template"]
                        fixer_name = st.radio("Code fix written by", options, key=f"fx-{case.case_id}", horizontal=True,
                                              index=options.index(s.fixer) if s.fixer in options else 0,
                                              format_func={"devin": "Devin (CLI)", "template": "Template"}.get)
                        st.caption(f"Devin CLI: {d['detail']}. Opens a pull request on {s.demo_repo} "
                                   f"in `{r.fix_target['path'] if r.fix_target else '?'}`; a human merges.")
                        if st.button("Approve fix and open PR", key=f"pr-{case.case_id}", type="primary",
                                     disabled=not r.fix_target):
                            progress = st.status("Preparing the fix", expanded=True)

                            def on_fix(kind, payload, progress=progress):
                                progress.write(payload[1])
                                progress.update(label=payload[1])

                            case = approve(store, case, reviewer=reviewer, fixer=make_fixer(fixer_name), on_event=on_fix)
                            progress.update(state="complete" if case.actions[-1].url else "error")
                            st.rerun()
                with tabs[1], st.form(f"pl-{case.case_id}"):
                    reason = st.text_input("Reason (required)")
                    spike = date.fromisoformat(case.driver.start_date)
                    expiry = st.date_input("Expires (required, max 90 days)", value=spike + timedelta(days=7),
                                           min_value=spike, max_value=spike + timedelta(days=90))
                    if st.form_submit_button("Record as planned"):
                        try:
                            mark_planned(store, case, reviewer, reason, expiry.isoformat())
                            st.rerun()
                        except (ActionError, ValueError) as exc:
                            st.error(str(exc))
                with tabs[2], st.form(f"nm-{case.case_id}"):
                    new_owner = st.selectbox("Route to", [p for p in people if p != case.assigned_to])
                    if st.form_submit_button("Not mine: re-route"):
                        reassign(store, case, reviewer, new_owner)
                        st.rerun()
                with tabs[3], st.form(f"rj-{case.case_id}"):
                    why = st.text_input("Reason (required)")
                    if st.form_submit_button("Reject"):
                        try:
                            reject(store, case, reviewer, why)
                            st.rerun()
                        except ActionError as exc:
                            st.error(str(exc))
            for a in case.actions:
                with st.container(border=True):
                    st.markdown(f"**{a.kind.replace('_', ' ').title()}** · {a.status}")
                    if a.url:
                        st.link_button("Open pull request", a.url, icon=":material/open_in_new:")
                        if a.url.startswith("https://github.com/") and st.button("Refresh CI status", key=f"ci-{a.action_id}"):
                            try:
                                st.write(gh_pr_status(a.url))
                            except Exception as exc:  # noqa: BLE001
                                st.error(str(exc))
                    if a.detail.get("error"):
                        st.error(a.detail["error"])
                    if a.kind == "stop":
                        st.caption(f"{a.action_id} · {a.detail['api']} · {len(a.detail['instance_ids'])} instances")
                    if a.kind == "pull_request":
                        st.caption(f"Written by {a.detail.get('executor')} · files: {', '.join(a.detail.get('changed_files', []))}")
            if any(a.kind in ("stop", "pull_request") and a.status != "failed" for a in case.actions):
                if case.next_day:
                    nd = case.next_day
                    (st.success if nd.confirmed else st.warning)(nd.note)
                    st.caption(f"Expected {ui.money(nd.expected_saving)}/day · measured {ui.money(nd.actual_saving)}/day")
                elif st.button("Next day: check the bill", key=f"nd-{case.case_id}", icon=":material/event:"):
                    next_day(store, case)
                    st.rerun()
