"""AI performance across the demo week (Bedrock runs), and proof of every change made after an owner approval."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import streamlit as st

from detective.fixers.base import gh_pr_status
from detective.sources import FixtureSource
from evals.score import latest_runs, proof, score_case, summarise

ui.require_page("performance")
st.title("AI performance & proof")

mode = "offline" if st.session_state.get("include_dry_runs") else "bedrock"
stores = latest_runs(mode=mode)
if not stores:
    st.info("No Amazon Bedrock runs of the demo week yet. FinOps can run a day from the console, or the whole week with "
            "`uv run python scripts/run_week.py --confirm`.")
    st.stop()

rows = [score_case(c) for s in stores for c in s.cases()]
summary = summarise(rows)
evidence = proof(stores)
days = sorted({s.meta()["scenario_id"] for s in stores})
st.caption(f"Latest run of {len(days)} day(s): {', '.join(ui.scenario_label(d).split(' · ')[0] for d in days)} · "
           f"{summary.get('investigations', 0)} investigations by Claude on Amazon Bedrock")

tab_perf, tab_proof = st.tabs(["AI performance", "Proof of changes"])

with tab_perf:
    if not rows:
        st.success("No cost drivers needed investigating on these days.")
    else:
        c = st.columns(4)
        c[0].metric("Verdict accuracy", f"{summary['verdict_accuracy_pct']}%", help="Verdict matches the planted truth")
        c[1].metric("Routed to the right owner", f"{summary['routing_accuracy_pct']}%")
        c[2].metric("Claims with valid evidence", f"{summary['citations_valid_pct']}%",
                    help="Every accepted claim cites an ID a tool actually returned; code rejects the rest")
        c[3].metric("Right first time", f"{summary['first_pass_valid_pct']}%",
                    help="Findings accepted on the first attempt (no retry)")
        c = st.columns(4)
        c[0].metric("Avg time per driver", f"{summary['avg_seconds']:.0f} s")
        c[1].metric("Avg cost per driver", f"${summary['avg_cost_usd']:.3f}", f"total ${summary['total_cost_usd']:.2f}",
                    delta_color="off")
        c[2].metric("Tool calls per driver", f"{summary['avg_tool_calls']}", f"max {summary['max_tool_calls']}", delta_color="off")
        c[3].metric("Escalated to Sonnet", f"{summary['escalation_pct']}%")
        if summary["guardrail_tests"]:
            st.markdown(f"**Guardrails:** {summary['guardrail_passed']} of {summary['guardrail_tests']} passed "
                        "(unrelated change ignored, prompt injection in a ticket resisted). "
                        f"Actions taken before approval: **{evidence['actions_without_approval']}**.")

        st.subheader("By day")
        per_day = {}
        for r in rows:
            d = per_day.setdefault(r["scenario"], {"Day": ui.scenario_label(r["scenario"]), "Drivers": 0, "Correct": 0,
                                                   "Seconds": 0.0, "Cost USD": 0.0, "Tool calls": 0})
            d["Drivers"] += 1
            d["Correct"] += int(r["correct"])
            d["Seconds"] = round(d["Seconds"] + r["seconds"], 1)
            d["Cost USD"] = round(d["Cost USD"] + r["cost_usd"], 4)
            d["Tool calls"] += r["tool_calls"]
        st.dataframe(list(per_day.values()), hide_index=True, use_container_width=True)
        left, right = st.columns(2)
        left.caption("Seconds per day")
        left.bar_chart({"seconds": [d["Seconds"] for d in per_day.values()]}, height=200)
        right.caption("Bedrock cost per day (USD)")
        right.bar_chart({"USD": [d["Cost USD"] for d in per_day.values()]}, height=200)

        st.subheader("Every investigation")
        st.dataframe([{k: r[k] for k in ("case", "driver", "expected", "verdict", "correct", "citations_valid",
                                         "investigator_attempts", "tool_calls", "seconds", "cost_usd", "models")}
                      for r in rows], hide_index=True, use_container_width=True)

with tab_proof:
    c = st.columns(4)
    c[0].metric("Unplanned spend found", f"{ui.money(evidence['found_per_day'])}/day")
    c[1].metric("Avoidable with a fix", f"{ui.money(evidence['avoidable_per_day'])}/day")
    c[2].metric("Saving proven on the next bill", f"{ui.money(evidence['proven_per_day'])}/day",
                f"≈ {ui.money(evidence['proven_per_day'] * 365)}/year run-rate", delta_color="off")
    c[3].metric("Waiting on PR merge", f"{ui.money(evidence['pending_pr_per_day'])}/day")

    st.subheader("Savings proven by the bill")
    if not evidence["savings"]:
        st.caption("No approved stops yet.")
    for s_ in evidence["savings"]:
        with st.container(border=True):
            ok = s_["confirmed"]
            st.markdown(f"**{s_['case']}** · {s_['driver']} · approved by **{ui.actor_name(s_['approved_by'])}** "
                        f"at {s_['approved_at'][:16].replace('T', ' ')} UTC · action `{s_['action']}`")
            if s_["after_usd"] is not None:
                st.bar_chart({"USD/day": [s_["before_usd"], s_["after_usd"]]}, x_label="spike day → next day",
                             height=160)
            (st.success if ok else st.warning)(s_["note"])
            ui.citations(FixtureSource(s_["day"]), s_["evidence"][:6])

    st.subheader("Code changes (pull requests)")
    if not evidence["pull_requests"]:
        st.caption("No code fixes approved yet.")
    for p in evidence["pull_requests"]:
        with st.container(border=True):
            st.markdown(f"**{p['case']}** · approved by **{ui.actor_name(p['approved_by'])}** · written by {p['executor']} · "
                        f"{p['status']} · expected {ui.money(p['avoidable_usd'])}/day after merge and apply")
            if p["url"]:
                st.link_button("Open pull request", p["url"], icon=":material/open_in_new:")
                if p["url"].startswith("https://github.com/") and st.button("Check CI", key=f"ci-{p['case']}-{p['branch']}"):
                    try:
                        st.write(gh_pr_status(p["url"]))
                    except Exception as exc:  # noqa: BLE001 - show any gh error to the user
                        st.error(str(exc))
            if p["error"]:
                st.error(p["error"])
            if p["diff"]:
                st.code(p["diff"], language="diff")

    st.subheader("Human decisions (audit trail)")
    if evidence["decisions"]:
        st.dataframe(evidence["decisions"], hide_index=True, use_container_width=True)
    else:
        st.caption("No owner decisions yet.")
