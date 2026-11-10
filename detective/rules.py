"""Code decides: evidence check, the four confidence checks, verdict, action, routing and money.

Every rule reads only (a) the driver that code detected and (b) the evidence pool: records that tools actually
returned in this run. Experimental POC heuristics, not a validated financial control."""

from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta

from detective.models import Checks, Driver, Explanation, Findings, Report
from detective.sources import FixtureSource
from detective.tools import step_time, ts

SIZE_TOLERANCE = 0.15
IDLE_PEAK_PCT = 5.0
S3_SHARE_FOR_ENDPOINT = 50.0
QUIET_KEYS = {"dr", "disaster-recovery", "schedule", "batch", "warmup", "standby"}
FALLBACK_ROUTE = "finops-team"


def _events(pool: dict) -> dict[str, dict]:
    return {k: v for k, v in pool.items() if k.startswith("ct-")}


def _resources(event: dict) -> set[str]:
    return {r["ResourceName"] for r in event["Resources"]}


def _is_human(event: dict) -> bool:
    ident = event["CloudTrailEvent"]["userIdentity"]
    if ident["type"] == "IAMUser":
        return True
    if ident["type"] != "AssumedRole":
        return False
    session = ident["arn"].rsplit("/", 1)[-1].lower()
    role = ident["arn"].split("/")[-2].lower() if ident["arn"].count("/") >= 2 else ""
    return not (session.endswith("-ci") or "bot" in session or "github-actions" in role or "service" in role)


def _driver_tags(driver: Driver, pool: dict) -> dict[str, dict]:
    return {v["resource_id"]: v["tags"] for k, v in pool.items() if k.startswith("tag-") and v["resource_id"] in driver.resource_ids}


def _driver_metrics(driver: Driver, pool: dict) -> list[dict]:
    return [v for k, v in pool.items() if k.startswith("met-") and set(v["dimensions"].values()) & set(driver.resource_ids)]


def _hours(a: datetime, b: datetime) -> float:
    return max(0.0, (b - a) / timedelta(hours=1))


def _avg_rate(driver: Driver, source: FixtureSource) -> float:
    rates = [source.record(e)["line_item_unblended_rate"] for e in driver.bill_evidence_ids]
    return statistics.mean(rates)


def _bytes_step(metric: dict, spike_end: datetime) -> dict | None:
    stamps, values = metric["timestamps"], metric["values"]
    step = step_time(stamps, values)
    if not step:
        return None
    before = [v for t, v in zip(stamps, values) if t < step][-24:]
    after = [v for t, v in zip(stamps, values) if step <= t and ts(t) < spike_end]
    return {"step": step, "extra_gb_per_hour": (statistics.mean(after) - statistics.mean(before)) / 1e9,
            "hours_after": len(after)}


