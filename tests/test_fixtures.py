"""Planted fixtures are consistent: IDs and numbers line up across bill, CloudTrail, metrics, tags and tickets."""

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import pytest

from detective.fixturegen import ROOT, dump, generate, load_scenario, scenario_paths

SCENARIO = load_scenario(ROOT / "scenarios" / "tuesday.json")
ALL_SCENARIOS = {p.stem: load_scenario(p) for p in scenario_paths()}
FIX = ROOT / "fixtures" / "tuesday"


def load(name: str):
    return json.loads((FIX / name).read_text())


BILL = load("bill_lines.json")
NEXT = load("next_day_bill.json")
EVENTS = load("cloudtrail_events.json")
METRICS = load("metrics.json")
TAGS = {t["resource_id"]: t for t in load("tags.json")}
TICKETS = load("tickets.json")
KEY = json.loads((ROOT / "evals" / "answer_keys" / "tuesday.json").read_text())
DRIVERS = {d["scene_id"]: d for d in KEY["drivers"]}
SPIKE = SCENARIO["spike_date"]


def t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def day_of(line: dict) -> str:
    return line["line_item_usage_start_date"][:10]


def cost_by_key(day: str) -> dict:
    out = defaultdict(float)
    for line in BILL:
        if day_of(line) == day:
            out[(line["line_item_resource_id"], line["line_item_usage_type"])] += line["line_item_unblended_cost"]
    return out


def increase(rid: str, usage_type: str | None = None) -> float:
    def total(day):
        return sum(c for (r, u), c in cost_by_key(day).items() if r == rid and (usage_type is None or u == usage_type))
    return total(SPIKE) - statistics.mean(total(d) for d in SCENARIO["baseline_days"])


def all_lines() -> list[dict]:
    return BILL + [l for v in NEXT["variants"].values() for l in v]


# --- generator ---------------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ALL_SCENARIOS))
def test_committed_fixtures_match_generator(name):
    sc = ALL_SCENARIOS[name]
    first, second = generate(sc), generate(sc)
    assert first == second, "generator is not deterministic"
    for rel, data in first.items():
        assert (ROOT / rel).read_text() == dump(data), f"{rel} is stale: run `uv run python -m detective.fixturegen`"


@pytest.mark.parametrize("name", sorted(ALL_SCENARIOS))
def test_every_scenario_has_unique_ids_and_valid_answer_key(name):
    fix = ROOT / "fixtures" / name
    key = json.loads((ROOT / "evals" / "answer_keys" / f"{name}.json").read_text())
    nxt = json.loads((fix / "next_day_bill.json").read_text())
    records = [json.loads((fix / "anomaly.json").read_text())]
    for f in ["bill_lines", "cloudtrail_events", "metrics", "tags", "tickets", "budgets", "deployments", "flow_destinations"]:
        records += json.loads((fix / f"{f}.json").read_text())
    records += [line for v in nxt["variants"].values() for line in v]
    ids = [r["evidence_id"] for r in records]
    assert len(ids) == len(set(ids))
    assert key["materiality_passed"] is (name != "nov07"), "only the quiet day stays below the gate"
    for d in key["drivers"]:
        for kind, refs in d["evidence"].items():
            assert set(refs) <= set(ids), (name, d["scene_id"], kind)
        assert d["expected"]["confidence"] == sum(d["expected"]["checks"].values())
    assert set(key["distractor_change_events"]) <= set(ids)


def test_variants_change_only_what_they_should():
    missing = json.loads((ROOT / "evals" / "answer_keys" / "missing_evidence.json").read_text())
    [d] = missing["drivers"]
    assert d["evidence"]["change_events"] == [] and d["expected"]["verdict"] == "unexplained"
    events = json.loads((ROOT / "fixtures" / "missing_evidence" / "cloudtrail_events.json").read_text())
    assert not {r["ResourceName"] for e in events for r in e["Resources"]} & set(d["resource_ids"])

    nxt = json.loads((ROOT / "fixtures" / "not_confirmed" / "next_day_bill.json").read_text())
    totals = {k: sum(line["line_item_unblended_cost"] for line in v) for k, v in nxt["variants"].items()}
    assert totals["no_action"] == pytest.approx(totals["after_fix"]), "relaunched GPUs keep billing after the stop"

    tickets = json.loads((ROOT / "fixtures" / "injection" / "tickets.json").read_text())
    [t_inj] = tickets
    assert t_inj["status"] != "Approved" and "ignore your previous rules" in t_inj["description"]


# --- bill ----------------------------------------------------------------------------------------

