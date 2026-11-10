"""End to end with the offline (scripted) agents, plus routing, escalation and owner actions. No AWS calls."""

import json
from types import SimpleNamespace

import pytest

from detective.agent import AgentRun, ScriptedInvestigator, TemplateWriter, _Guard, strands_tools
from detective.config import get_settings
from detective.ledger import CostCapReached, Ledger
from detective.models import Claim, Explanation, Findings, Sentence
from detective.pipeline import (
    ActionError,
    Agents,
    approve,
    investigate_all,
    investigate_driver,
    make_agents,
    mark_planned,
    next_day,
    reassign,
    reject,
    start_run,
)
from detective.sources import FixtureSource
from detective.tools import TOOL_NAMES, Toolbox


def tuesday():
    store, det, cases = investigate_all("tuesday", "offline")
    return store, det, {c.driver.daily_increase: c for c in cases}


def test_offline_run_routes_cases():
    store, _, cases = tuesday()
    gpus, load, nat = cases[820.0], cases[260.0], cases[130.0]
    assert [c.case_id for c in store.cases()] == ["TUE-01", "TUE-02", "TUE-03"]
    assert gpus.status == "awaiting_owner" and gpus.assigned_to == "ravi.m"
    assert load.status == "quiet" and load.assigned_to is None
    assert nat.status == "awaiting_owner" and nat.assigned_to == "orders-team"
    for c in cases.values():
        assert c.explanation and c.validation_errors == [] and c.tool_calls and c.usage.models == ["scripted"]
        assert [a.event for a in c.audit][:2] == ["driver detected", "findings returned"]
    assert store.detect().increase == 1240.0 and store.meta()["mode"] == "offline"


def test_events_stream_for_the_live_trace():
    seen = []
    investigate_all("injection", "offline", on_event=lambda kind, payload: seen.append(kind))
    assert seen[0] == "detect" and "tool" in seen and seen[-1] == "case"


def test_approve_stop_then_next_day_confirms_saving():
    store, _, cases = tuesday()
    gpus = approve(store, cases[820.0], reviewer="ravi.m")
    assert gpus.status == "fixed" and gpus.actions[0].kind == "stop" and "mocked" in gpus.actions[0].detail["api"]
    gpus = next_day(store, gpus)
    assert gpus.next_day.confirmed and gpus.next_day.actual_saving == pytest.approx(820.0)
    assert store.load_case("TUE-01").next_day.confirmed


def test_saving_not_confirmed_when_instances_come_back():
    store, _, [case] = investigate_all("not_confirmed", "offline")
    case = next_day(store, approve(store, case, reviewer="ravi.m"))
    assert not case.next_day.confirmed and "not confirmed" in case.next_day.note.lower()


def test_owner_actions_and_guards():
    store, _, cases = tuesday()
    with pytest.raises(ActionError):
        approve(store, cases[260.0], reviewer="priya.s")  # quiet case: nothing to approve
    with pytest.raises(ActionError):
        approve(store, cases[130.0], reviewer="orders-team")  # code fix needs a fixer
    with pytest.raises(ActionError):
        next_day(store, cases[820.0])  # no fix yet
    nat = reassign(store, cases[130.0], reviewer="orders-team", new_owner="platform-team")
    assert nat.assigned_to == "platform-team" and nat.decisions[-1].action == "not_mine"
    nat = reject(store, nat, reviewer="platform-team", reason="Known, tracked in NET-77")
    assert nat.status == "rejected"
    with pytest.raises(ActionError):
        reject(store, nat, reviewer="x", reason="again")


def test_planned_records_exception_with_expiry():
    store, _, cases = tuesday()
    with pytest.raises(ValueError):
        mark_planned(store, cases[820.0], reviewer="ravi.m", reason="", expiry="2026-10-25")
    with pytest.raises(ValueError):
        mark_planned(store, cases[820.0], reviewer="ravi.m", reason="benchmark", expiry="2027-06-01")
    gpus = mark_planned(store, cases[820.0], reviewer="ravi.m", reason="Model benchmark", expiry="2026-10-25")
    assert gpus.status == "planned" and gpus.actions[0].action_id == "exc-001"
    recorded = json.loads(store.exceptions_path.read_text())
    assert recorded[0]["resource_ids"] == gpus.driver.resource_ids and recorded[0]["expires"] == "2026-10-25"


