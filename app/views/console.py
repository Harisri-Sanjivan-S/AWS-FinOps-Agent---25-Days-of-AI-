"""Console (FinOps): the spike, code's measurement, the ranked drivers, and the Investigate button."""

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

st.title("Cost spike investigation console")
scenario = st.selectbox("Scenario", ui.scenario_names(), format_func=ui.scenario_label, key="scenario")
source = FixtureSource(scenario)
det = detect(source)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Spike day", det.spike_date)
c2.metric("Actual spend", ui.money(det.spike_total), f"+{ui.money(det.increase)} (+{det.increase_pct:.0f}%)", delta_color="inverse")
c3.metric("Expected (last 4 same weekdays)", ui.money(det.baseline_mean), f"σ {ui.money(det.baseline_sd)}", delta_color="off")
c4.metric("Materiality gate", "Passed" if det.material else "Not material", "> $100/day and > 20%", delta_color="off")

left, right = st.columns([2, 3])
with left:
    st.subheader("Daily bill")
    days = {**det.baseline_days, det.spike_date: det.spike_total}
    st.bar_chart({"USD": list(days.values())}, x_label="", y_label="USD/day", height=260)
    st.caption(" · ".join(f"{d[5:]}: {ui.money(v)}" for d, v in days.items()))
with right:
    st.subheader("Code splits the increase into drivers")
    st.caption(f"{len(det.drivers)} drivers explain {det.explained_pct:.0f}% of the increase. No AI is used for this step.")
    rows = [{"#": i, "Driver": d.title, "USD/day": d.daily_increase, "Share": f"{d.share_pct:.0f}%", "Cause": d.cause,
             "Resources": len(d.resource_ids)} for i, d in enumerate(det.drivers, 1)]
    st.dataframe(rows, hide_index=True, use_container_width=True)
    noise = sum(n.daily_increase for n in det.noise if n.daily_increase > 0)
    st.markdown(f"<span style='color:#9e9e9e'>Noise ignored: {ui.money(noise)}/day across {len(det.noise)} small changes.</span>",
                unsafe_allow_html=True)

st.divider()
mode = st.session_state["mode"]
approved = True
if mode == "bedrock":
    est = estimate_usd(s.model_fast, len(det.drivers))
    st.warning(f"This run calls Amazon Bedrock ({s.model_fast}). Estimated cost about ${est:.2f} "
               f"(cap ${s.cost_cap_usd:.0f}; spend so far is shown in the sidebar).")
    approved = st.checkbox(f"I approve spending about ${est:.2f} on this investigation")

if st.button("Investigate", type="primary", disabled=not (det.material and approved), icon=":material/search:"):
    st.session_state["pending_run"] = {"scenario": scenario, "mode": mode}
    st.switch_page("views/investigation.py")
