"""Inbox: the actor's cases as a clean list; the selected case shows one main action and the rest under "More".
Every action is checked again by the pipeline's role-based access rules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

from datetime import date, timedelta

import streamlit as st

from checks import check_badges
from detective import integrations, rbac
from detective.config import get_settings
from detective.fixers import make_fixer
from detective.models import VERDICT_LABELS
from detective.pipeline import ActionError, approve, mark_planned, next_day, reassign, refresh_ci, reject

me = ui.require_page("inbox")
s = get_settings()
pairs = ui.demo_cases(me.actor_id)

head, filt = st.columns([2, 1], vertical_alignment="bottom")
with filt:
    view = st.segmented_control("Show", ["Needs my decision", "All"], default="Needs my decision", key="inbox_filter",
                                label_visibility="collapsed") or "All"
with head:
    ui.header("Inbox", "Inbox", view)
if view == "Needs my decision":
    pairs = [(st_, c) for st_, c in pairs if c.assigned_to == me.actor_id and c.status == "awaiting_owner"]
if not pairs:
    st.markdown("Nothing for you right now.")
    st.stop()


def run_action(fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except (ActionError, ValueError, RuntimeError) as exc:
        st.error(str(exc))
        return
    st.rerun()


left, right = st.columns([2, 3])
by_id = {c.case_id: (st_, c) for st_, c in pairs}
ids = list(by_id)
with left, ui.card("My cases" if view == "Needs my decision" else "All cases", fill="main"):
    chosen = st.session_state.get("inbox_case") if st.session_state.get("inbox_case") in ids else ids[0]
    case_id = st.selectbox("Open case", ids, index=ids.index(chosen), key="inbox_case",
                           format_func=lambda i: f"{i} · {by_id[i][1].driver.title}")
    st.dataframe([{"Day": ui.day_short(c.scenario_id), "Case": c.case_id,
                   "Verdict": VERDICT_LABELS[c.report.verdict] if c.report else "Needs review",
                   "USD / day": round(c.driver.daily_increase, 2), "Status": ui.STATUS_LABELS[c.status],
                   "Owner": ui.actor_name(c.assigned_to)} for _, c in pairs], hide_index=True, width="stretch",
                 height=560)
store, case = by_id[case_id]
r = case.report
mine = case.assigned_to == me.actor_id

with right, ui.card("Selected case", fill="main"):
    st.subheader(f"{case.case_id} · {case.driver.title}")
    st.markdown(ui.case_status_line(case), unsafe_allow_html=True)
    if not r:
        st.markdown("Needs human review: " + "; ".join(case.validation_errors))
        st.stop()
    if case.status == "quiet":
        st.markdown("Planned and in use, so no alert was sent.")
        ui.facts_line([("Planned by", ", ".join(r.planned_evidence_ids)), ("Usage", r.usage_summary)])
        st.stop()

    ui.kv([("Increase", f"{ui.money(case.driver.daily_increase)} / day"), ("Already spent", ui.money(r.already_spent)),
           ("Avoidable", f"{ui.money(r.avoidable_per_day)} / day")])
    for sentence in (case.explanation.sentences if case.explanation else [])[:3]:
        st.markdown(ui.md(f"- {sentence.text}"))

    allowed = [a for a in ("approve", "planned", "not_mine", "reject") if rbac.can_act(me.actor_id, case, a)]
    if case.status == "awaiting_owner" and allowed:
        names = {"approve": "Approve fix", "planned": "Planned", "not_mine": "Not mine" if mine else "Re-route",
                 "reject": "Reject"}
        tabs = dict(zip(allowed, st.tabs([names[a] for a in allowed])))
        if "approve" in tabs:
            with tabs["approve"]:
                if r.proposed_fix == "stop_instances":
                    what = "Stop the idle hosts early" if r.verdict == "oversized" else "Stop the instances"
                    st.markdown(f"{what} ({len(case.driver.resource_ids)} instances; reversible, disks are kept).")
                    if st.button("Approve stop", key=f"ap-{case.case_id}", type="primary"):
                        run_action(approve, store, case, reviewer=me.actor_id)
                elif r.proposed_fix:
                    options, default = integrations.writer_options()
                    conn = integrations.devin_connection()
                    labels = {"template": "Template", "devin": f"Devin ({conn['user']})" if conn else "Devin"}
                    fixer_name = st.radio("Code fix written by", options, index=options.index(default),
                                          key=f"fx-{case.case_id}", horizontal=True, format_func=labels.get)
                    if not conn:
                        ui.muted("Connect Devin in Settings → Integrations to let Devin write fixes.")
                    ui.muted(f"Opens a pull request on {s.demo_repo} in "
                             f"{r.fix_target['path'] if r.fix_target else '?'}; a human merges.")
                    if st.button("Approve fix and open PR", key=f"pr-{case.case_id}", type="primary",
                                 disabled=not r.fix_target):
                        progress = st.status("Preparing the fix", expanded=True)

                        def on_fix(kind, payload, progress=progress):
                            progress.write(payload[1])
                            progress.update(label=payload[1])

                        run_action(approve, store, case, reviewer=me.actor_id, fixer=make_fixer(fixer_name),
                                   on_event=on_fix)
                else:
                    st.markdown("No reversible fix is proposed for this case. Use Planned, Not mine or Reject.")
        if "planned" in tabs:
            with tabs["planned"], st.form(f"pl-{case.case_id}"):
                reason = st.text_input("Reason (required)")
                spike = date.fromisoformat(case.driver.start_date)
                expiry = st.date_input("Expires (required, max 90 days)", value=spike + timedelta(days=7), min_value=spike,
                                       max_value=spike + timedelta(days=90))
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
        ui.muted(f"Waiting for {ui.actor_name(case.assigned_to)} to decide.")

    if case.actions:
        st.subheader("Progress")
        for a in case.actions:
            line = f"{a.kind.replace('_', ' ').title()} · {a.status} · {a.at[:16].replace('T', ' ')}"
            st.markdown(line)
            if a.kind == "pull_request":
                st.markdown(check_badges(a.detail.get("tf_checks", {}), a.detail.get("ci", "not checked")),
                            unsafe_allow_html=True)
                c1, c2, _ = st.columns([1, 1, 2])
                if a.url:
                    c1.link_button("Open pull request", a.url)
                    if a.url.startswith("https://github.com/") and c2.button("Refresh CI status", key=f"ci-{a.action_id}"):
                        run_action(refresh_ci, store, case)
                if a.detail.get("diff"):
                    with st.expander("View diff"):
                        st.code(a.detail["diff"], language="diff")
            if a.detail.get("error"):
                st.markdown(f"Error: {a.detail['error']}")
        if any(a.kind in ("stop", "pull_request") and a.status != "failed" for a in case.actions):
            if case.next_day:
                nd = case.next_day
                (st.success if nd.confirmed else st.warning)(ui.md(nd.note))
            elif mine and st.button("Next day: check the bill", key=f"nd-{case.case_id}"):
                run_action(next_day, store, case)
