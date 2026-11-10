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
from detective.cases import RunStore, list_runs
from detective.config import get_settings
from detective.fixturegen import scenario_paths
from detective.ledger import Ledger
from detective.models import VERDICT_LABELS, Case
from detective.sources import FixtureSource

MODES = {"bedrock": "Amazon Bedrock (Claude)", "offline": "Dry run (no model calls, $0)"}
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
    st.session_state.setdefault("actor", "finops-team")
    st.session_state.setdefault("scenario", "nov10")
    st.session_state.setdefault("mode", "bedrock")  # the app always uses Bedrock; tests set "offline"


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


def current_store() -> RunStore | None:
    run_dir = st.session_state.get("run_dir")
    if run_dir and (Path(run_dir) / "run.json").exists():
        return RunStore(Path(run_dir))
    runs = list_runs()
    return runs[0] if runs else None


def _open_count(actor_id: str) -> int:
    store = current_store()
    return sum(1 for c in store.cases() if c.assigned_to == actor_id and c.status == "awaiting_owner") if store else 0


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
        st.switch_page(pages[0])
    with st.sidebar:
        for page in pages:
            st.page_link(page)
        st.divider()
        if a.can_investigate:
            runs = list_runs()
            if runs:
                labels = {r.dir.name: f"{r.meta()['scenario_id']} · {r.dir.name[4:8]} {r.dir.name[9:13]}" for r in runs}
                options = list(labels)
                current = Path(st.session_state["run_dir"]).name if st.session_state.get("run_dir") else options[0]
                choice = st.selectbox("Run", options, index=options.index(current) if current in options else 0,
                                      format_func=labels.get)
                st.session_state["run_dir"] = str(get_settings().runs_dir / choice)
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
    return "dry run" if model_id == "scripted" else model_id.split(".")[-1].split("-v1")[0]


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
