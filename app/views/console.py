"""Console (FinOps): pick a day; the daily-spend chart and the ranked drivers sit side by side in cards."""

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

head, days_col = st.columns([1, 2.4], vertical_alignment="bottom")
with days_col:
    picked = st.segmented_control("Day", names, format_func=ui.day_short, default=st.session_state["scenario"],
                                  key="day_strip", label_visibility="collapsed")
if picked and picked != st.session_state["scenario"]:
    st.session_state["scenario"] = picked
scenario = st.session_state["scenario"]
with head:
    ui.header("Console", "Console", ui.day_short(scenario))

source = FixtureSource(scenario)
det = detect(source)
st.markdown(ui.md(f"{source.scenario.get('title', '')} · Spend **{ui.money(det.spike_total)}** · expected "
                  f"{ui.money(det.baseline_mean)} · {det.increase_pct:+.0f}% · {len(det.drivers)} cost driver"
                  f"{'s' if len(det.drivers) != 1 else ''}"))

left, right = st.columns([3, 2])
with left, ui.card("Daily spend (USD)", fill="main"):
    days = {**det.baseline_days, det.spike_date: det.spike_total}
    st.bar_chart({"Spend": list(days.values())}, color=ui.BLUE, height=500, x_label="Same weekday, last 4 weeks → today",
                 y_label="USD")
    ui.muted(f"Expected {ui.money(det.baseline_mean)} · today {ui.money(det.spike_total)}")


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


with right, ui.card("Cost drivers", fill="main"):
    if not det.material:
        st.markdown("Below the alert threshold. Nothing to investigate.")
    else:
        st.dataframe([{"Driver": d.title, "USD / day": round(d.daily_increase, 2), "Share": d.share_pct / 100}
                      for d in det.drivers], hide_index=True, width="stretch", height=420,
                     column_config={"Share": st.column_config.ProgressColumn("Share", format="percent", min_value=0,
                                                                             max_value=1)})
        b, c = st.columns([1, 1.4], vertical_alignment="center")
        clicked = b.button("Investigate", type="primary")
        with c:
            ui.muted(f"Estimated cost ${est:.2f}")
        if clicked:
            if ui.run_mode() == "bedrock":
                confirm()
            else:
                start()
