"""Headless tests of the Streamlit app and role-based access. Tests set FINOPS_UI_MODE=offline (scripted investigator);
the app itself always uses Bedrock. No AWS, no GitHub."""

import pytest
from streamlit.testing.v1 import AppTest

from detective import config
from detective.cases import list_runs
from detective.config import ROOT
from detective.pipeline import investigate_all

APP = ROOT / "app"
HIDDEN_WORDS = ["planted", "offline", "replay", "anomaly detection", "shaped like", "mirror", "dry run"]


def run(name: str, **state) -> AppTest:
    at = AppTest.from_file(str(APP / name), default_timeout=60)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def no_errors(at: AppTest) -> None:
    assert not at.exception, [e.message for e in at.exception]


def visible_text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.title]
    parts += [i.value for i in at.info] + [w.value for w in at.warning] + [m.label for m in at.metric]
    return " ".join(str(p) for p in parts).lower()


@pytest.fixture
def week():
    """Two demo days with cases for Ravi on both (10 Nov and 13 Nov), plus the Orders-team NAT case on 10 Nov."""
    for day in ("nov10", "nov13"):
        investigate_all(day, "offline")


def test_router_shows_pages_by_role(week):
    finops = run("Home.py", actor="finops-team")
    no_errors(finops)
    assert finops.title[0].value == "Cost spike investigation console"
    assert finops.sidebar.selectbox[0].label == "Acting as"
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


def test_console_has_no_internal_labels():
    at = run("views/console.py")
    no_errors(at)
    assert at.selectbox[0].options[0].startswith("Sat 07 Nov") and len(at.selectbox[0].options) == 8
    assert at.metric[1].value == "$4,496.44" and "Passed" in at.metric[3].value
    assert at.button[0].label == "Investigate" and not at.button[0].disabled
    text = visible_text(at)
    assert not [w for w in HIDDEN_WORDS if w in text], text


def test_console_uses_bedrock_and_needs_cost_approval(monkeypatch):
    monkeypatch.setenv("FINOPS_UI_MODE", "bedrock")
    config.get_settings.cache_clear()
    at = run("views/console.py")
    no_errors(at)
    assert any("Amazon Bedrock" in w.value for w in at.warning)
    assert at.button[0].disabled, "no Bedrock spend until the approval box is ticked"


def test_quiet_day_explains_why_nothing_runs(monkeypatch):
    monkeypatch.setenv("FINOPS_UI_MODE", "bedrock")
    config.get_settings.cache_clear()
    at = run("views/console.py", scenario="nov07")
    no_errors(at)
    assert any("Below the alert threshold" in s.value for s in at.success)
    assert not at.warning and not at.button, "no cost warning and no Investigate button on a quiet day"


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
    assert len(replay.status) == 3
    other = run("views/investigation.py", scenario="nov08")
    assert any("not been investigated yet" in i.value for i in other.info)


def test_case_page_lists_own_cases_across_days(week):
    at = run("views/case.py", actor="ravi.m")
    no_errors(at)
    assert [o.split(" · ")[0] for o in at.selectbox[0].options] == ["N10-01", "N13-01"]
    assert any(m.label == "Already spent (code)" and m.value == "$888.33" for m in at.metric)
    finops = run("views/case.py", actor="finops-team")
    assert len(finops.selectbox[0].options) == 4


def test_inbox_owner_sees_every_day_and_approves(week):
    at = run("views/inbox.py", actor="ravi.m")
    no_errors(at)
    assert len(at.expander) == 2 and [h.value.split(" · ")[0] for h in at.subheader] == ["Tue 10 Nov", "Fri 13 Nov"]
    approve_btn = next(b for b in at.button if b.label == "Approve stop")
    at = approve_btn.click().run()
    no_errors(at)
    nd = next(b for b in at.button if b.label == "Next day: check the bill")
    at = nd.click().run()
    no_errors(at)
    assert any("Saving confirmed" in s.value for s in at.success)
    orders = run("views/inbox.py", actor="orders-team")
    assert len(orders.expander) == 1 and "N10-03" in orders.expander[0].label


def test_inbox_finops_sees_all_but_cannot_approve_others(week):
    at = run("views/inbox.py", actor="finops-team")
    no_errors(at)
    assert len(at.expander) == 4
    assert not [b for b in at.button if b.label in ("Approve stop", "Approve fix and open PR")]
    assert any(t.label == "Re-route" for t in at.tabs)
    assert any("no alert was sent" in m.value for m in at.markdown)
    other = run("views/inbox.py", actor="priya.s")
    no_errors(other)
    assert any("Nothing for you" in s.value for s in other.success)


def test_results_page():
    at = run("views/results.py")
    no_errors(at)
    assert not [w for w in HIDDEN_WORDS if w in visible_text(at)]
