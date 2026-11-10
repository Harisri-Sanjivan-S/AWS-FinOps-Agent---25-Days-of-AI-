"""Inbox: the cases the current actor may see, and only the actions their role allows (enforced again in the pipeline)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

from datetime import date, timedelta

import streamlit as st

from detective import rbac
from detective.config import get_settings
from detective.fixers import make_fixer
from detective.fixers.base import gh_pr_status
from detective.fixers.devin import devin_status
from detective.pipeline import ActionError, approve, mark_planned, next_day, reassign, reject

me = ui.require_page("inbox")
s = get_settings()
st.title("Inbox")
st.caption(f"{me.name} · {me.role}")
store = ui.current_store()
cases = rbac.visible_cases(me.actor_id, store.cases()) if store else []
if not cases:
    st.success("Nothing for you right now.")
    st.stop()


@st.cache_data(ttl=120)
def devin_state(devin_bin: str) -> dict:
    return devin_status(devin_bin)


def run_action(fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
        st.rerun()
    except (ActionError, ValueError) as exc:
        st.error(str(exc))


for case in cases:
    r = case.report
    mine = case.assigned_to == me.actor_id
    title = (f"{case.case_id} · {case.driver.title} · {ui.money(case.driver.daily_increase)}/day · "
             f"{ui.STATUS_LABELS[case.status]}" + ("" if mine else f" · owner: {ui.actor_name(case.assigned_to)}"))
    with st.expander(title, expanded=mine and case.status in ("awaiting_owner", "fix_in_progress", "fixed")):
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
            allowed = [a for a in ("approve", "planned", "not_mine", "reject") if rbac.can_act(me.actor_id, case, a)]
            if case.status == "awaiting_owner" and allowed:
                names = {"approve": "Approve fix", "planned": "Planned", "not_mine": "Not mine" if mine else "Re-route",
                         "reject": "Reject"}
                tabs = dict(zip(allowed, st.tabs([names[a] for a in allowed])))
                if "approve" in tabs:
                    with tabs["approve"]:
                        if not r.proposed_fix:
                            st.caption("No reversible fix proposed for this case. Use Planned, Not mine or Reject.")
                        elif r.proposed_fix == "stop_instances":
                            st.markdown(f"Stop {len(case.driver.resource_ids)} instances (reversible; disks are kept).")
                            if st.button("Approve stop", key=f"ap-{case.case_id}", type="primary"):
                                run_action(approve, store, case, reviewer=me.actor_id)
                        else:
                            d = devin_state(s.devin_bin)
                            options = ["devin", "template"] if d["logged_in"] else ["template"]
                            fixer_name = st.radio("Code fix written by", options, key=f"fx-{case.case_id}", horizontal=True,
                                                  index=options.index(s.fixer) if s.fixer in options else 0,
                                                  format_func={"devin": "Devin", "template": "Template"}.get)
                            st.caption(f"Opens a pull request on {s.demo_repo} in "
                                       f"`{r.fix_target['path'] if r.fix_target else '?'}`; a human merges.")
                            if st.button("Approve fix and open PR", key=f"pr-{case.case_id}", type="primary",
                                         disabled=not r.fix_target):
                                progress = st.status("Preparing the fix", expanded=True)

                                def on_fix(kind, payload, progress=progress):
                                    progress.write(payload[1])
                                    progress.update(label=payload[1])

                                run_action(approve, store, case, reviewer=me.actor_id, fixer=make_fixer(fixer_name),
                                           on_event=on_fix)
                if "planned" in tabs:
                    with tabs["planned"], st.form(f"pl-{case.case_id}"):
                        reason = st.text_input("Reason (required)")
                        spike = date.fromisoformat(case.driver.start_date)
                        expiry = st.date_input("Expires (required, max 90 days)", value=spike + timedelta(days=7),
                                               min_value=spike, max_value=spike + timedelta(days=90))
                        if st.form_submit_button("Record as planned"):
                            run_action(mark_planned, store, case, me.actor_id, reason, expiry.isoformat())
                if "not_mine" in tabs:
                    with tabs["not_mine"], st.form(f"nm-{case.case_id}"):
                        new_owner = st.selectbox("Route to", [a for a in rbac.ACTORS if a != case.assigned_to],
                                                 format_func=ui.actor_name)
                        if st.form_submit_button("Re-route"):
                            run_action(reassign, store, case, me.actor_id, new_owner)
                if "reject" in tabs:
                    with tabs["reject"], st.form(f"rj-{case.case_id}"):
                        why = st.text_input("Reason (required)")
                        if st.form_submit_button("Reject"):
                            run_action(reject, store, case, me.actor_id, why)
            elif case.status == "awaiting_owner":
                st.caption(f"Read-only: waiting for {ui.actor_name(case.assigned_to)} to decide.")
            for a in case.actions:
                with st.container(border=True):
                    st.markdown(f"**{a.kind.replace('_', ' ').title()}** · {a.status}")
                    if a.url:
                        st.link_button("Open pull request", a.url, icon=":material/open_in_new:")
                        if a.url.startswith("https://github.com/") and st.button("Refresh CI status", key=f"ci-{a.action_id}"):
                            try:
                                st.write(gh_pr_status(a.url))
                            except Exception as exc:  # noqa: BLE001 - show any gh error to the user
                                st.error(str(exc))
                    if a.detail.get("error"):
                        st.error(a.detail["error"])
                    if a.kind == "stop":
                        st.caption(f"{a.action_id} · {len(a.detail['instance_ids'])} instances stopped")
                    if a.kind == "pull_request":
                        st.caption(f"Written by {a.detail.get('executor')} · files: {', '.join(a.detail.get('changed_files', []))}")
            if any(a.kind in ("stop", "pull_request") and a.status != "failed" for a in case.actions):
                if case.next_day:
                    nd = case.next_day
                    (st.success if nd.confirmed else st.warning)(nd.note)
                    st.caption(f"Expected {ui.money(nd.expected_saving)}/day · measured {ui.money(nd.actual_saving)}/day")
                elif mine and st.button("Next day: check the bill", key=f"nd-{case.case_id}", icon=":material/event:"):
                    run_action(next_day, store, case)
