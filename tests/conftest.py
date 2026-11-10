import json

import pytest

from detective import config
from detective.config import ROOT


@pytest.fixture(autouse=True)
def isolated_runs(tmp_path, monkeypatch):
    """Every test writes runs, cases and the ledger to a temp folder and never reaches AWS."""
    monkeypatch.setenv("FINOPS_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("AWS_PROFILE", "no-such-profile-in-tests")
    monkeypatch.delenv("FINOPS_COST_OVERRIDE", raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def answer_key(scenario: str) -> dict:
    return json.loads((ROOT / "evals" / "answer_keys" / f"{scenario}.json").read_text())


SCENARIOS = ["tuesday", "missing_evidence", "not_confirmed", "injection"]