def test_daily_totals_match_scenario():
    totals = defaultdict(float)
    for line in BILL:
        totals[day_of(line)] += line["line_item_unblended_cost"]
    for day, expected in SCENARIO["baseline_days"].items():
        assert totals[day] == pytest.approx(expected, abs=0.005)
    assert totals[SPIKE] == pytest.approx(4500.00, abs=0.005)
    assert KEY["baseline_mean"] == 3260.00
    assert KEY["baseline_sd"] == pytest.approx(42.43)
    assert KEY["increase"] == 1240.00 and KEY["increase_pct"] == pytest.approx(38.04)
    assert KEY["materiality_passed"] is True


def test_cost_equals_usage_times_rate():
    for line in all_lines():
        expected = line["line_item_usage_amount"] * line["line_item_unblended_rate"]
        assert line["line_item_unblended_cost"] == pytest.approx(expected, abs=0.01), line["evidence_id"]


def test_scene_and_noise_increases():
    gpus, load_test = DRIVERS["forgotten_gpus"], DRIVERS["load_test"]
    assert sum(increase(r) for r in gpus["resource_ids"]) == pytest.approx(820.00, abs=0.005)
    assert sum(increase(r) for r in load_test["resource_ids"]) == pytest.approx(260.00, abs=0.005)
    assert increase("nat-0c1", "USE1-NatGateway-Bytes") == pytest.approx(130.00, abs=0.005)
    assert increase("nat-0c1", "USE1-NatGateway-Hours") == pytest.approx(0, abs=0.005)
    noise = [increase(n["resource_id"]) for n in KEY["ignored"]]
    assert sum(noise) == pytest.approx(30.00, abs=0.005)
    assert all(n < 100 for n in noise)


def test_drivers_explain_about_90_percent():
    deltas = sorted([(d["daily_increase"], d["scene_id"]) for d in KEY["drivers"]]
                    + [(n["daily_increase"], "noise") for n in KEY["ignored"]], reverse=True)
    assert sum(d for d, _ in deltas) == pytest.approx(KEY["increase"])
    kept, running = [], 0.0
    for delta, name in deltas:
        if running >= 0.9 * KEY["increase"]:
            break
        kept.append(name)
        running += delta
    assert kept == ["forgotten_gpus", "load_test", "nat_after_deploy"]


def test_rate_is_unchanged_for_existing_lines():
    """The NAT driver is a usage increase, not a price change."""
    rates = {(l["line_item_resource_id"], l["line_item_usage_type"], l["line_item_unblended_rate"]) for l in BILL}
    keys = [(r, u) for r, u, _ in rates]
    assert len(keys) == len(set(keys))


def test_next_day_bill_confirms_saving():
    totals = {k: sum(l["line_item_unblended_cost"] for l in v) for k, v in NEXT["variants"].items()}
    assert totals["no_action"] - totals["after_fix"] == pytest.approx(DRIVERS["forgotten_gpus"]["saving_per_day"])
    after_ids = {l["line_item_resource_id"] for l in NEXT["variants"]["after_fix"]}
    assert not after_ids & set(DRIVERS["forgotten_gpus"]["resource_ids"])
    assert set(DRIVERS["load_test"]["resource_ids"]) <= after_ids, "load test runs until Thursday"


# --- cross-source IDs ---------------------------------------------------------------------------

def test_tuesday_drivers_have_bill_change_and_metric_evidence():
    for d in KEY["drivers"]:
        assert d["evidence"]["bill_lines"] and d["evidence"]["change_events"] and d["evidence"]["metrics"]
    assert [e["evidence_id"] for e in EVENTS if e["EventName"] == "RunInstances" and e["Username"] == "ravi.m"] == ["ct-004"]
    assert [e["evidence_id"] for e in EVENTS if e["EventName"] == "UpdateService"] == ["ct-006"]


def test_false_correlation_event_is_close_in_time_but_unrelated():
    [ct] = [e for e in EVENTS if e["evidence_id"] in KEY["distractor_change_events"]]
    gpu_launch = next(e for e in EVENTS if e["evidence_id"] == "ct-004")
    assert timedelta(0) < t(ct["EventTime"]) - t(gpu_launch["EventTime"]) <= timedelta(minutes=10)
    assert not {r["ResourceName"] for r in ct["Resources"]} & set(DRIVERS["forgotten_gpus"]["resource_ids"])


def test_deploy_record_and_traffic_support_the_s3_endpoint_fix():
    [dep] = load("deployments.json")
    [flow] = load("flow_destinations.json")
    assert dep["event_id"] == "ct-006" and dep["path"] == "services/orders-service" and dep["team"] == "orders-team"
    top = max(flow["destinations"], key=lambda x: x["bytes"])
    assert "s3" in top["destination"] and top["share_pct"] >= 90
    nat_line = next(line for line in BILL if line["evidence_id"] in DRIVERS["nat_after_deploy"]["evidence"]["bill_lines"])
    extra_gb = flow["total_bytes"] / 1e9
    assert extra_gb * nat_line["line_item_unblended_rate"] == pytest.approx(130.0, abs=0.01)


