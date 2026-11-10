"""Headless smoke tests of the Streamlit pages (offline mode only; no AWS, no GitHub)."""

import pytest
from streamlit.testing.v1 import AppTest

from detective.cases import list_runs
from detective.config import ROOT
from detective.pipeline import investigate_all

APP = ROOT / "app"


def page(name: str, **state) -> AppTest:
    at = AppTest.from_file(str(APP / name), default_timeout=60)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def no_errors(at: AppTest) -> None:
    assert not at.exception, [e.message for e in at.exception]


def test_console_renders_measurement():
    at = page("Home.py")
    no_errors(at)
    assert any("Cost Anomaly Detection" in m.value for m in at.markdown)
    assert at.metric[1].value == "$4,500.00" and "Passed" in at.metric[3].value
    assert at.button[0].label == "Investigate" and not at.button[0].disabled


def test_live_investigation_offline_creates_cases():
    at = page("pages/1_Investigation.py", pending_run={"scenario": "tuesday", "mode": "offline"})
    no_errors(at)
    [run] = list_runs()
    assert [c.status for c in run.cases()] == ["awaiting_owner", "quiet", "awaiting_owner"]
    assert any("Investigation finished" in s.value for s in at.success)


@pytest.fixture
def run_dir():
    store, _, _ = investigate_all("tuesday", "offline")
    return str(store.dir)


def test_case_page(run_dir):
    at = page("pages/2_Case.py", run_dir=run_dir, case_id="TUE-01")
    no_errors(at)
    assert any("Likely waste" in m.value for m in at.markdown)
    assert any(m.label == "Already spent (code)" and m.value == "$888.33" for m in at.metric)


def test_inbox_approve_stop_and_next_day(run_dir):
    at = page("pages/3_Inbox.py", run_dir=run_dir, persona="ravi.m")
    no_errors(at)
    [approve_btn] = [b for b in at.button if b.label == "Approve stop"]
    at = approve_btn.click().run()
    no_errors(at)
    [nd] = [b for b in at.button if b.label == "Next day: check the bill"]
    at = nd.click().run()
    no_errors(at)
    assert any("Saving confirmed" in s.value for s in at.success)


def test_inbox_quiet_case_for_finops_and_results_page(run_dir):
    at = page("pages/3_Inbox.py", run_dir=run_dir, persona="finops-team")
    no_errors(at)
    assert any("no alert was sent" in m.value for m in at.markdown)
    no_errors(page("pages/4_Results_and_Settings.py", run_dir=run_dir))
