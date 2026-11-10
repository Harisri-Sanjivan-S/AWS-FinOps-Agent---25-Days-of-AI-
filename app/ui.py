"""Shared Streamlit helpers: layout and style, actor switcher, role-based navigation, breadcrumbs, status dots,
evidence links. Design: calm, uniform, one accent colour; detail is one click away, never on the surface."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from detective import rbac
from detective.cases import RunStore
from detective.config import get_settings
from detective.fixturegen import scenario_paths
from detective.models import VERDICT_LABELS, Case
from detective.sources import FixtureSource
from evals.score import all_runs, latest_runs

ACCENT = "#1F3A5F"
MUTED = "#6B7280"
VERDICT_COLORS = {"likely_waste": "#B4413C", "unbudgeted": "#B7791F", "oversized": "#A08A2A", "expected": "#3C7A57",
                  "unexplained": "#6B7280"}
STATUS_LABELS = {"quiet": "No alert", "awaiting_owner": "Awaiting decision", "planned": "Planned",
                 "rejected": "Rejected", "fix_in_progress": "Fix in review", "fixed": "Fixed", "needs_review": "Needs review"}
PAGES = {
    "console": ("views/console.py", "Console", ":material/query_stats:"),
    "investigation": ("views/investigation.py", "Investigation", ":material/manage_search:"),
    "case": ("views/case.py", "Cases", ":material/description:"),
    "inbox": ("views/inbox.py", "Inbox", ":material/inbox:"),
    "performance": ("views/performance.py", "AI performance", ":material/monitoring:"),
    "results": ("views/results.py", "Settings", ":material/tune:"),
}

CSS = f"""
<style>
  .block-container {{max-width: 1080px; padding-top: 2.2rem; padding-bottom: 3rem;}}
  footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{visibility: hidden; height: 0;}}
  h1 {{font-size: 1.55rem !important; font-weight: 600 !important; margin-bottom: .2rem !important;}}
  h2 {{font-size: 1.15rem !important; font-weight: 600 !important;}}
  h3 {{font-size: 1.0rem !important; font-weight: 600 !important; color: {ACCENT};}}
  [data-testid="stMetricValue"] {{font-size: 1.1rem !important;}}
  .crumbs {{color: {MUTED}; font-size: .82rem; letter-spacing: .01em; margin-bottom: .15rem;}}
  .crumbs b {{color: {ACCENT}; font-weight: 600;}}
  .muted {{color: {MUTED}; font-size: .88rem;}}
  .dot {{display:inline-block; width:.55rem; height:.55rem; border-radius:50%; margin-right:.4rem; vertical-align: .05rem;}}
  .kv {{width:100%; border-collapse: collapse; font-size:.92rem;}}
  .kv td {{padding:.32rem 0; border-bottom:1px solid #EEF1F4; vertical-align: top;}}
  .kv td:first-child {{color:{MUTED}; width: 38%;}}
  [data-testid="stSidebar"] {{border-right: 1px solid #EEF1F4;}}
</style>
"""


def style() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


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
    style()
    a = actor()
    if name not in a.pages:
        st.error(f"{a.name} ({a.role}) does not have access to this page.")
        st.stop()
    return a


def header(title: str, *crumbs: str) -> None:
    """Breadcrumb trail, then a plain page title."""
    trail = " › ".join([*crumbs[:-1], f"<b>{crumbs[-1]}</b>"]) if crumbs else ""
    if trail:
        st.markdown(f"<div class='crumbs'>{trail}</div>", unsafe_allow_html=True)
    st.title(title)


# --- demo days and runs -------------------------------------------------------------------------------------

def scenario_names() -> list[str]:
    """The demo week (7-14 Nov 2026). Test-only scenarios are not shown in the app."""
    return [p.stem for p in scenario_paths() if json.loads(p.read_text()).get("week")]


def _scenario(name: str) -> dict:
    return json.loads((ROOT / "scenarios" / f"{name}.json").read_text())


def day_short(name: str) -> str:
    return f"{date.fromisoformat(_scenario(name)['spike_date']):%a %d %b}"


def scenario_label(name: str) -> str:
    sc = _scenario(name)
    return f"{day_short(name)} · {sc.get('title', name)}"


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


# --- sidebar and navigation ---------------------------------------------------------------------------------

def _actor_changed() -> None:
    st.session_state["actor_changed"] = True


def actor_switcher() -> rbac.Actor:
    """Top of the sidebar: who is using the app. Each actor gets only the pages and cases their role allows."""
    defaults()
    style()
    with st.sidebar:
        st.markdown(f"<div style='font-weight:600;color:{ACCENT};font-size:1rem;margin-bottom:.6rem'>"
                    "Agentic FinOps</div>", unsafe_allow_html=True)

        def label(a: str) -> str:
            x, n = rbac.ACTORS[a], _open_count(a)
            return f"{x.name}" + (f"  ({n})" if n else "")

        st.selectbox("Acting as", list(rbac.ACTORS), format_func=label, key="actor", on_change=_actor_changed)
        st.markdown(f"<div class='muted' style='margin-top:-.4rem'>{actor().role}</div>", unsafe_allow_html=True)
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
        st.divider()
        for page in pages:
            st.page_link(page, query_params={"actor": a.actor_id, "day": st.session_state["scenario"]})
    nav.run()


# --- small presentation helpers -----------------------------------------------------------------------------

def dot(verdict: str) -> str:
    """Quiet status: a small coloured dot and the label."""
    return f"<span class='dot' style='background:{VERDICT_COLORS[verdict]}'></span>{VERDICT_LABELS[verdict]}"


def badge(verdict: str, big: bool = False) -> str:  # kept for compatibility; renders as a quiet dot
    return dot(verdict)


def kv(rows: list[tuple[str, str]]) -> None:
    """A clean key-value list."""
    body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    st.markdown(f"<table class='kv'>{body}</table>", unsafe_allow_html=True)


def muted(text: str) -> None:
    st.markdown(f"<div class='muted'>{text}</div>", unsafe_allow_html=True)


def money(x: float) -> str:
    return f"${x:,.2f}"


def model_label(model_id: str) -> str:
    return model_id.split(".")[-1].split("-v1")[0]


def actor_name(actor_id: str | None) -> str:
    return rbac.ACTORS[actor_id].name if actor_id in rbac.ACTORS else (actor_id or "—")


def md(text: str) -> str:
    """Escape '$' so amounts are not rendered as maths by Markdown."""
    return text.replace("$", "\\$")


def _when(ts: str) -> str:
    return ts[:16].replace("T", " ") + " UTC" if ts else ""


def describe(eid: str, r: dict) -> tuple[str, str]:
    """(source, plain-English detail) for one evidence record."""
    if "line_item_unblended_cost" in r:
        hours = r["line_item_usage_amount"]
        unit = r["pricing_unit"]
        return ("Bill", (f"{r['line_item_resource_id']} · {hours:g} {unit} × ${r['line_item_unblended_rate']:.3f} = "
                        f"${r['line_item_unblended_cost']:,.2f} on {r['line_item_usage_start_date'][:10]}"))
    if "EventName" in r:
        return ("CloudTrail", (f"{r['EventName']} by {r['Username']} · {_when(r['EventTime'])} · "
                              f"{len(r['Resources'])} resource(s)"))
    if eid.startswith("tag-"):
        tags = ", ".join(f"{k}={v}" for k, v in r["tags"].items()) or "no tags"
        return ("Tags", f"{r['resource_id']} · {tags}")
    if eid.startswith("met-"):
        vals = r["values"]
        res = next(iter(r["dimensions"].values()))
        if r["unit"] == "Bytes":
            return ("CloudWatch", f"{r['metric_name']} on {res} · peak {max(vals) / 1e9:,.0f} GB/h")
        return ("CloudWatch", f"{r['metric_name']} on {res} · average {sum(vals) / len(vals):.1f}%, peak {max(vals):.1f}%")
    if "key" in r and "status" in r:
        return ("Ticket", f"{r['key']} · {r['status']} · {r['summary']}")
    if "BudgetName" in r:
        return ("Budget", (f"{r['BudgetName']} · ${r['CalculatedSpend']['ActualSpend']['Amount']:,.2f} of "
                          f"${r['BudgetLimit']['Amount']:,.2f}"))
    if eid.startswith("dep-"):
        return ("Deployment", f"{r['service']} {r['version']} · {r['team']} · {r['note']}")
    if eid.startswith("net-"):
        top = max(r["destinations"], key=lambda d: d["share_pct"])
        return ("Flow logs", f"{top['share_pct']:.0f}% of NAT traffic to {top['destination']}")
    if eid.startswith("exc-"):
        return ("Exception", f"{r['reason']} · expires {r['expires']}")
    if "Impact" in r:
        return ("Anomaly", f"Actual ${r['Impact']['TotalActualSpend']:,.2f} vs expected ${r['Impact']['TotalExpectedSpend']:,.2f}")
    return ("Record", eid)


def citations(source: FixtureSource, ids: list[str], limit: int = 0) -> None:
    """One small 'Evidence' link under a sentence; it opens a readable list of the cited records."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return
    with st.popover(f"Evidence ({len(ids)})"):
        rows = []
        for eid in ids:
            rec = source.record(eid)
            kind, detail = describe(eid, rec) if rec else ("Not found", "")
            rows.append({"Source": kind, "Detail": detail, "ID": eid})
        st.dataframe(rows, hide_index=True, width="stretch")


def case_status_line(case: Case) -> str:
    r = case.report
    if not r:
        return "Needs review"
    parts = [dot(r.verdict), f"confidence {r.confidence} of 4", STATUS_LABELS[case.status].lower()]
    if case.assigned_to:
        parts.insert(2, f"owner {actor_name(case.assigned_to)}")
    return " · ".join(parts)
