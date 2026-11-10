from detective.sources import FixtureSource
from detective.tools import Toolbox

GPUS = [f"i-0a{i}" for i in range(1, 9)]


def box(scenario="tuesday", **kw):
    return Toolbox(FixtureSource(scenario), **kw)


def test_bill_lines_and_pool():
    tb = box()
    r = tb.get_bill_lines(GPUS)
    assert r["ok"] and len(r["items"]) == 8 and all(i["date"] == "2026-10-20" for i in r["items"])
    assert set(r["evidence_ids"]) <= tb.returned_ids
    assert tb.calls[0].name == "get_bill_lines" and tb.calls[0].evidence_ids == r["evidence_ids"]


def test_change_events_filter_and_window():
    tb = box()
    r = tb.lookup_change_events("2026-10-18T00:00:00Z", "2026-10-21T00:00:00Z", "i-0a1")
    assert [i["evidence_id"] for i in r["items"]] == ["ct-004"]
    assert r["items"][0]["via_console"] is True and "tagSpecificationSet" not in r["items"][0]["request"]
    all_events = tb.lookup_change_events("2026-10-18T00:00:00Z", "2026-10-21T00:00:00Z")
    assert {"ct-006", "ct-007"} <= set(all_events["evidence_ids"])
    too_wide = tb.lookup_change_events("2026-10-01T00:00:00Z", "2026-10-21T00:00:00Z")
    assert not too_wide["ok"] and "too wide" in too_wide["error"]


def test_tags_report_untagged_and_missing():
    r = box().get_tags(GPUS[:2] + ["i-nope"])
    assert len(r["items"]) == 2 and all(i["tags"] == {} for i in r["items"])
    assert "no tags" in r["note"] and "i-nope" in r["note"]


def test_ticket_search_wraps_untrusted_text():
    tb = box()
    by_key = tb.find_ticket("CHG-1042")
    assert by_key["evidence_ids"] == ["CHG-1042"]
    assert by_key["items"][0]["description"].startswith("<untrusted_data>")
    by_words = tb.find_ticket("c6i.16xlarge checkout")
    assert by_words["evidence_ids"] == ["CHG-1042"]
    none = tb.find_ticket("g6.12xlarge")
    assert none["ok"] and none["items"] == [] and "gap" in none["note"]


def test_utilization_summaries():
    tb = box()
    r = tb.get_utilization(GPUS)
    assert len(r["items"]) == 16 and all(i["peak"] < 5 for i in r["items"])
    nat = tb.get_utilization(["nat-0c1"])
    [row] = nat["items"]
    assert row["step_up_at"] == "2026-10-20T10:00:00Z"
    assert row["gb_per_hour_after_step"] > 2 * row["gb_per_hour_before_step"]


def test_deployment_traffic_budget_exception():
    tb = box()
    dep = tb.get_deployment("ct-006")
    assert dep["evidence_ids"] == ["dep-001"] and dep["items"][0]["note"].startswith("<untrusted_data>")
    net = tb.get_traffic_destinations("nat-0c1")
    assert net["evidence_ids"] == ["net-001"]
    assert tb.get_budget("checkout-loadtest")["evidence_ids"] == ["bud-001"]
    assert tb.check_exception(GPUS)["items"] == []


def test_cap_and_errors_never_raise():
    tb = box(cap=2)
    tb.get_tags(GPUS)
    tb.get_tags(GPUS)
    r = tb.get_tags(GPUS)
    assert not r["ok"] and "limit" in r["error"] and len(tb.calls) == 3 and tb.calls[-1].ok is False
    bad = box().lookup_change_events("not-a-time", "2026-10-21T00:00:00Z")
    assert not bad["ok"] and "failed" in bad["error"]


def test_on_call_streams_trace():
    seen = []
    box(on_call=seen.append).get_tags(["nat-0c1"])
    assert seen and seen[0].name == "get_tags" and seen[0].evidence_ids