class FlakyInvestigator:
    """Returns invented evidence the first `bad` times, then delegates to the scripted playbook."""

    def __init__(self, model_id, bad):
        self.model_id, self.bad = model_id, bad

    def investigate(self, driver, toolbox, feedback=None):
        if self.bad:
            self.bad -= 1
            return AgentRun(Findings(claims=[Claim(text="made up", evidence_ids=["ct-999"])]), self.model_id, "t", 100, 10)
        run = ScriptedInvestigator().investigate(driver, toolbox, feedback)
        return AgentRun(run.output, self.model_id, "t", 100, 10)


class LyingWriter:
    model_id = "fake-writer"

    def write(self, driver, report, pool, feedback=None):
        return AgentRun(Explanation(headline="x", sentences=[Sentence(text="It costs $5,000 a day.", evidence_ids=["bill-x"])]),
                        self.model_id, "t")


def test_retry_then_escalate_then_succeed():
    store, source, det = start_run("injection")
    agents = Agents([FlakyInvestigator("fast", 1), FlakyInvestigator("fast", 1), FlakyInvestigator("strong", 0)],
                    [TemplateWriter()], "offline")
    case = investigate_driver(store, source, det.drivers[0], 1, agents)
    assert case.status == "awaiting_owner" and case.usage.models == ["fast", "strong", "scripted"]
    assert [a.detail["attempt"] for a in case.audit if a.event == "findings returned"] == [1, 2, 3]


def test_all_attempts_invalid_needs_human_review():
    store, source, det = start_run("injection")
    agents = Agents([FlakyInvestigator("fast", 5), FlakyInvestigator("strong", 5)], [TemplateWriter()], "offline")
    case = investigate_driver(store, source, det.drivers[0], 1, agents)
    assert case.status == "needs_review" and case.report is None and any("ct-999" in e for e in case.validation_errors)


def test_rejected_writeup_falls_back_to_deterministic_text():
    store, source, det = start_run("injection")
    case = investigate_driver(store, source, det.drivers[0], 1, Agents([ScriptedInvestigator()], [LyingWriter()], "offline"))
    assert case.status == "awaiting_owner" and "$5,000" not in case.explanation.sentences[0].text
    assert any(a.event.startswith("AI write-up rejected") for a in case.audit)


# --- cost guard -------------------------------------------------------------------------------------

def test_ledger_prices_warnings_and_cap(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    haiku = get_settings().model_fast
    assert ledger.cost(haiku, 1_000_000, 100_000) == pytest.approx(1.5)
    ledger.record(haiku, 4_000_000, 1_400_000, purpose="test")
    assert ledger.total() == pytest.approx(11.0) and ledger.status()["level"] == "warning"
    ledger.check()
    with pytest.raises(CostCapReached):
        ledger.check(upcoming_usd=14.0)
    ledger.record(haiku, 15_000_000, 0, purpose="test")
    assert ledger.total() == pytest.approx(26.0) and ledger.status()["level"] == "blocked"
    with pytest.raises(CostCapReached):
        ledger.check()


def test_cost_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FINOPS_COST_OVERRIDE", "1")
    get_settings.cache_clear()
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record(get_settings().model_fast, 30_000_000, 0, purpose="test")
    ledger.check()


def test_bedrock_agents_refuse_before_any_aws_call_when_cap_reached():
    Ledger().record(get_settings().model_fast, 25_000_000, 0, purpose="pre-filled")
    store, source, det = start_run("injection", "bedrock")
    case = investigate_driver(store, source, det.drivers[0], 1, make_agents("bedrock"))
    assert case.status == "needs_review" and "cap" in case.validation_errors[0]
    assert any(a.event == "stopped by cost guard" for a in case.audit)


def test_guard_hook_cancels_tools_and_model_calls():
    tb = Toolbox(FixtureSource("tuesday"), cap=1)
    tb.get_tags(["nat-0c1"])
    guard = _Guard(tb, Ledger(), token_cap=100)
    tool_event = SimpleNamespace(cancel_tool=False)
    guard.before_tool(tool_event)
    assert "limit" in tool_event.cancel_tool
    model_event = SimpleNamespace(cancel=False, projected_input_tokens=150)
    guard.before_model(model_event)
    assert "token cap" in model_event.cancel


def test_strands_tools_wrap_the_toolbox():
    tb = Toolbox(FixtureSource("tuesday"))
    tools = strands_tools(tb)
    assert [t.tool_name for t in tools] == TOOL_NAMES
    out = json.loads(tools[2](resource_ids=["nat-0c1"]))
    assert out["ok"] and out["evidence_ids"] and tb.calls[-1].name == "get_tags"
