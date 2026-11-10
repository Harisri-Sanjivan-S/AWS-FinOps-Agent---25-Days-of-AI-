import pytest
from conftest import SCENARIOS, answer_key

from detective.detect import detect
from detective.sources import FixtureSource


def test_tuesday_gate_and_drivers():
    r = detect(FixtureSource("tuesday"))
    assert r.baseline_mean == 3260.0 and r.baseline_sd == pytest.approx(42.43)
    assert r.spike_total == 4500.0 and r.increase == 1240.0 and r.increase_pct == pytest.approx(38.04)
    assert r.material and r.anomaly_id == "anom-001"
    assert [d.daily_increase for d in r.drivers] == [820.0, 260.0, 130.0]
    assert r.explained_pct == pytest.approx(97.6)
    assert [d.kind for d in r.drivers] == ["compute", "compute", "network"]
    assert all(d.cause == "usage" for d in r.drivers)
    assert {n.resource_id for n in r.noise} >= {"winter-access-logs", "snap-0e1"}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_drivers_match_answer_key(scenario):
    key = answer_key(scenario)
    r = detect(FixtureSource(scenario))
    assert r.increase == pytest.approx(key["increase"]) and r.material == key["materiality_passed"]
    assert [sorted(d.resource_ids) for d in r.drivers] == [sorted(d["resource_ids"]) for d in key["drivers"]]
    for d, k in zip(r.drivers, key["drivers"]):
        assert d.daily_increase == pytest.approx(k["daily_increase"], abs=0.01)
        assert set(d.bill_evidence_ids) == set(k["evidence"]["bill_lines"])


def test_rate_change_is_classified_as_rate(tmp_path):
    src = FixtureSource("tuesday")
    lines = [dict(line) for line in src.bill_lines]
    for line in lines:
        if line["line_item_resource_id"] == "nat-0c1" and line["line_item_usage_type"].endswith("Bytes") \
                and line["line_item_usage_start_date"].startswith("2026-10-20"):
            base = next(x for x in src.bill_lines if x["line_item_resource_id"] == "nat-0c1"
                        and x["line_item_usage_type"].endswith("Bytes") and x["line_item_usage_start_date"].startswith("2026-10-13"))
            line["line_item_usage_amount"] = base["line_item_usage_amount"]
            line["line_item_unblended_rate"] = round(line["line_item_unblended_cost"] / base["line_item_usage_amount"], 6)
    src.__dict__["bill_lines"] = lines
    nat = next(d for d in detect(src).drivers if d.kind == "network")
    assert nat.cause == "rate"
