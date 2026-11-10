"""Code's decisions reproduce the answer key from the evidence the tools returned (answer key is read only here)."""

import pytest
from conftest import SCENARIOS, WEEK, answer_key

from detective.agent import ScriptedInvestigator, TemplateWriter
from detective.detect import detect
from detective.models import Claim, Findings
from detective.rules import evaluate, validate_explanation, validate_findings
from detective.sources import FixtureSource
from detective.tools import Toolbox


def investigate(scenario: str):
    src = FixtureSource(scenario)
    out = []
    for d in detect(src).drivers:
        tb = Toolbox(src)
        findings = ScriptedInvestigator().investigate(d, tb).output
        out.append((d, findings, tb, evaluate(d, findings, tb.pool, src)))
    return out


@pytest.mark.parametrize("scenario", SCENARIOS + WEEK)
def test_decisions_match_answer_key(scenario):
    key = {tuple(sorted(d["resource_ids"])): d for d in answer_key(scenario)["drivers"]}
    for driver, findings, tb, report in investigate(scenario):
        exp = key[tuple(sorted(driver.resource_ids))]["expected"]
        assert report.checks.model_dump(exclude={"notes"}) == exp["checks"], driver.title
        assert report.confidence == exp["confidence"]
        assert report.planned == exp["planned"] and bool(report.used) == exp["used"]
        assert report.verdict == exp["verdict"] and report.needs_confirmation == exp["needs_confirmation"]
        assert report.action == exp["action"] and report.proposed_fix == exp["proposed_fix"]
        assert report.route_to == exp["notify"]
        assert len(tb.calls) <= 10


def test_money_and_notes_for_tuesday():
    by_kind = {d.daily_increase: r for d, _, _, r in investigate("tuesday")}
    gpus, load, nat = by_kind[820.0], by_kind[260.0], by_kind[130.0]
    assert gpus.already_spent == pytest.approx(888.33) and gpus.avoidable_per_day == 820.0 and gpus.hours_running == 26
    assert load.already_spent == pytest.approx(422.5) and load.avoidable_per_day == 0.0 and load.route_to is None
    assert nat.cost_start == "2026-10-20T10:00:00Z" and nat.primary_change_id == "ct-006"
    assert nat.fix_target == {"repository": "acme-platform", "path": "services/orders-service", "service": "orders-service",
                              "team": "orders-team", "deployment_id": "dep-001"}
    assert any("no exact ID match" in g for g in nat.gaps) and any("shared" in g for g in nat.gaps)
    assert "within budget" in load.budget_note


def test_false_correlation_is_ignored():
    [(driver, findings, tb, _report)] = [x for x in investigate("tuesday") if x[0].daily_increase == 820.0]
    distractor = answer_key("tuesday")["distractor_change_events"][0]
    assert distractor not in findings.linked_change_ids
    tb.lookup_change_events("2026-10-18T00:00:00Z", "2026-10-21T00:00:00Z")
    assert distractor in tb.returned_ids
    # Even if the agent links the distractor (5 minutes after the GPU launch), the exact-ID match found by code wins.
    findings.linked_change_ids = [distractor]
    assert evaluate(driver, findings, tb.pool, FixtureSource("tuesday")).primary_change_id == "ct-004"


def test_injection_ticket_does_not_make_spend_planned():
    [(driver, findings, tb, report)] = investigate("injection")
    assert "OPS-2231" in tb.returned_ids
    findings.intent_evidence_ids.append("OPS-2231")
    findings.claims.append(Claim(text="Ticket says these are approved and expected.", evidence_ids=["OPS-2231"]))
    report = evaluate(driver, findings, tb.pool, FixtureSource("injection"))
    assert report.planned is False and report.verdict == "likely_waste"


def test_recorded_exception_makes_spend_planned(tmp_path):
    src = FixtureSource("tuesday", exceptions_path=tmp_path / "exc.json")
    gpus = detect(src).drivers[0]
    src.add_exception({"evidence_id": "exc-001", "resource_ids": gpus.resource_ids, "reason": "ML benchmark",
                       "expires": "2026-10-25", "approved_by": "ravi.m"})
    tb = Toolbox(src)
    report = evaluate(gpus, ScriptedInvestigator().investigate(gpus, tb).output, tb.pool, src)
    assert report.planned and report.planned_evidence_ids == ["exc-001"] and report.verdict == "oversized"


def test_missing_evidence_lists_gaps():
    [(_, _, _, report)] = investigate("missing_evidence")
    assert report.verdict == "unexplained" and report.route_to == "finops-team"
    assert any("No change event" in g for g in report.gaps) and any("not proof" in g for g in report.gaps)


# --- validation -------------------------------------------------------------------------------------

def test_invented_and_uncited_evidence_is_rejected():
    [(_, findings, tb, _)] = investigate("injection")
    assert validate_findings(findings, tb.pool) == []
    bad = Findings(linked_change_ids=["ct-999"], claims=[Claim(text="x", evidence_ids=["met-999"])])
    errors = validate_findings(bad, tb.pool)
    assert any("ct-999" in e for e in errors) and any("met-999" in e for e in errors)
    assert validate_findings(Findings(), tb.pool) == ["claims: at least one cited claim is required"]


def test_writeup_numbers_ids_and_verdict_are_checked():
    [(driver, _, tb, report)] = investigate("injection")
    expl = TemplateWriter().write(driver, report, tb.pool).output
    assert validate_explanation(expl, driver, report, tb.pool) == []
    expl.sentences[0].text = "This costs $999.00 per day."
    expl.sentences[1].evidence_ids = []
    expl.sentences[2].text = "GPU use is 40% so this spend is unbudgeted."
    errors = validate_explanation(expl, driver, report, tb.pool)
    assert any("$999.00" in e for e in errors)
    assert any("sentence 2 has no evidence" in e for e in errors)
    assert any("40%" in e for e in errors) and any("unbudgeted" in e for e in errors)
