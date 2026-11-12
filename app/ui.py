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

NAVY = "#232F3E"      # AWS squid ink (top bar)
ORANGE = "#EC7211"    # AWS orange (primary actions, active page)
BLUE = "#0972D3"      # AWS link blue (charts, links)
ACCENT = NAVY
MUTED = "#414D5C"
BORDER = "#D1D5DB"
VERDICT_COLORS = {"likely_waste": "#D13212", "unbudgeted": "#C77700", "oversized": "#8D6E00", "expected": "#037F0C",
                  "unexplained": "#5F6B7A"}
STATUS_LABELS = {"quiet": "No alert", "awaiting_owner": "Awaiting decision", "planned": "Planned",
                 "rejected": "Rejected", "fix_in_progress": "Fix in review", "fixed": "Fixed", "needs_review": "Needs review"}
PAGES = {
    "console": ("views/console.py", "Command Center", None),
    "investigation": ("views/investigation.py", "Investigation", None),
    "case": ("views/case.py", "Cases", None),
    "inbox": ("views/inbox.py", "Inbox", None),
    "performance": ("views/performance.py", "AI performance", None),
    "results": ("views/results.py", "Settings", None),
}

CSS = f"""
<style>
  [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] {{display:none;}}
  header[data-testid="stHeader"] {{height: 0; background: transparent;}}
  footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{visibility: hidden; height: 0;}}
  .block-container, [data-testid="stMainBlockContainer"] {{max-width: 100%; padding: .2rem 1.1rem .4rem 1.1rem !important;}}
  [data-testid="stAppViewContainer"] > section, [data-testid="stMain"] {{padding-top: 0 !important;}}
  [data-testid="stElementContainer"]:has(style), .element-container:has(style) {{display: none;}}
  .st-key-topbar {{margin-top: 0 !important;}}
  [class*="fill_main_"] {{height: calc(100vh - 205px); overflow-y: auto;}}
  [class*="fill_low_"] {{height: calc(100vh - 320px); overflow-y: auto;}}
  [class*="fill_proof_"] {{height: calc(100vh - 335px); overflow-y: auto;}}
  /* top bar */
  .st-key-topbar {{background: {NAVY}; border-radius: 0; padding: 0 1.1rem; margin: 0 -1.1rem .5rem -1.1rem;
       border-bottom: 2px solid {ORANGE}; min-height: 46px;}}
  .st-key-topbar p, .st-key-topbar span, .st-key-topbar label {{color: #FFFFFF !important;}}
  .st-key-topbar [data-testid="stMarkdownContainer"] {{margin: 0; padding: 0;}}
  .st-key-topbar [data-testid="stMarkdownContainer"] p, .st-key-topbar [data-testid="stMarkdown"] {{margin: 0 !important;}}
  .brand {{font-weight: 700; font-size: 1rem; color: #FFFFFF; white-space: nowrap; line-height: 44px; letter-spacing: .01em;}}
  .st-key-topbar [data-testid="stPageLink"] {{margin: 0;}}
  .st-key-topbar [data-testid="stPageLink"] a {{background: transparent !important; padding: 0 !important;
       line-height: 44px; border-radius: 0;}}
  .st-key-topbar [data-testid="stPageLink"] a p, .st-key-topbar [data-testid="stPageLink"] a span {{color: #AAB7C4 !important;
       font-size: .88rem;}}
  .st-key-topbar [data-testid="stPageLink"] a:hover p {{color: #FFFFFF !important;}}
  .navon {{display:inline-block; color:#FFFFFF; font-weight:600; font-size:.88rem; line-height:41px;
       box-shadow: inset 0 -3px 0 {ORANGE}; white-space: nowrap;}}
  .navlive {{color:#FFFFFF; font-size:.82rem; white-space:nowrap; line-height:44px; text-align:right;}}
  .st-key-topbar [data-testid="stPopover"] button {{background: #37475A !important; border: 0 !important;
       border-radius: 999px !important; min-height: 30px !important; padding: 2px 12px 2px 4px !important;}}
  .st-key-topbar [data-testid="stPopover"] button p {{color: #FFFFFF !important; font-size: .8rem; font-weight: 600;}}
  .initials {{display:inline-block; width:22px; height:22px; border-radius:50%; background:{ORANGE}; color:#FFF;
       font-size:.66rem; font-weight:800; text-align:center; line-height:22px; margin-right:6px;}}
  /* type and cards */
  h1 {{font-size: 1.35rem !important; font-weight: 700 !important; color: #16191F; margin: 0 0 .15rem 0 !important;
       padding: 0 !important;}}
  h2, h3 {{font-size: 1rem !important; font-weight: 700 !important; color: #16191F;}}
  [data-testid="stVerticalBlockBorderWrapper"] {{border-color: #DADAD3 !important; border-radius: 8px;
       background: #FFFFFF; box-shadow: 0 1px 3px rgba(0,28,36,.08);}}
  [data-testid="stMetricValue"] {{font-size: 1.25rem !important; font-weight: 700; color: #16191F;}}
  [data-testid="stMetricLabel"] p {{color: {MUTED}; font-weight: 600; text-transform: uppercase; font-size: .72rem;
       letter-spacing: .04em;}}
  .label {{color: {MUTED}; font-size: .72rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
       margin-bottom: .25rem;}}
  .crumbs {{color: {MUTED}; font-size: .8rem; margin-bottom: .1rem;}}
  .crumbs b {{color: {BLUE}; font-weight: 600;}}
  .muted {{color: {MUTED}; font-size: .86rem;}}
  .live {{display:inline-block; width:.55rem; height:.55rem; border-radius:50%; background:#D13212; margin-right:.35rem;
       animation: pulse 1.6s infinite; vertical-align: .05rem;}}
  @keyframes pulse {{0% {{box-shadow:0 0 0 0 rgba(209,50,18,.55);}} 70% {{box-shadow:0 0 0 8px rgba(209,50,18,0);}}
       100% {{box-shadow:0 0 0 0 rgba(209,50,18,0);}}}}
  .feed {{width:100%; border-collapse: collapse; font-size:.86rem;}}
  .feed td {{padding:.42rem .3rem; border-bottom:1px solid #E9EBED; vertical-align: middle;}}
  .feed .t {{font-weight:800; color:#000; white-space:nowrap; width: 5.2rem;}}
  .feed .d {{color:#414D5C; font-size:.78rem; display:block;}}
  .feed .m {{font-weight:700; white-space:nowrap; text-align:right;}}
  .chip {{display:inline-block; border:1px solid #D1D5DB; border-radius:999px; padding:.05rem .5rem; font-size:.74rem;
       font-weight:700; margin:0 .25rem .25rem 0; background:#FFFFFF;}}
  .chip.on {{background:#037F0C; border-color:#037F0C; color:#FFFFFF;}}
  .chip.off {{color:#5F6B7A;}}
  [class*="st-key-tint_blue_"], [data-testid="stVerticalBlockBorderWrapper"]:has(> div > [class*="st-key-tint_blue_"])
       {{background: #EAF2FB !important;}}
  [class*="st-key-tint_orange_"], [data-testid="stVerticalBlockBorderWrapper"]:has(> div > [class*="st-key-tint_orange_"])
       {{background: #FFF3E6 !important;}}
  [class*="st-key-tint_green_"], [data-testid="stVerticalBlockBorderWrapper"]:has(> div > [class*="st-key-tint_green_"])
       {{background: #EAF6EC !important;}}
  [class*="st-key-tint_purple_"], [data-testid="stVerticalBlockBorderWrapper"]:has(> div > [class*="st-key-tint_purple_"])
       {{background: #F3EEFA !important;}}
  [class*="st-key-tint_sand_"], [data-testid="stVerticalBlockBorderWrapper"]:has(> div > [class*="st-key-tint_sand_"])
       {{background: #F7F3EA !important;}}
  [class*="st-key-tint_slate_"], [data-testid="stVerticalBlockBorderWrapper"]:has(> div > [class*="st-key-tint_slate_"])
       {{background: #EEF1F4 !important;}}
  [data-testid="stMetric"] {{background: #EEF1F4; border-radius: 8px; padding: .5rem .7rem !important;}}
  .fact {{color: #000000; font-weight: 800; text-transform: uppercase; letter-spacing: .03em;}}
  .factline {{font-size: .95rem; color: #16191F; margin: .1rem 0 .35rem 0;}}
  .dot {{display:inline-block; width:.6rem; height:.6rem; border-radius:50%; margin-right:.4rem; vertical-align: .02rem;}}
  .pill {{display:inline-block; color:#FFFFFF; font-weight:700; font-size:.74rem; padding:.1rem .55rem;
       border-radius: 999px; letter-spacing:.02em;}}
  .kv {{width:100%; border-collapse: collapse; font-size:.9rem;}}
  .kv td {{padding:.3rem 0; border-bottom:1px solid #E9EBED; vertical-align: top; color:#16191F;}}
  .kv td:first-child {{color:{NAVY}; width: 40%; font-weight: 700;}}
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
    _card_seq["n"] = 0
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
    """Kept for the entry point: the switcher now lives on the right of the top bar (see navigation)."""
    defaults()
    return actor()


def navigation() -> None:
    """Top bar: brand, the pages this actor's role allows, and the actor switcher on the right."""
    defaults()
    style()
    a = actor()
    pages = [st.Page(PAGES[p][0], title=PAGES[p][1], icon=PAGES[p][2], url_path=p, default=(i == 0))
             for i, p in enumerate(a.pages)]
    nav = st.navigation(pages, position="hidden")
    if st.session_state.pop("actor_changed", False):
        st.switch_page(pages[0], query_params={"actor": a.actor_id})
    st.query_params["actor"] = a.actor_id
    st.query_params["day"] = st.session_state["scenario"]
    with st.container(key="topbar"):
        cols = st.columns([1.5] + [1.05] * len(pages) + [0.9, 1.25], vertical_alignment="center")
        cols[0].markdown("<div class='brand'>Agentic FinOps</div>", unsafe_allow_html=True)
        for col, page, key in zip(cols[1:-2], pages, a.pages):
            if page.title == nav.title:
                col.markdown(f"<span class='navon'>{page.title}</span>", unsafe_allow_html=True)
            else:
                col.page_link(page, query_params={"actor": a.actor_id, "day": st.session_state["scenario"]})
        n_open = _open_count(a.actor_id) if not a.sees_all_cases else sum(
            1 for s in demo_stores() for c in s.cases() if c.status == "awaiting_owner")
        cols[-2].markdown(f"<div class='navlive'>{'<span class=live></span>' if n_open else ''}{n_open} open</div>",
                          unsafe_allow_html=True)
        initials = "".join(w[0] for w in a.name.replace(".", "").split()[:2]).upper()
        with cols[-1].popover(f"{initials} · {a.name}", use_container_width=True):

            def label(x: str) -> str:
                n = _open_count(x)
                return f"{rbac.ACTORS[x].name} · {rbac.ACTORS[x].role}" + (f"  ({n} open)" if n else "")

            st.selectbox("Acting as", list(rbac.ACTORS), format_func=label, key="actor", on_change=_actor_changed)
    nav.run()


