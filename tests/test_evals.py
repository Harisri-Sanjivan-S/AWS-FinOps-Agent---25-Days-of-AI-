"""The eval harness itself, offline (scripted agent) and with a fake model for the baseline. No AWS calls."""

import json

from evals import baseline
from evals import run as evals_run


def test_offline_eval_scores_all_seven_scenarios(tmp_path):
    data = evals_run.main(["--mode", "offline", "--runs", "2"], results_dir=tmp_path)
    m = data["summary"]["metrics"]
    assert len(data["cases"]) == 14 and all(r["correct"] for r in data["cases"]), [r for r in data["cases"] if not r["correct"]]
    assert m["Verdict accuracy"] == "100%" and m["Valid citations"] == "100%" and m["Consistent scenarios"] == "7 of 7"
    assert m["False alarms on legitimate spend"] == 0 and m["Actions before approval"] == 0
    assert int(m["Tool calls per driver (avg / max)"].split("/")[1]) <= 10
    assert len(list(tmp_path.glob("*.json"))) == 1 and len(list(tmp_path.glob("*.md"))) == 1


def test_bedrock_eval_needs_confirmation(tmp_path, capsys):
    assert evals_run.main(["--mode", "bedrock", "--runs", "3"], results_dir=tmp_path) is None
    out = capsys.readouterr().out
    assert "Estimate: 18 driver investigations" in out and "--confirm" in out
    assert not list(tmp_path.iterdir())


def test_baseline_needs_confirmation_and_scores_with_a_fake_model(tmp_path, capsys):
    assert baseline.main(["--runs", "1"], results_dir=tmp_path) is None
    assert "Not running" in capsys.readouterr().out

    def fake(model_id, prompt):
        return json.dumps({"verdict": "likely_waste", "evidence_ids": ["ct-004"]}), 1000, 50

    data = baseline.main(["--runs", "1", "--confirm"], results_dir=tmp_path, caller=fake)
    rows = data["baseline"]
    assert len(rows) == 6 and sum(r["correct"] for r in rows) == 3  # GPUs (tuesday, not_confirmed, injection)
