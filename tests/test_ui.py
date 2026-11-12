"""Headless tests of the Streamlit app and role-based access. Tests set FINOPS_UI_MODE=offline (scripted investigator);
the app itself always uses Bedrock. No AWS, no GitHub."""

import pytest
from streamlit.testing.v1 import AppTest

from detective import config
from detective.cases import list_runs
from detective.config import ROOT
from detective.pipeline import investigate_all

APP = ROOT / "app"
HIDDEN_WORDS = ["planted", "offline", "replay", "anomaly detection", "shaped like", "mirror", "dry run",
                "noise ignored", "explained by the next morning", "bedrock spend"]


def run(name: str, **state) -> AppTest:
    at = AppTest.from_file(str(APP / name), default_timeout=60)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def no_errors(at: AppTest) -> None:
    assert not at.exception, [e.message for e in at.exception]


def text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.title]
    parts += [i.value for i in at.info] + [w.value for w in at.warning] + [s.value for s in at.success]
    return " ".join(str(p) for p in parts)


def buttons(at: AppTest) -> list[str]:
    return [b.label for b in at.button]


@pytest.fixture
def week():
    """Two demo days with cases for Ravi on both (10 and 13 Nov), plus the Orders-team NAT case on 10 Nov."""
    for day in ("nov10", "nov13"):
        investigate_all(day, "offline")


def test_router_shows_pages_by_role(week):
    finops = run("Home.py", actor="finops-team")
    no_errors(finops)
    assert finops.title[0].value == "Command Center"
    assert any(s.label == "Acting as" for s in finops.selectbox)
    assert not [w for w in HIDDEN_WORDS if w in text(finops).lower()]
    owner = run("Home.py", actor="ravi.m")
    no_errors(owner)
    assert owner.title[0].value == "Inbox", "owners land on their inbox; console is not in their navigation"


def test_refresh_keeps_actor_and_day_from_the_url(week):
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=60)
    at.query_params["actor"] = "ravi.m"
    at.query_params["day"] = "nov13"
    at.run()  # fresh session, as after a browser refresh
    no_errors(at)
    assert at.session_state["actor"] == "ravi.m" and at.title[0].value == "Inbox"
    assert at.session_state["scenario"] == "nov13"
    bad = AppTest.from_file(str(APP / "Home.py"), default_timeout=60)
    bad.query_params["actor"] = "mallory"
    bad.run()
    assert bad.session_state["actor"] == "finops-team", "unknown actors in the URL are ignored"


def test_console_is_clean_and_breadcrumbed():
    at = run("views/console.py")
    no_errors(at)
    page = text(at)
    assert "Console</b>" not in page and "Tue 10 Nov" in page, "breadcrumb shows the selected day"
    assert "Command Center" in page and "Incidents · last 24 hours" in page and "Where the increase went" in page
    assert "Spend today</div>" in page and "$4,496.44" in page
    assert "Investigate" in buttons(at) and "Estimated cost" in page
    assert not [w for w in HIDDEN_WORDS if w in page.lower()], page


def test_console_bedrock_asks_for_cost_approval_before_running(monkeypatch):
    monkeypatch.setenv("FINOPS_UI_MODE", "bedrock")
    config.get_settings.cache_clear()
    at = run("views/console.py")
    no_errors(at)
    at = next(b for b in at.button if b.label == "Investigate").click().run()
    no_errors(at)
    assert "pending_run" not in at.session_state, "nothing runs until the cost is approved in the confirmation"


def test_quiet_day_explains_why_nothing_runs():
    at = run("views/console.py", scenario="nov07")
    no_errors(at)
    assert "Below the alert threshold. Nothing to investigate." in text(at)
    assert "Investigate" not in buttons(at)


def test_owner_cannot_open_finops_pages():
    for view in ("views/console.py", "views/investigation.py", "views/results.py"):
        at = run(view, actor="ravi.m")
        no_errors(at)
        assert any("does not have access" in e.value for e in at.error)


def test_live_investigation_then_replay_follows_the_selected_day():
    at = run("views/investigation.py", pending_run={"scenario": "nov10", "mode": "offline"})
    no_errors(at)
    [store] = list_runs()
    assert [c.status for c in store.cases()] == ["awaiting_owner", "quiet", "awaiting_owner"]
    assert any("Investigation finished" in s.value for s in at.success)
    replay = run("views/investigation.py", scenario="nov10")
    no_errors(replay)
    assert len(replay.selectbox[1].options) == 3
    page = text(replay)
    assert "Incident timeline" in page and "Evidence map" in page and "The four checks" in page
    assert "Checking the bill lines" in page, "the agent's notes sit beside its tool calls"
    other = run("views/investigation.py", scenario="nov08")
    assert "not been investigated yet" in text(other)


def test_case_page_lists_own_cases_across_days(week):
    at = run("views/case.py", actor="ravi.m")
    no_errors(at)
    assert [o.split(" · ")[0] for o in at.selectbox[0].options] == ["N10-01", "N13-01"]
    page = text(at)
    assert "$888.33" in page and "Likely waste" in page and "Cases" in page
    finops = run("views/case.py", actor="finops-team")
    assert len(finops.selectbox[0].options) == 4


def test_inbox_owner_sees_every_day_and_approves(week):
    at = run("views/inbox.py", actor="ravi.m")
    no_errors(at)
    assert [o.split(" · ")[0] for o in at.selectbox[0].options] == ["N10-01", "N13-01"]
    at = next(b for b in at.button if b.label == "Approve stop").click().run()
    no_errors(at)
    at = run("views/inbox.py", actor="ravi.m", inbox_filter="All", inbox_case="N10-01")
    at = next(b for b in at.button if b.label == "Next day: check the bill").click().run()
    no_errors(at)
    assert any("Saving confirmed" in s.value for s in at.success)
    orders = run("views/inbox.py", actor="orders-team")
    assert [o.split(" · ")[0] for o in orders.selectbox[0].options] == ["N10-03"]


def test_inbox_finops_sees_all_but_cannot_approve_others(week):
    at = run("views/inbox.py", actor="finops-team", inbox_filter="All")
    no_errors(at)
    assert len(at.selectbox[0].options) == 4
    assert not {"Approve stop", "Approve fix and open PR"} & set(buttons(at))
    assert any(t.label == "Re-route" for t in at.tabs), "FinOps may re-route any case"
    quiet = run("views/inbox.py", actor="finops-team", inbox_filter="All", inbox_case="N10-02")
    assert "no alert was sent" in text(quiet)
    other = run("views/inbox.py", actor="priya.s")
    no_errors(other)
    assert "Nothing for you" in text(other)


def test_settings_page():
    for tab in ("Results", "Models", "Access", "Cost", "Reset"):
        at = run("views/results.py", settings_tab=tab)
        no_errors(at)
        assert not [w for w in HIDDEN_WORDS if w in text(at).lower()]
