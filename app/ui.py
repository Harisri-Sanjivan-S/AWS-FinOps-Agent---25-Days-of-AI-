"""Shared Streamlit helpers: sidebar (mode, scenario, run, cost meter), badges, citation popovers, message card."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from detective.cases import RunStore, list_runs
from detective.config import get_settings
from detective.fixturegen import scenario_paths
from detective.ledger import Ledger
from detective.models import VERDICT_LABELS, Case
from detective.sources import FixtureSource

MODES = {"offline": "Offline replay (scripted, no AI, $0)", "bedrock": "Amazon Bedrock (real AI, costs money)"}
VERDICT_COLORS = {"likely_waste": "#c62828", "unbudgeted": "#ef6c00", "oversized": "#f9a825", "expected": "#2e7d32",
                  "unexplained": "#6d4c41"}
STATUS_LABELS = {"quiet": "Quiet (no alert)", "awaiting_owner": "Awaiting owner", "planned": "Planned (exception)",
                 "rejected": "Rejected", "fix_in_progress": "Fix in review", "fixed": "Fixed", "needs_review": "Needs human review"}
PERSONAS = ["finops-team", "ravi.m", "priya.s", "orders-team", "platform-team"]


def page(title: str) -> None:
    st.set_page_config(page_title=f"Agentic FinOps · {title}", page_icon=":material/query_stats:", layout="wide")
    sidebar()


def scenarios() -> dict[str, str]:
    return {p.stem: json.loads(p.read_text())["description"] for p in scenario_paths()}


def sidebar() -> None:
    s = get_settings()
    st.session_state.setdefault("scenario", "tuesday")
    st.session_state.setdefault("mode", "offline")
    with st.sidebar:
        st.markdown("### Agentic FinOps on AWS")
        st.caption("Every cost spike explained by the next morning, with proof.")
        names = list(scenarios())
        st.selectbox("Scenario (planted data shaped like AWS)", names, key="scenario")
        st.radio("Investigator", list(MODES), format_func=MODES.get, key="mode")
        runs = list_runs()
        if runs:
            labels = {r.dir.name: f"{r.meta()['scenario_id']} · {r.meta()['mode']} · {r.dir.name[:15]}" for r in runs}
            current = st.session_state.get("run_dir")
            options = list(labels)
            index = options.index(Path(current).name) if current and Path(current).name in labels else 0
            choice = st.selectbox("Run", options, index=index, format_func=labels.get)
            st.session_state["run_dir"] = str(s.runs_dir / choice)
        status = Ledger().status()
        color = {"ok": "green", "warning": "orange", "blocked": "red"}[status["level"]]
        st.markdown(f"**Bedrock spend:** :{color}[${status['total_usd']:.4f}] of ${status['cap_usd']:.0f} cap")
        st.caption(f"Region {s.aws_region} · fast {s.model_fast.split('.')[-1][:28]}")


def current_store() -> RunStore | None:
    run_dir = st.session_state.get("run_dir")
    return RunStore(Path(run_dir)) if run_dir and (Path(run_dir) / "run.json").exists() else None


def badge(verdict: str, big: bool = False) -> str:
    size = "1.05rem" if big else "0.8rem"
    return (f"<span style='background:{VERDICT_COLORS[verdict]};color:white;padding:2px 10px;border-radius:12px;"
            f"font-size:{size};font-weight:600'>{VERDICT_LABELS[verdict]}</span>")


def pill(text: str, color: str = "#455a64") -> str:
    return (f"<span style='border:1px solid {color};color:{color};padding:1px 8px;border-radius:10px;"
            f"font-size:0.75rem'>{text}</span>")


def money(x: float) -> str:
    return f"${x:,.2f}"


def evidence_popover(source: FixtureSource, eid: str, key: str) -> None:
    with st.popover(eid, use_container_width=False):
        record = source.record(eid)
        if record is None:
            st.write("Not found")
        else:
            shown = {k: v for k, v in record.items() if k not in ("CloudTrailEvent", "timestamps", "values")}
            if "values" in record:
                shown["values (first 6)"] = record["values"][:6]
            st.json(shown, expanded=True)


def citations(source: FixtureSource, ids: list[str], key: str, limit: int = 6) -> None:
    cols = st.columns(min(len(ids), limit) or 1)
    for i, eid in enumerate(ids[:limit]):
        with cols[i]:
            evidence_popover(source, eid, f"{key}-{i}")
    if len(ids) > limit:
        st.caption(f"+{len(ids) - limit} more: {', '.join(ids[limit:])}")


def message_card(case: Case) -> None:
    r = case.report
    with st.container(border=True):
        st.markdown("<div style='font-size:0.8rem;color:#616161'>Slack / Teams preview (production path) · #finops-alerts</div>",
                    unsafe_allow_html=True)
        st.markdown(f"**Agentic FinOps** &nbsp; {badge(r.verdict)} &nbsp; **{case.case_id}** · {case.driver.title}",
                    unsafe_allow_html=True)
        lines = [f"@{case.assigned_to}: {money(case.driver.daily_increase)}/day since {r.cost_start[:16].replace('T', ' ')} UTC.",
                 f"Confidence {r.confidence} of 4" + (" · please confirm" if r.needs_confirmation else "") + ".",
                 f"Already spent {money(r.already_spent)} · avoidable {money(r.avoidable_per_day)}/day."]
        st.markdown("  \n".join(lines))
        st.caption("Buttons in the real message: Planned · Not mine · Approve fix · Reject")