def evaluate(driver: Driver, findings: Findings, pool: dict, source: FixtureSource, lookback_hours: int = 48) -> Report:
    spike_start = ts(f"{source.spike_date}T00:00:00Z")
    spike_end = spike_start + timedelta(days=1)
    events = _events(pool)
    wanted = set(driver.resource_ids)
    notes: dict[str, str] = {}

    # Primary change: an exact resource-ID match found by code wins; otherwise the agent's most likely lead.
    exact = sorted((k for k, e in events.items() if wanted <= _resources(e)), key=lambda k: events[k]["EventTime"])
    leads = [k for k in findings.linked_change_ids if k in events]
    primary = exact[0] if exact else (leads[0] if leads else None)
    primary_ev = events.get(primary) if primary else None

    metrics = _driver_metrics(driver, pool)
    bytes_metric = next((m for m in metrics if m["unit"] == "Bytes"), None)
    step = _bytes_step(bytes_metric, spike_end) if bytes_metric else None

    if driver.kind == "network" and step:
        cost_start = ts(step["step"])
    elif metrics:
        cost_start = min(ts(m["timestamps"][0]) for m in metrics)
    elif primary_ev and primary in exact:
        cost_start = ts(primary_ev["EventTime"])
    else:
        cost_start = spike_start
    cost_start = min(cost_start, spike_end)

    # Check 1: size. Independent usage evidence x billed rate within tolerance of the billed increase.
    rate = _avg_rate(driver, source)
    estimate = None
    if driver.kind == "compute" and primary in exact:
        n = len(_resources(primary_ev) & wanted)
        estimate = n * min(24.0, _hours(max(ts(primary_ev["EventTime"]), spike_start), spike_end)) * rate
        notes["size"] = f"{n} instances x {min(24.0, _hours(max(ts(primary_ev['EventTime']), spike_start), spike_end)):.0f} h x ${rate:.3f}/h"
    elif driver.kind == "network" and step:
        estimate = step["extra_gb_per_hour"] * step["hours_after"] * rate
        notes["size"] = f"+{step['extra_gb_per_hour']:.0f} GB/h x {step['hours_after']} h x ${rate:.3f}/GB"
    size = estimate is not None and abs(estimate - driver.daily_increase) <= SIZE_TOLERANCE * driver.daily_increase
    if estimate is not None:
        notes["size"] += f" = ${estimate:,.0f} vs billed ${driver.daily_increase:,.0f}"

    # Check 2: timing. The change happened within the look-back before the cost started, not after it.
    timing = False
    if primary_ev:
        lag = _hours(ts(primary_ev["EventTime"]), cost_start)
        timing = ts(primary_ev["EventTime"]) <= cost_start and lag <= lookback_hours
        notes["timing"] = f"change at {primary_ev['EventTime']}, cost from {cost_start:%Y-%m-%dT%H:%MZ} ({lag:.1f} h later)"

    # Check 3: exact ID.
    exact_id = primary in exact
    notes["exact_id"] = "change names every billed resource" if exact_id else "no change names these resources"

    # Check 4: owner. Owner tag on non-shared resources, else a human identity on the exact-match change.
    tags = _driver_tags(driver, pool)
    shared = any(t.get("shared") == "true" for t in tags.values())
    tag_owners = {t.get("owner") for t in tags.values() if t and t.get("shared") != "true"}
    owner, owner_source = None, "none"
    if len(tags) == len(wanted) and len(tag_owners) == 1 and None not in tag_owners:
        owner, owner_source = tag_owners.pop(), "tag"
    elif exact_id and _is_human(primary_ev):
        owner, owner_source = primary_ev["Username"], "change identity"
    notes["owner"] = f"{owner} (from {owner_source})" if owner else ("shared resource, no single owner" if shared else "no owner found")
    checks = Checks(size=size, timing=timing, exact_id=exact_id, owner=owner is not None, notes=notes)

    # Planned: approved ticket covering the date, linked by change-id tag or cited and consistent; or an active exception.
    change_ids = {t.get("change-id") for t in tags.values() if t}
    itype = driver.usage_type.split(":")[-1]
    planned_ids = []
    for k, t in pool.items():
        if "key" in t and "status" in t:
            covers = t["status"] == "Approved" and t["start_date"] and t["end_date"] and t["start_date"] <= source.spike_date <= t["end_date"]
            linked = t["key"] in change_ids or (k in findings.intent_evidence_ids and itype in t["description"]
                                                 and str(len(wanted)) in t["description"])
            if covers and linked:
                planned_ids.append(k)
        elif k.startswith("exc-") and wanted & set(t["resource_ids"]) and t["expires"] >= source.spike_date:
            planned_ids.append(k)
    planned = bool(planned_ids)

    projects = {t.get("project") for t in tags.values() if t}
    budget = next((b for b in pool.values() if "BudgetName" in b
                   and any(f"user:project${p}" in b["CostFilters"]["TagKeyValue"] for p in projects)), None)
    budget_note = None
    if budget:
        lim, act = budget["BudgetLimit"]["Amount"], budget["CalculatedSpend"]["ActualSpend"]["Amount"]
        fc = budget["CalculatedSpend"]["ForecastedSpend"]["Amount"]
        budget_note = f"{budget['BudgetName']}: ${act:,.2f} spent of ${lim:,.2f}, forecast ${fc:,.2f} ({'within' if fc <= lim else 'over'} budget)"

    # Used: compute idle if peak primary metric < 5%; network used if bytes flow after the start.
    used, usage_summary, usage_ids = None, "Utilization not checked.", [m["evidence_id"] for m in metrics]
    if driver.kind == "network" and bytes_metric:
        used = True
        usage_summary = (f"NAT traffic stepped up at {step['step']} by about {step['extra_gb_per_hour']:.0f} GB/hour" if step
                         else "NAT traffic is flowing")
    elif metrics:
        gpu = [m for m in metrics if "gpu" in m["metric_name"].lower()]
        series = gpu or [m for m in metrics if m["unit"] == "Percent"]
        if series:
            peak = max(max(m["values"]) for m in series)
            avg = statistics.mean(v for m in series for v in m["values"])
            used = peak >= IDLE_PEAK_PCT
            label = "GPU" if gpu else "CPU"
            usage_summary = f"{label} average {avg:.1f}%, peak {peak:.1f}% across {len(series)} series"

    quiet = sorted({f"{k}={v}" for t in tags.values() for k, v in t.items()
                    if k.lower() in QUIET_KEYS or str(v).lower() in QUIET_KEYS})

    # Verdict and action.
    confidence = checks.score
    if confidence <= 1:
        verdict = "unexplained"
    else:
        is_used = True if used is None else used
        verdict = {(True, True): "expected", (True, False): "oversized",
                   (False, True): "unbudgeted", (False, False): "likely_waste"}[(planned, is_used)]
        if verdict == "likely_waste" and quiet:
            verdict = "unbudgeted"
    needs_confirmation = confidence in (2, 3) or verdict == "unexplained" or used is None

    traffic = next((v for k, v in pool.items() if k.startswith("net-") and v["nat_gateway_id"] in wanted), None)
    s3_share = max((d["share_pct"] for d in traffic["destinations"] if "s3" in d["destination"].lower()), default=0.0) if traffic else 0.0
    proposed_fix = None
    if verdict == "likely_waste" and driver.kind == "compute":
        proposed_fix = "stop_instances"
    elif verdict in ("unbudgeted", "oversized") and driver.kind == "network" and s3_share >= S3_SHARE_FOR_ENDPOINT:
        proposed_fix = "s3_gateway_endpoint"
    action = {"likely_waste": "simulate_stop" if driver.kind == "compute" else "ask_owner", "expected": "none",
              "unexplained": "none"}.get(verdict, "ask_owner")

    deployment = next((v for k, v in pool.items() if k.startswith("dep-") and v["event_id"] == primary), None)
    fix_target = None
    if proposed_fix == "s3_gateway_endpoint" and deployment:
        fix_target = {"repository": deployment["repository"], "path": deployment["path"], "service": deployment["service"],
                      "team": deployment["team"], "deployment_id": deployment["evidence_id"]}

    # Routing.
    if verdict == "expected":
        route_to = None
    elif owner:
        route_to = owner
    elif deployment:
        route_to = deployment["team"]
    elif primary_ev and any(pool.get(k, {}).get("resource_id") in _resources(primary_ev) for k in pool if k.startswith("tag-")):
        route_to = next(v["tags"].get("owner") for k, v in pool.items()
                        if k.startswith("tag-") and v["resource_id"] in _resources(primary_ev)) or FALLBACK_ROUTE
    else:
        route_to = FALLBACK_ROUTE

    hours_running = round(_hours(cost_start, spike_end), 1)
    gaps = []
    if not primary:
        gaps.append(f"No change event explains this cost within the {lookback_hours}-hour look-back.")
    elif not exact_id:
        gaps.append(f"The most likely change ({primary}) does not name these resources, so there is no exact ID match.")
    if not owner:
        gaps.append("No single owner: " + ("the resource is shared by several teams." if shared
                                            else "resources are untagged and no exact-match change identity was found."))
    if not planned:
        gaps.append("No approved ticket or exception covers this spend. Please confirm; this is not proof it was unapproved.")
    if used is None:
        gaps.append("Utilization was not checked.")
    for g in findings.gaps:
        if g not in gaps:
            gaps.append(g)

    return Report(
        checks=checks, confidence=confidence, planned=planned, planned_evidence_ids=planned_ids, used=used,
        usage_summary=usage_summary, usage_evidence_ids=usage_ids, quiet_markers=quiet, verdict=verdict,
        needs_confirmation=needs_confirmation, action=action, proposed_fix=proposed_fix, route_to=route_to, owner=owner,
        owner_source=owner_source, primary_change_id=primary, cost_start=cost_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        hours_running=hours_running, already_spent=round(driver.daily_increase / 24 * hours_running, 2),
        avoidable_per_day=driver.daily_increase if proposed_fix else 0.0, budget_note=budget_note, gaps=gaps,
        fix_target=fix_target)


