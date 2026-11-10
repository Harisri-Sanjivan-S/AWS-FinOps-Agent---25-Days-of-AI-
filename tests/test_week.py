"""Demo week: batch runner (scripted investigator in tests), scoring, proof of changes and the performance page.
No AWS calls."""

from streamlit.testing.v1 import AppTest

from detective import rbac
from detective.config import ROOT
from evals.score import all_runs, latest_runs, proof, score_case, summarise
from scripts import run_week


def test_week_runner_needs_confirmation_for_bedrock(capsys):
    assert run_week.main([]) == []
    out = capsys.readouterr().out
    assert "8 day(s), 10 cost drivers" in out and "--confirm" in out


def test_week_with_owner_actions_scores_and_proves():
    results = run_week.main(["--owner-actions"], mode="offline")
    assert len(results) == 8 and results[0][1] == [], "7 Nov stays below the gate"
    stores = latest_runs(mode="offline")
    rows = [score_case(c) for s in stores for c in s.cases()]
    summary = summarise(rows)
    assert summary["investigations"] == 10 and summary["verdict_accuracy_pct"] == 100
    assert summary["routing_accuracy_pct"] == 100 and summary["guardrail_tests"] >= 2
    assert summary["guardrail_passed"] == summary["guardrail_tests"]
    evidence = proof(stores)
    confirmed = [s for s in evidence["savings"] if s["confirmed"]]
    not_confirmed = [s for s in evidence["savings"] if s["confirmed"] is False]
    assert {s["case"] for s in confirmed} == {"N08-01", "N09-01", "N10-01", "N13-01"}, "N09: idle hosts stopped early"
    assert [s["case"] for s in not_confirmed] == ["N14-01"], "relaunched GPUs: the bill does not drop"
    assert abs(evidence["proven_per_day"] - (700 + 700 + 820 + 820)) < 0.05 and evidence["actions_without_approval"] == 0
    assert all(d["by"] in rbac.ACTORS for d in evidence["decisions"])


def test_reinvestigating_a_day_keeps_earlier_proof():
    run_week.main(["--days", "nov08", "--owner-actions"], mode="offline")
    run_week.main(["--days", "nov08"], mode="offline")  # a fresh run of the same day, no approvals yet
    latest = latest_runs(mode="offline")
    assert not proof(latest)["savings"], "the newest run alone has no stop yet"
    evidence = proof(latest, all_runs(mode="offline"))
    assert [s["case"] for s in evidence["savings"]] == ["N08-01"] and evidence["found_per_day"] == 700


def test_performance_page_access_and_render():
    run_week.main(["--days", "nov10", "nov13", "--owner-actions"], mode="offline")
    page = str(ROOT / "app" / "views" / "performance.py")
    for actor in ("finops-team", "platform-team"):
        at = AppTest.from_file(page, default_timeout=60)
        at.session_state["actor"] = actor
        at.run()
        assert not at.exception, [e.message for e in at.exception]
        assert any(m.label == "Verdict accuracy" and m.value == "100%" for m in at.metric)
        assert any(m.label == "Saving proven on the next bill" and m.value == "$1,640.00/day" for m in at.metric)
    at = AppTest.from_file(page, default_timeout=60)
    at.session_state["actor"] = "ravi.m"
    at.run()
    assert any("does not have access" in e.value for e in at.error)
