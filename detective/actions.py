"""Inbox-only actions. Never given to the agent; each runs after a human decision and is reversible or mocked."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from detective.models import ActionRecord, Case, NextDayResult, now_iso
from detective.sources import FixtureSource

MAX_EXCEPTION_DAYS = 90


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{hashlib.sha1('|'.join(parts).encode()).hexdigest()[:8]}"


def simulate_stop(case: Case) -> ActionRecord:
    """Mirrors ec2:StopInstances. Mocked in the POC: nothing in AWS is touched."""
    return ActionRecord(kind="stop", action_id=_id("act", case.case_id, now_iso()), status="stopped (simulated)",
                        detail={"api": "ec2:StopInstances (mocked)", "instance_ids": case.driver.resource_ids,
                                "reversible": "start the instances again; EBS volumes are kept"})


def record_exception(source: FixtureSource, case: Case, reviewer: str, reason: str, expiry: str) -> ActionRecord:
    """Mirrors a write to the exceptions table (DynamoDB in production). Planned spend needs a reason and an expiry."""
    if not reason.strip():
        raise ValueError("A reason is required to mark spend as planned.")
    spike = date.fromisoformat(source.spike_date)
    exp = date.fromisoformat(expiry)
    if not spike <= exp <= spike + timedelta(days=MAX_EXCEPTION_DAYS):
        raise ValueError(f"Expiry must be between {spike} and {spike + timedelta(days=MAX_EXCEPTION_DAYS)}.")
    existing = [e for e in source.exceptions() if e["evidence_id"].startswith("exc-")]
    record = {"evidence_id": f"exc-{len(existing) + 1:03d}", "resource_ids": case.driver.resource_ids, "reason": reason.strip(),
              "expires": expiry, "approved_by": reviewer, "case_id": case.case_id, "recorded_at": now_iso(),
              "stated_daily_cost": case.driver.daily_increase}
    source.add_exception(record)
    return ActionRecord(kind="exception", action_id=record["evidence_id"], status="recorded",
                        detail={"expires": expiry, "reason": reason.strip(),
                                "recheck": "flag again at expiry or if spend exceeds the stated amount by 20%"})


def next_day_check(source: FixtureSource, case: Case) -> NextDayResult:
    """Compare the driver's cost on the next day's bill with the spike day. Savings count only when the bill proves them."""
    nxt = source.next_day_bill
    stopped = any(a.kind == "stop" for a in case.actions)
    variant = "after_fix" if stopped else "no_action"
    wanted, usage_type = set(case.driver.resource_ids), case.driver.usage_type
    lines = [line for line in nxt["variants"][variant]
             if line["line_item_resource_id"] in wanted and line["line_item_usage_type"] == usage_type]
    spike_cost = sum(source.record(e)["line_item_unblended_cost"] for e in case.driver.bill_evidence_ids)
    next_cost = sum(line["line_item_unblended_cost"] for line in lines)
    expected = case.report.avoidable_per_day if case.report else 0.0
    actual = round(spike_cost - next_cost, 2)
    confirmed = expected > 0 and actual >= 0.9 * expected
    if any(a.kind == "pull_request" for a in case.actions) and not stopped:
        note = "Not confirmed yet: the pull request is awaiting review. The saving can only appear after it is merged and applied."
    elif confirmed:
        note = f"Saving confirmed: the driver cost ${next_cost:,.2f} on {nxt['date']} versus ${spike_cost:,.2f} on the spike day."
    else:
        note = (f"Saving not confirmed: the driver still cost ${next_cost:,.2f} on {nxt['date']}. "
                "Follow up: check whether the resources were restarted or replaced.")
    return NextDayResult(date=nxt["date"], expected_saving=expected, actual_saving=actual, confirmed=confirmed,
                         evidence_ids=[line["evidence_id"] for line in lines] or case.driver.bill_evidence_ids, note=note)