# --- validation ---------------------------------------------------------------------------------------

def validate_findings(findings: Findings, pool: dict) -> list[str]:
    errors = []
    fields = {"linked_change_ids": findings.linked_change_ids, "ignored_change_ids": findings.ignored_change_ids,
              "intent_evidence_ids": findings.intent_evidence_ids, "usage_evidence_ids": findings.usage_evidence_ids,
              "deployment_evidence_ids": findings.deployment_evidence_ids, "traffic_evidence_ids": findings.traffic_evidence_ids}
    for name, ids in fields.items():
        for eid in ids:
            if eid not in pool:
                errors.append(f"{name}: '{eid}' was not returned by any tool in this run")
    if not findings.claims:
        errors.append("claims: at least one cited claim is required")
    for i, claim in enumerate(findings.claims, 1):
        bad = [e for e in claim.evidence_ids if e not in pool]
        if bad:
            errors.append(f"claim {i} cites IDs not returned by any tool: {', '.join(bad)}")
    return errors


MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")


def fact_numbers(driver: Driver, report: Report, pool: dict) -> tuple[set[float], set[float]]:
    money = {driver.daily_increase, report.already_spent, report.avoidable_per_day, round(driver.daily_increase / 24, 2)}
    pct = {driver.share_pct}
    for v in pool.values():
        if "line_item_unblended_cost" in v:
            money |= {v["line_item_unblended_cost"], v["line_item_unblended_rate"]}
        if "BudgetName" in v:
            money |= {v["BudgetLimit"]["Amount"], v["CalculatedSpend"]["ActualSpend"]["Amount"],
                      v["CalculatedSpend"]["ForecastedSpend"]["Amount"]}
        if v.get("estimated_daily_cost"):
            money.add(v["estimated_daily_cost"])
        if "destinations" in v:
            pct |= {d["share_pct"] for d in v["destinations"]}
        if v.get("unit") == "Percent" and v.get("values"):
            pct |= {round(statistics.mean(v["values"]), 1), round(max(v["values"]), 1)}
    nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)%", report.usage_summary)]
    pct |= set(nums)
    return money, pct