_card_seq = {"n": 0}


TINTS = {"blue": "#EAF2FB", "orange": "#FFF3E6", "green": "#EAF6EC", "purple": "#F3EEFA", "sand": "#F7F3EA",
         "slate": "#EEF1F4"}


def card(title: str, fill: str = "main", height: int | None = None, tint: str | None = "slate"):
    """A bordered card with a small uppercase title. Its height follows the window (fill = how much sits above it:
    "main" under a page header, "low" under a header plus a row of stat cards), or is fixed when `height` is given;
    `tint` gives the card its own light background colour. Long content scrolls inside."""
    _card_seq["n"] += 1
    t = f"tint_{tint}_" if tint else ""
    box = (st.container(border=True, height=height, key=f"{t}card_{_card_seq['n']}") if height
           else st.container(border=True, key=f"{t}fill_{fill}_{_card_seq['n']}"))
    box.markdown(f"<div class='label'>{title}</div>", unsafe_allow_html=True)
    return box


def pill(verdict: str) -> str:
    return f"<span class='pill' style='background:{VERDICT_COLORS[verdict]}'>{VERDICT_LABELS[verdict]}</span>"


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


def facts(items: list[tuple[str, str]], lead: str = "") -> str:
    """A free-text summary line: each label in black, bold capitals, followed by its value."""
    parts = [f"<span class='fact'>{k}</span> {v}" if k else f"<span class='fact'>{v}</span>" for k, v in items]
    return (f"{lead} · " if lead else "") + " &nbsp;·&nbsp; ".join(parts)


def facts_line(items: list[tuple[str, str]], lead: str = "") -> None:
    st.markdown(f"<div class='factline'>{facts(items, lead)}</div>", unsafe_allow_html=True)


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


def evidence_table(source: FixtureSource, ids: list[str]) -> None:
    rows = []
    for eid in dict.fromkeys(ids):
        rec = source.record(eid)
        kind, detail = describe(eid, rec) if rec else ("Not found", "")
        rows.append({"Source": kind, "Detail": detail, "ID": eid})
    st.dataframe(rows, hide_index=True, width="stretch")


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
    items = [("Confidence", f"{r.confidence} of 4")]
    if case.assigned_to:
        items.append(("Owner", actor_name(case.assigned_to)))
    items.append(("Status", STATUS_LABELS[case.status].lower()))
    return f"<div class='factline'>{pill(r.verdict)} &nbsp; {facts(items)}</div>"
