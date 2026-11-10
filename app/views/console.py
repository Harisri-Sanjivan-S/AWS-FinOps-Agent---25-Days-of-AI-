"""Console (FinOps): pick a day, see code's measurement and the ranked drivers, start an investigation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import streamlit as st

from detective.config import get_settings
from detective.detect import detect
from detective.ledger import estimate_usd
from detective.sources import FixtureSource

ui.require_page("console")
s = get_settings()
names = ui.scenario_names()

picked = st.segmented_control("Day", names, format_func=ui.day_short, default=st.session_state["scenario"],
                              key="day_strip", label_visibility="collapsed")
if picked and picked != st.session_state["scenario"]:
    st.session_state["scenario"] = picked
scenario = st.session_state["scenario"]
ui.header("Console", "Console", ui.day_short(scenario))

source = FixtureSource(scenario)
det = detect(source)
ui.muted(f"{source.scenario.get('title', '')}")
st.markdown(ui.md(f"Spend **{ui.money(det.spike_total)}** · expected {ui.money(det.baseline_mean)} · "
                  f"{det.increase_pct:+.0f}% · {len(det.drivers)} cost driver{'s' if len(det.drivers) != 1 else ''}"))

days = {**det.baseline_days, det.spike_date: det.spike_total}
st.line_chart({"Daily spend (USD)": list(days.values())}, height=170, x_label="", y_label="")

if not det.material:
    st.markdown("Below the alert threshold. Nothing to investigate.")
    st.stop()

st.subheader("Cost drivers")
st.dataframe([{"Driver": d.title, "Resources": len(d.resource_ids), "USD / day": round(d.daily_increase, 2),
               "Share": f"{d.share_pct:.0f}%"} for d in det.drivers], hide_index=True, width="stretch")


def start() -> None:
    st.session_state["pending_run"] = {"scenario": scenario, "mode": ui.run_mode()}
    st.switch_page("views/investigation.py")


est = estimate_usd(s.model_fast, len(det.drivers))


@st.dialog("Start investigation")
def confirm() -> None:
    st.markdown(f"Claude on Amazon Bedrock will investigate {len(det.drivers)} cost driver(s) for "
                f"{ui.day_short(scenario)}.")
    ui.kv([("Estimated cost", f"${est:.2f}"), ("Model", ui.model_label(s.model_fast)),
           ("Spending guard", f"${s.cost_cap_usd:.0f}")])
    ok = st.checkbox(f"I approve spending about ${est:.2f}")
    if st.button("Start", type="primary", disabled=not ok):
        start()


left, right = st.columns([1, 3], vertical_alignment="center")
clicked = left.button("Investigate", type="primary", icon=":material/search:")
with right:
    ui.muted(f"Estimated cost ${est:.2f}")
if clicked:
    if ui.run_mode() == "bedrock":
        confirm()
    else:
        start()