def _close(x: float, allowed: set[float]) -> bool:
    return any(abs(x - a) <= max(0.6, 0.02 * abs(a)) for a in allowed)


def validate_explanation(expl: Explanation, driver: Driver, report: Report, pool: dict) -> list[str]:
    errors = []
    allowed_ids = set(pool) | set(driver.bill_evidence_ids)
    money, pct = fact_numbers(driver, report, pool)
    other_labels = {"likely waste", "oversized", "unbudgeted", "unexplained"} - {report.verdict.replace("_", " ")}
    if not expl.sentences:
        errors.append("the explanation has no sentences")
    for i, s in enumerate(expl.sentences, 1):
        if not s.evidence_ids:
            errors.append(f"sentence {i} has no evidence IDs")
        bad = [e for e in s.evidence_ids if e not in allowed_ids]
        if bad:
            errors.append(f"sentence {i} cites unknown IDs: {', '.join(bad)}")
        for m in MONEY.findall(s.text):
            if not _close(float(m.replace(",", "")), money):
                errors.append(f"sentence {i} states ${m}, which is not a code-computed or evidence amount")
        for p in PERCENT.findall(s.text):
            if not _close(float(p), pct):
                errors.append(f"sentence {i} states {p}%, which is not in the evidence")
        lowered = s.text.lower()
        for label in other_labels:
            if label in lowered:
                errors.append(f"sentence {i} uses the label '{label}', but code's verdict is {report.verdict}")
    for label in other_labels:
        if label in expl.headline.lower():
            errors.append(f"headline uses the label '{label}', but code's verdict is {report.verdict}")
    return errors
