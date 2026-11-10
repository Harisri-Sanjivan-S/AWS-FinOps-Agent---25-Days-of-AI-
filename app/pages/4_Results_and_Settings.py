"""Measured results from the eval runner, plus settings: models, cost ledger, Devin connection, reset."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import json

import streamlit as st

from detective.config import ROOT, get_settings
from detective.fixers.devin import devin_status
from detective.ledger import Ledger

ui.page("Results")
s = get_settings()
st.title("Results and settings")

st.subheader("Measured results")
results = sorted((ROOT / "evals" / "results").glob("*.json"), reverse=True)
if not results:
    st.info("No measured results yet. Run `uv run python -m evals.run --mode bedrock --runs 3 --confirm` "
            "(shows a cost estimate first).")
else:
    choice = st.selectbox("Result file", results, format_func=lambda p: p.name)
    data = json.loads(choice.read_text())
    summary = data["summary"]
    cols = st.columns(len(summary["metrics"]))
    for col, (k, v) in zip(cols, summary["metrics"].items()):
        col.metric(k, v)
    st.dataframe(data["cases"], hide_index=True, use_container_width=True)
    if data.get("baseline"):
        st.markdown("**Naive baseline** (all data in one prompt, no tools, no rules)")
        st.dataframe(data["baseline"], hide_index=True, use_container_width=True)
    st.caption(f"Mode {data['mode']} · model {data['model']} · runs {data['runs']} · {data['created']}")

st.divider()
st.subheader("Settings")
c1, c2 = st.columns(2)
with c1:
    st.markdown(f"**Region:** {s.aws_region} · **profile:** {s.aws_profile}")
    st.markdown(f"**Fast model:** `{s.model_fast}`  \n**Escalation model:** `{s.model_strong}`")
    st.markdown(f"**Agent limits:** {s.tool_call_cap} tool calls, {s.turns} turns, {s.total_tokens:,} tokens, "
                f"{s.lookback_hours} h look-back, temperature 0")
    st.markdown("**Data:** planted, shaped like AWS. Mirrors CUR 2.0, CloudTrail, CloudWatch, Budgets, Cost Anomaly Detection.")
with c2:
    d = devin_status(s.devin_bin)
    st.markdown(f"**Code-fix agent:** default `{s.fixer}` · Devin CLI: {d['detail']}")
    st.caption("Connect to Devin = CLI login on this machine (`devin auth login`). Production path: Devin API with a "
               "service-user key stored in AWS Secrets Manager.")
    st.markdown(f"**Demo repo:** [{s.demo_repo}](https://github.com/{s.demo_repo})")

st.subheader("Bedrock cost ledger")
ledger = Ledger()
status = ledger.status()
st.progress(min(1.0, status["total_usd"] / status["cap_usd"]),
            text=f"${status['total_usd']:.4f} of ${status['cap_usd']:.0f} guard (project ceiling $30)")
entries = ledger.entries()
if entries:
    st.dataframe(entries[-50:][::-1], hide_index=True, use_container_width=True)
else:
    st.caption("No Bedrock calls recorded yet.")

st.subheader("Reset demo")
st.caption("Deletes local runs and cases (the cost ledger is kept). To also close demo PRs: "
           "`uv run python scripts/reset_demo.py --yes --github`.")
if st.checkbox("I want to delete all local runs") and st.button("Reset local runs", type="secondary"):
    from scripts.reset_demo import reset_local
    removed = reset_local(yes=True)
    st.session_state.pop("run_dir", None)
    st.success(f"Removed {len(removed)} run(s).")