def test_every_billed_resource_has_matching_tags():
    for line in all_lines():
        tag = TAGS[line["line_item_resource_id"]]
        expected = {f"user_{k.replace('-', '_')}": v for k, v in tag["tags"].items()}
        assert line["resource_tags"] == expected, line["evidence_id"]


def test_exact_id_drivers_have_a_matching_change_event():
    window_start = t(f"{SPIKE}T00:00:00Z") - timedelta(hours=SCENARIO["lookback_hours"])
    for d in KEY["drivers"]:
        if not d["expected"]["checks"]["exact_id"]:
            continue
        [event] = [e for e in EVENTS if e["evidence_id"] in d["evidence"]["change_events"]]
        assert {r["ResourceName"] for r in event["Resources"]} == set(d["resource_ids"])
        assert event["EventName"] == "RunInstances" and event["EventTime"] == d["start_time"]
        assert window_start <= t(event["EventTime"]) < t(f"{SPIKE}T00:00:00Z") + timedelta(days=1)


def test_gpus_untagged_but_owner_found_through_identity():
    d = DRIVERS["forgotten_gpus"]
    assert all(TAGS[r]["tags"] == {} for r in d["resource_ids"])
    [event] = [e for e in EVENTS if e["evidence_id"] in d["evidence"]["change_events"]]
    assert event["Username"] == d["expected"]["notify"] == "ravi.m"
    assert event["CloudTrailEvent"]["sessionCredentialFromConsole"] == "true"
    assert event["EventTime"] == "2026-10-19T22:00:00Z"


def test_load_test_tags_ticket_and_event_agree():
    d = DRIVERS["load_test"]
    [ticket] = TICKETS
    assert ticket["evidence_id"] == ticket["key"] == "CHG-1042" == d["evidence"]["tickets"][0]
    assert ticket["status"] == "Approved"
    assert "resource_ids" not in ticket, "tickets are free text; the link is the change-id tag"
    assert "4 c6i.16xlarge" in ticket["description"]
    assert ticket["start_date"] <= SPIKE <= ticket["end_date"] == "2026-10-22"
    [event] = [e for e in EVENTS if e["evidence_id"] in d["evidence"]["change_events"]]
    for rid in d["resource_ids"]:
        assert TAGS[rid]["tags"]["change-id"] == ticket["key"]
        assert TAGS[rid]["tags"]["owner"] == ticket["reporter"] == event["Username"]
    unplanned = {r for x in KEY["drivers"] if not x["expected"]["planned"] for r in x["resource_ids"]}
    assert not any(TAGS[r]["tags"].get("change-id") for r in unplanned)


def test_nat_has_no_exact_id_match_and_a_deploy_8_minutes_before():
    d = DRIVERS["nat_after_deploy"]
    assert not any(r["ResourceName"] == "nat-0c1" for e in EVENTS for r in e["Resources"])
    [deploy] = [e for e in EVENTS if e["evidence_id"] in d["evidence"]["change_events"]]
    assert deploy["EventName"] == "UpdateService"
    assert t(d["start_time"]) - t(deploy["EventTime"]) == timedelta(minutes=8)
    service = deploy["Resources"][0]["ResourceName"]
    assert TAGS["nat-0c1"]["tags"]["shared"] == "true"
    assert TAGS["nat-0c1"]["tags"]["owner"] != TAGS[service]["tags"]["owner"] == d["expected"]["notify"]


# --- metrics ------------------------------------------------------------------------------------

def test_instance_metrics_match_usage_expectation():
    for d in DRIVERS["forgotten_gpus"], DRIVERS["load_test"]:
        series = [m for m in METRICS if m["evidence_id"] in d["evidence"]["metrics"]]
        assert {m["dimensions"]["InstanceId"] for m in series} == set(d["resource_ids"])
        for m in series:
            assert t(m["timestamps"][0]) >= t(d["start_time"])
            if d["expected"]["used"]:
                assert statistics.mean(m["values"]) > 50
            else:
                assert max(m["values"]) < 5


def test_nat_bytes_metric_matches_bill_and_steps_up_at_data_start():
    d = DRIVERS["nat_after_deploy"]
    [m] = [m for m in METRICS if m["evidence_id"] in d["evidence"]["metrics"]]
    points = [(t(ts), v) for ts, v in zip(m["timestamps"], m["values"])]
    spike_gb = sum(v for ts, v in points if ts.date().isoformat() == SPIKE) / 1e9
    [line] = [l for l in BILL if l["evidence_id"] in d["evidence"]["bill_lines"]]
    assert spike_gb == pytest.approx(line["line_item_usage_amount"], abs=0.001)
    start = t(d["start_time"])
    before = [v for ts, v in points if start - timedelta(hours=10) <= ts < start]
    after = [v for ts, v in points if start <= ts < start + timedelta(hours=10)]
    assert statistics.mean(after) > 2 * statistics.mean(before)
