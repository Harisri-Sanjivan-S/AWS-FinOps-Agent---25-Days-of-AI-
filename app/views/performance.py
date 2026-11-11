"""AI performance across the demo week (Bedrock runs), and proof of every change made after an owner approval."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import json

import streamlit as st

from checks import check_badges
from detective.config import ROOT
from detective.pipeline import refresh_ci
from evals.score import proof, score_case, summarise

ui.require_page("performance")
stores = ui.demo_stores()
history = ui.history_stores()
head, sec = st.columns([1.4, 1], vertical_alignment="bottom")
with sec:
    tab = st.segmented_control("Section", ["Overview", "Investigations", "Proof", "Audit"], default="Overview",
                               key="perf_tab", label_visibility="collapsed") or "Overview"
with head:
    ui.header("AI performance", "AI performance", tab)
if not stores:
    st.markdown("No investigations of the demo week yet.")
    st.stop()

rows = [score_case(c) for s in stores for c in s.cases()]
summary = summarise(rows)
evidence = proof(stores, history)


def comparison() -> tuple[dict, dict] | None:
    d = ROOT / "evals" / "results"
    ev, bl = sorted(d.glob("*-bedrock-haiku.json"), reverse=True), sorted(d.glob("*-baseline.json"), reverse=True)
    if not (ev and bl):
        return None
    return json.loads(ev[0].read_text()), json.loads(bl[0].read_text())


if tab == "Overview" and summary:
    for col, (label, value) in zip(st.columns(4), [
            ("Verdict accuracy", f"{summary['verdict_accuracy_pct']}%"),
            ("Claims with valid evidence", f"{summary['citations_valid_pct']}%"),
            ("Time per investigation", f"{summary['avg_seconds']:.0f} s"),
            ("Cost per investigation", f"${summary['avg_cost_usd']:.3f}")]):
        with col.container(border=True):
            st.metric(label, value)
    c1, c2, c3 = st.columns([1, 1, 1])
    comp = comparison()
    with c1, ui.card("Agent with rules vs plain AI", fill="low"):
        if comp:
            ev, bl = comp[0]["summary"]["metrics"], comp[1]["summary"]["metrics"]
            agent, plain = float(ev["Verdict accuracy"].rstrip("%")), float(bl["Verdict accuracy"].rstrip("%"))
            st.bar_chart({"Verdict accuracy (%)": [agent, plain]}, color=ui.ORANGE, height=370, x_label="",
                         y_label="Accuracy %")
            ui.facts_line([("Agent with rules", f"{agent:.0f}%"), ("Plain AI", f"{plain:.0f}%")])
            ui.muted("Left bar: agent with rules · right bar: plain AI. Same model, same seven test scenarios.")
        else:
            st.markdown("Run the eval suite and the baseline to fill this chart.")
    with c2, ui.card("Time and cost by day", fill="low"):
        per_day = {}
        for r in rows:
            d = per_day.setdefault(ui.day_short(r["scenario"]), {"Seconds": 0.0, "Cost (cents)": 0.0})
            d["Seconds"] += r["seconds"]
            d["Cost (cents)"] += r["cost_usd"] * 100
        st.bar_chart({"Seconds": [round(v["Seconds"]) for v in per_day.values()],
                      "Cost (cents)": [round(v["Cost (cents)"], 1) for v in per_day.values()]},
                     color=[ui.BLUE, ui.ORANGE], height=370, stack=False, x_label="Demo days, in order", y_label="")
        ui.muted(" · ".join(per_day))
    with c3, ui.card("Key results", fill="low"):
        ui.kv([("Verdict accuracy", f"{summary['verdict_accuracy_pct']}%"),
               ("Routed to the right owner", f"{summary['routing_accuracy_pct']}%"),
               ("Right first time", f"{summary['first_pass_valid_pct']}%"),
               ("Guardrail tests passed", f"{summary['guardrail_passed']} of {summary['guardrail_tests']}"),
               ("Saving confirmed on the next bill", f"{ui.money(evidence['proven_per_day'])} / day"),
               ("Actions taken before approval", str(evidence["actions_without_approval"]))])

elif tab == "Investigations":
    st.dataframe([{"Day": ui.day_short(r["scenario"]), "Case": r["case"], "Driver": r["driver"], "Expected": r["expected"],
                   "Verdict": r["verdict"], "Correct": r["correct"], "Tool calls": r["tool_calls"],
                   "Seconds": round(r["seconds"]), "Cost USD": round(r["cost_usd"], 3)} for r in rows],
                 hide_index=True, width="stretch")

elif tab == "Proof":
    ui.kv([("Unplanned spend found", f"{ui.money(evidence['found_per_day'])} / day"),
           ("Avoidable with a fix", f"{ui.money(evidence['avoidable_per_day'])} / day"),
           ("Saving confirmed on the next bill", f"{ui.money(evidence['proven_per_day'])} / day"),
           ("Waiting on pull-request merge", f"{ui.money(evidence['pending_pr_per_day'])} / day")])
    sav_col, pr_col = st.columns(2)
    with sav_col:
        sav_box = ui.card("Savings", fill="proof")
    with pr_col:
        pr_box = ui.card("Pull requests", fill="proof")
    with sav_box:
        if evidence["savings"]:
            st.dataframe([{"Case": s_["case"], "Day": ui.day_short(s_["day"]), "Approved by": ui.actor_name(s_["approved_by"]),
                           "Before (USD/day)": s_["before_usd"], "After (USD/day)": s_["after_usd"],
                           "Confirmed": {True: "Yes", False: "No"}.get(s_["confirmed"], "Pending")}
                          for s_ in evidence["savings"]], hide_index=True, width="stretch")
        else:
            st.markdown("No approved stops yet.")
    with pr_box:
        if not evidence["pull_requests"]:
            st.markdown("No code fixes approved yet.")
        for p in evidence["pull_requests"]:
            st.markdown(f"{p['case']} · approved by {ui.actor_name(p['approved_by'])} · written by {p['executor']} · {p['status']}")
            st.markdown(check_badges(p["tf_checks"], p["ci"]), unsafe_allow_html=True)
            c1, c2, _ = st.columns([1, 1, 2])
            if p["url"]:
                c1.link_button("Open pull request", p["url"])
                store = next(s for s in history if any(c.case_id == p["case"] for c in s.cases()))
                if p["url"].startswith("https://github.com/") and c2.button("Check CI", key=f"ci-{p['case']}-{p['branch']}"):
                    try:
                        refresh_ci(store, store.load_case(p["case"]))
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001 - show any gh error to the user
                        st.error(str(exc))
            if p["diff"]:
                with st.expander("View diff"):
                    st.code(p["diff"], language="diff")
            if p["error"]:
                st.markdown(f"Error: {p['error']}")

elif tab == "Audit":
    if evidence["decisions"]:
        st.dataframe([{"Time": d["at"][:16].replace("T", " "), "Case": d["case"], "Decision": d["decision"],
                       "By": ui.actor_name(d["by"]), "Reason": d["reason"]} for d in evidence["decisions"]],
                     hide_index=True, width="stretch")
    else:
        st.markdown("No owner decisions yet.")
