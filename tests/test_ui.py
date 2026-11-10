"""Headless smoke tests of the Streamlit app and role-based access (dry-run mode only; no AWS, no GitHub)."""

import pytest
from streamlit.testing.v1 import AppTest

from detective.cases import list_runs
from detective.config import ROOT
from detective.pipeline import investigate_all

APP = ROOT / "app"
HIDDEN_WORDS = ["planted", "offline", "replay", "anomaly detection", "shaped like", "mirror"]


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
def run_dir():
    store, _, _ = investigate_all("tuesday", "offline")
    return str(store.dir)


def test_router_shows_pages_by_role(run_dir):
    finops = run("Home.py", actor="finops-team", run_dir=run_dir)
    no_errors(finops)
    assert finops.title[0].value == "Cost spike investigation console"
    assert finops.sidebar.selectbox[0].label == "Acting as"
    owner = run("Home.py", actor="ravi.m", run_dir=run_dir)
    no_errors(owner)
    assert owner.title[0].value == "Inbox", "owners land on their inbox; console is not in their navigation"


def test_console_has_no_internal_labels():
    at = run("views/console.py", mode="offline")
    no_errors(at)
    assert at.selectbox[0].options[0].startswith("Sat 07 Nov") and len(at.selectbox[0].options) == 8
    assert at.metric[1].value == "$4,496.44" and "Passed" in at.metric[3].value
    assert at.button[0].label == "Investigate" and not at.button[0].disabled
    text = visible_text(at)
    assert not [w for w in HIDDEN_WORDS if w in text], text


def test_console_bedrock_by_default_needs_cost_approval():
    at = run("views/console.py")
    no_errors(at)
    assert any("Amazon Bedrock" in w.value for w in at.warning)
    assert at.button[0].disabled, "no Bedrock spend until the approval box is ticked"


def test_owner_cannot_open_finops_pages():
    for view in ("views/console.py", "views/investigation.py", "views/results.py"):
        at = run(view, actor="ravi.m")
        no_errors(at)
        assert any("does not have access" in e.value for e in at.error)


def test_live_investigation_creates_cases():
    at = run("views/investigation.py", pending_run={"scenario": "tuesday", "mode": "offline"})
    no_errors(at)
    [store] = list_runs()
    assert [c.status for c in store.cases()] == ["awaiting_owner", "quiet", "awaiting_owner"]
    assert any("Investigation finished" in s.value for s in at.success)


def test_case_page_shows_only_own_cases(run_dir):
    at = run("views/case.py", run_dir=run_dir, actor="ravi.m")
    no_errors(at)
    assert [o for o in at.radio[0].options] == ["TUE-01 · 8 x g6.12xlarge instances (untagged)"]
    assert any(m.label == "Already spent (code)" and m.value == "$888.33" for m in at.metric)
    finops = run("views/case.py", run_dir=run_dir, actor="finops-team")
    assert len(finops.radio[0].options) == 3


def test_inbox_owner_approves_stop_and_checks_next_day(run_dir):
    at = run("views/inbox.py", run_dir=run_dir, actor="ravi.m")
    no_errors(at)
    assert len(at.expander) == 1
    [approve_btn] = [b for b in at.button if b.label == "Approve stop"]
    at = approve_btn.click().run()
    no_errors(at)
    [nd] = [b for b in at.button if b.label == "Next day: check the bill"]
    at = nd.click().run()
    no_errors(at)
    assert any("Saving confirmed" in s.value for s in at.success)


def test_inbox_finops_sees_all_but_cannot_approve_others(run_dir):
    at = run("views/inbox.py", run_dir=run_dir, actor="finops-team")
    no_errors(at)
    assert len(at.expander) == 3
    assert not [b for b in at.button if b.label in ("Approve stop", "Approve fix and open PR")]
    assert any(t.label == "Re-route" for t in at.tabs)
    assert any("no alert was sent" in m.value for m in at.markdown)
    other = run("views/inbox.py", run_dir=run_dir, actor="priya.s")
    no_errors(other)
    assert any("Nothing for you" in s.value for s in other.success)


def test_results_page(run_dir):
    at = run("views/results.py", run_dir=run_dir)
    no_errors(at)
    assert not [w for w in HIDDEN_WORDS if w in visible_text(at)]
