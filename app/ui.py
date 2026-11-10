"""Shared Streamlit helpers: actor switcher, role-based navigation, badges, citation popovers, message card."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from detective import rbac
from detective.cases import RunStore
from detective.config import get_settings
from detective.fixturegen import scenario_paths
from detective.ledger import Ledger
from detective.models import VERDICT_LABELS, Case
from detective.sources import FixtureSource
from evals.score import all_runs, latest_runs

VERDICT_COLORS = {"likely_waste": "#c62828", "unbudgeted": "#ef6c00", "oversized": "#f9a825", "expected": "#2e7d32",
                  "unexplained": "#6d4c41"}
STATUS_LABELS = {"quiet": "Quiet (no alert)", "awaiting_owner": "Awaiting owner", "planned": "Planned (exception)",
                 "rejected": "Rejected", "fix_in_progress": "Fix in review", "fixed": "Fixed", "needs_review": "Needs human review"}
PAGES = {
    "console": ("views/console.py", "Console", ":material/query_stats:"),
    "investigation": ("views/investigation.py", "Investigation", ":material/manage_search:"),
    "case": ("views/case.py", "Case report", ":material/description:"),
    "inbox": ("views/inbox.py", "Inbox", ":material/inbox:"),
    "performance": ("views/performance.py", "AI performance & proof", ":material/monitoring:"),
    "results": ("views/results.py", "Results and settings", ":material/tune:"),
}


def defaults() -> None:
    # A browser refresh starts a new session: restore the actor and the day from the URL (?actor=...&day=...).
    qp = st.query_params
    if "actor" not in st.session_state and qp.get("actor") in rbac.ACTORS:
        st.session_state["actor"] = qp["actor"]
    if "scenario" not in st.session_state and qp.get("day") in scenario_names():
        st.session_state["scenario"] = qp["day"]
    st.session_state.setdefault("actor", "finops-team")
    st.session_state.setdefault("scenario", "nov10")


def run_mode() -> str:
    """Always Amazon Bedrock in the app (the automated tests set FINOPS_UI_MODE=offline)."""
    return get_settings().ui_mode


def actor() -> rbac.Actor:
    defaults()
    return rbac.get_actor(st.session_state["actor"])


def require_page(name: str) -> rbac.Actor:
    """Guard inside each view, so a page can never render for an actor whose role does not include it."""
    a = actor()
    if name not in a.pages:
        st.error(f"{a.name} ({a.role}) does not have access to this page.")
        st.stop()
    return a


def scenario_names() -> list[str]:
    """The demo week (7-14 Nov 2026). Test-only scenarios are not shown in the app."""
    return [p.stem for p in scenario_paths() if json.loads(p.read_text()).get("week")]


def scenario_label(name: str) -> str:
    from datetime import date
    sc = json.loads((ROOT / "scenarios" / f"{name}.json").read_text())
    day = date.fromisoformat(sc["spike_date"])
    return f"{day:%a %d %b} · {sc.get('title', name)}"


def demo_stores() -> list[RunStore]:
    """The latest Bedrock run of every demo day: what owners work on."""
    return latest_runs(mode=run_mode())


def history_stores() -> list[RunStore]:
    """Every Bedrock run of the demo week (proof of past actions is never hidden by a re-run)."""
    return all_runs(mode=run_mode())


def day_store(scenario: str) -> RunStore | None:
    return next((s for s in demo_stores() if s.meta()["scenario_id"] == scenario), None)


def demo_cases(actor_id: str) -> list[tuple[RunStore, Case]]:
    """(store, case) for every case this actor may see, across all demo days, oldest day first."""
    return [(s, c) for s in demo_stores() for c in rbac.visible_cases(actor_id, s.cases())]


def _open_count(actor_id: str) -> int:
    return sum(1 for s in demo_stores() for c in s.cases() if c.assigned_to == actor_id and c.status == "awaiting_owner")


def _actor_changed() -> None:
    st.session_state["actor_changed"] = True


def actor_switcher() -> rbac.Actor:
    """Top of the sidebar: who is using the app. Each actor gets only the pages and cases their role allows."""
    defaults()
    with st.sidebar:
        st.markdown("### Agentic FinOps on AWS")

        def label(a: str) -> str:
            x, n = rbac.ACTORS[a], _open_count(a)
            return f"{x.name} · {x.role}" + (f" ({n})" if n else "")

        st.selectbox("Acting as", list(rbac.ACTORS), format_func=label, key="actor", on_change=_actor_changed,
                     help="Role-based access: each actor sees only their pages, their cases and their actions.")
    return actor()


def navigation() -> None:
    """Build role-based navigation (hidden default nav) and render links under the actor switcher."""
    a = actor()
    pages = [st.Page(PAGES[p][0], title=PAGES[p][1], icon=PAGES[p][2], url_path=p, default=(i == 0))
             for i, p in enumerate(a.pages)]
    nav = st.navigation(pages, position="hidden")
    if st.session_state.pop("actor_changed", False):
        st.switch_page(pages[0], query_params={"actor": a.actor_id})
    st.query_params["actor"] = a.actor_id
    st.query_params["day"] = st.session_state["scenario"]
    with st.sidebar:
        for page in pages:
            st.page_link(page, query_params={"actor": a.actor_id, "day": st.session_state["scenario"]})
        st.divider()
        if a.can_investigate:
            status = Ledger().status()
            color = {"ok": "green", "warning": "orange", "blocked": "red"}[status["level"]]
            st.markdown(f"**Bedrock spend:** :{color}[${status['total_usd']:.4f}] of ${status['cap_usd']:.0f} cap")
        st.caption("Every cost spike explained by the next morning, with proof.")
    nav.run()


def badge(verdict: str, big: bool = False) -> str:
    size = "1.05rem" if big else "0.8rem"
    return (f"<span style='background:{VERDICT_COLORS[verdict]};color:white;padding:2px 10px;border-radius:12px;"
            f"font-size:{size};font-weight:600'>{VERDICT_LABELS[verdict]}</span>")


def money(x: float) -> str:
    return f"${x:,.2f}"


def model_label(model_id: str) -> str:
    return model_id.split(".")[-1].split("-v1")[0]


def actor_name(actor_id: str | None) -> str:
    return rbac.ACTORS[actor_id].name if actor_id in rbac.ACTORS else (actor_id or "nobody (quiet)")


def evidence_popover(source: FixtureSource, eid: str) -> None:
    with st.popover(eid):
        record = source.record(eid)
        if record is None:
            st.write("Not found")
        else:
            shown = {k: v for k, v in record.items() if k not in ("CloudTrailEvent", "timestamps", "values")}
            if "values" in record:
                shown["values (first 6)"] = record["values"][:6]
            st.json(shown, expanded=True)


def citations(source: FixtureSource, ids: list[str], limit: int = 6) -> None:
    cols = st.columns(min(len(ids), limit) or 1)
    for i, eid in enumerate(ids[:limit]):
        with cols[i]:
            evidence_popover(source, eid)
    if len(ids) > limit:
        st.caption(f"+{len(ids) - limit} more: {', '.join(ids[limit:])}")


def message_card(case: Case) -> None:
    r = case.report
    with st.container(border=True):
        st.markdown("<div style='font-size:0.8rem;color:#616161'>Notification · #finops-alerts</div>", unsafe_allow_html=True)
        st.markdown(f"**Agentic FinOps** &nbsp; {badge(r.verdict)} &nbsp; **{case.case_id}** · {case.driver.title}",
                    unsafe_allow_html=True)
        owner = rbac.ACTORS[case.assigned_to].name if case.assigned_to in rbac.ACTORS else case.assigned_to
        lines = [f"@{owner}: {money(case.driver.daily_increase)}/day since {r.cost_start[:16].replace('T', ' ')} UTC.",
                 f"Confidence {r.confidence} of 4" + (" · please confirm" if r.needs_confirmation else "") + ".",
                 f"Already spent {money(r.already_spent)} · avoidable {money(r.avoidable_per_day)}/day."]
        st.markdown("  \n".join(lines))
