"""Generate planted, AWS-shaped fixtures and an answer key from one scenario definition.

Usage: uv run python -m detective.fixturegen [scenarios/<name>.json]   (default: every scenario in scenarios/)
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOUR = timedelta(hours=1)


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def expand(spec: dict) -> list[dict]:
    """One spec with `count` + `id_prefix` becomes one spec per resource."""
    if "count" not in spec:
        return [spec]
    return [{**spec, "resource_id": f"{spec['id_prefix']}{i}"} for i in range(1, spec["count"] + 1)]


def item(spec: dict, *, cost: float | None = None, usage: float | None = None) -> dict:
    rate = spec["rate"]
    if usage is None:
        usage = round(cost / rate, 6)
    if cost is None:
        cost = usage * rate
    return {"spec": spec, "usage": usage, "cost": round(cost, 2)}


def cur_tags(tags: dict) -> dict:
    return {f"user_{k.replace('-', '_')}": v for k, v in tags.items()}


def render_day(day: str, items: list[dict], prefix: str) -> list[dict]:
    start = date.fromisoformat(day)
    return [
        {
            "evidence_id": f"{prefix}-{n:03d}",
            "line_item_usage_start_date": f"{start}T00:00:00Z",
            "line_item_usage_end_date": f"{start + timedelta(days=1)}T00:00:00Z",
            "line_item_product_code": it["spec"]["product_code"],
            "line_item_usage_type": it["spec"]["usage_type"],
            "line_item_resource_id": it["spec"]["resource_id"],
            "line_item_usage_amount": it["usage"],
            "pricing_unit": it["spec"]["unit"],
            "line_item_unblended_rate": it["spec"]["rate"],
            "line_item_unblended_cost": it["cost"],
            "resource_tags": cur_tags(it["spec"]["tags"]),
        }
        for n, it in enumerate(items, 1)
    ]


def instance_specs(scene: dict) -> list[dict]:
    inst = scene["instances"]
    per_instance = round(scene["daily_cost"] / inst["count"], 2)
    spec = {
        **inst,
        "product_code": "AmazonEC2",
        "usage_type": f"USE1-BoxUsage:{inst['instance_type']}",
        "unit": "Hrs",
        "rate": round(per_instance / 24, 7),
        "tags": scene["tags"],
        "cost": per_instance,
    }
    return expand(spec)


def generate(sc: dict) -> dict[str, object]:
    """Return {relative output path: JSON data}. Pure and deterministic for a given scenario."""
    rng = random.Random(sc["seed"])
    acct, region = sc["account_id"], sc["region"]
    spike_day, next_day = sc["spike_date"], sc["next_day_date"]
    spike_start = parse_time(f"{spike_day}T00:00:00Z")
    spike_end = spike_start + timedelta(days=1)
    baseline_mean = round(statistics.mean(sc["baseline_days"].values()), 2)
    scenes = {s["scene_id"]: s for s in sc["scenes"]}
    nat = next((s for s in sc["scenes"] if s["kind"] == "shared_nat_after_deploy"), None)

    # Steady workload: identical every day except "elastic" lines, which absorb each baseline day's wobble.
    steady = [s for spec in sc["steady"] for s in expand(spec)]
    base: list[dict] = []
    for spec in steady:
        if "usage_per_day" in spec:
            base.append(item(spec, usage=spec["usage_per_day"]))
        elif "daily_cost" in spec:
            base.append(item(spec, cost=spec["daily_cost"]))
    balance = next(s for s in steady if s.get("balance"))
    base.append(item(balance, cost=baseline_mean - sum(i["cost"] for i in base)))
    elastic = [i for i in base if i["spec"].get("elastic")]

    def baseline_items(total: float) -> list[dict]:
        delta = round(total - baseline_mean, 2)
        weights = [rng.random() for _ in elastic]
        shares = [round(delta * w / sum(weights), 2) for w in weights]
        shares[-1] = round(delta - sum(shares[:-1]), 2)
        bump = {id(i): s for i, s in zip(elastic, shares)}
        return [item(i["spec"], cost=i["cost"] + bump[id(i)]) if id(i) in bump else i for i in base]

    bill_lines: list[dict] = []
    for day, total in sc["baseline_days"].items():
        bill_lines += render_day(day, baseline_items(total), f"bill-{day.replace('-', '')}")

    # Scene and noise items on top of the steady base.
    nat_bytes = next(i for i in base if i["spec"]["usage_type"].endswith("NatGateway-Bytes"))
    nat_cost = nat["daily_cost"] if nat else 0.0
    nat_extra_per_hour = nat_cost / ((spike_end - parse_time(nat["data_start"])) / HOUR) if nat else 0.0

    def nat_bump(items: list[dict], extra_cost: float) -> list[dict]:
        if not extra_cost:
            return items
        return [item(i["spec"], cost=i["cost"] + extra_cost) if i is nat_bytes else i for i in items]

    instance_items = {
        s["scene_id"]: [item(spec, usage=24, cost=spec["cost"]) for spec in instance_specs(s)]
        for s in sc["scenes"]
        if s["kind"] == "new_instances"
    }
    noise_items = [item(n, cost=n["daily_cost"]) for n in sc["noise"]]
    all_instances = [i for items in instance_items.values() for i in items]

    spike_items = nat_bump(base, nat_cost) + all_instances + noise_items
    bill_lines += render_day(spike_day, spike_items, f"bill-{spike_day.replace('-', '')}")

    stopped = {sid for sid, s in scenes.items() if s["expected"].get("proposed_fix") == "stop_instances"}
    still_billed = {sid for sid in stopped if scenes[sid].get("relaunched_after_stop")}
    next_base = nat_bump(base, round(nat_extra_per_hour * 24, 2))
    variants = {
        "no_action": next_base + all_instances + noise_items,
        "after_fix": next_base
        + [i for sid, items in instance_items.items() if sid not in stopped - still_billed for i in items]
        + noise_items,
    }
    next_day_bill = {
        "date": next_day,
        "stopped_scenes": sorted(stopped),
        "variants": {
            name: render_day(next_day, items, f"bill-{next_day.replace('-', '')}-{name.replace('_', '')}")
            for name, items in variants.items()
        },
    }

    # CloudTrail: one event per planted change (scene launches, the deploy, and noise changes).
    def ct_event(time: str, name: str, source: str, identity: dict, resources: list[tuple[str, str]],
                 user_agent: str, source_ip: str, request: dict, console: bool = False) -> dict:
        event_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        username = identity.get("userName") or identity["arn"].rsplit("/", 1)[-1]
        detail = {
            "eventVersion": "1.10",
            "userIdentity": {"accountId": acct, **identity},
            "eventTime": time,
            "eventSource": source,
            "eventName": name,
            "awsRegion": region,
            "sourceIPAddress": source_ip,
            "userAgent": user_agent,
            "requestParameters": request,
            "readOnly": False,
            "eventID": event_id,
            "recipientAccountId": acct,
        }
        if console:
            detail["sessionCredentialFromConsole"] = "true"
        return {
            "EventId": event_id,
            "EventName": name,
            "EventTime": time,
            "EventSource": source,
            "Username": username,
            "ReadOnly": "false",
            "Resources": [{"ResourceType": t, "ResourceName": r} for t, r in resources],
            "CloudTrailEvent": detail,
        }

    events = []
    for s in sc["scenes"]:
        if s["kind"] == "new_instances" and s["launch"].get("omit_event"):
            continue
        if s["kind"] == "new_instances":
            ids = [i["spec"]["resource_id"] for i in instance_items[s["scene_id"]]]
            request = {"instanceType": s["instances"]["instance_type"], "minCount": len(ids), "maxCount": len(ids)}
            if s["tags"]:
                request["tagSpecificationSet"] = {"items": [{"resourceType": "instance", "tags": [{"key": k, "value": v} for k, v in s["tags"].items()]}]}
            lt = s["launch"]
            events.append(ct_event(lt["time"], "RunInstances", "ec2.amazonaws.com", lt["identity"],
                                   [("AWS::EC2::Instance", r) for r in ids], lt["user_agent"], lt["source_ip"],
                                   request, lt["console"]))
        else:
            d = s["deploy"]
            events.append(ct_event(d["time"], d["event_name"], d["event_source"], d["identity"],
                                   [(d["resource_type"], d["resource_id"])], d["user_agent"], d["source_ip"], d["request"]))
    for n in sc["noise"]:
        c = n["change"]
        events.append(ct_event(c["time"], c["event_name"], c["event_source"], c["identity"],
                               [(c["resource_type"], n["resource_id"])], c["user_agent"], c["source_ip"], {}))
    events.sort(key=lambda e: e["EventTime"])
    events = [{"evidence_id": f"ct-{n:03d}", **e} for n, e in enumerate(events, 1)]
    # Distractor changes are numbered after the planted ones so existing IDs stay stable.
    for x in sc.get("extra_events", []):
        e = ct_event(x["time"], x["event_name"], x["event_source"], x["identity"], [(x["resource_type"], x["resource_id"])],
                     x["user_agent"], x["source_ip"], x.get("request", {}))
        events.append({"evidence_id": f"ct-{len(events) + 1:03d}", **e})
    events.sort(key=lambda e: e["EventTime"])
    distractor_ids = [e["evidence_id"] for e in events
                      if any(r["ResourceName"] in {x["resource_id"] for x in sc.get("extra_events", [])} for r in e["Resources"])]

    # CloudWatch: hourly series over the metrics window.
    w_start, w_end = parse_time(sc["metrics_window"]["start"]), parse_time(sc["metrics_window"]["end"])
    metrics = []

    def series(namespace: str, name: str, unit: str, dims: dict, points: list[tuple[datetime, float]]) -> None:
        metrics.append({
            "evidence_id": f"met-{len(metrics) + 1:03d}",
            "namespace": namespace,
            "metric_name": name,
            "dimensions": dims,
            "stat": "Average",
            "period": 3600,
            "unit": unit,
            "timestamps": [iso(t) for t, _ in points],
            "values": [v for _, v in points],
        })

    for s in sc["scenes"]:
        if s["kind"] == "new_instances":
            launch = parse_time(s["launch"]["time"])
            first = max(w_start, launch.replace(minute=0, second=0) + (HOUR if launch.minute or launch.second else timedelta()))
            hours = [first + k * HOUR for k in range(int((w_end - first) / HOUR))]
            for i in instance_items[s["scene_id"]]:
                for m in s["metrics"]:
                    pts = [(t, round(min(100.0, max(0.0, rng.gauss(m["mean"], m["sd"]))), 2)) for t in hours]
                    series(m["namespace"], m["name"], m["unit"], {"InstanceId": i["spec"]["resource_id"]}, pts)
        else:
            base_gb, extra_gb = nat_bytes["usage"], nat_extra_per_hour / nat_bytes["spec"]["rate"]
            data_start = parse_time(s["data_start"])
            pts = []
            day = w_start
            while day < w_end:
                hours = [day + k * HOUR for k in range(24)]
                target = [base_gb / 24 + (extra_gb if t >= data_start else 0) for t in hours]
                noisy = [g * (1 + rng.gauss(0, 0.03)) for g in target]
                scale = sum(target) / sum(noisy)
                pts += [(t, round(g * scale * 1e9)) for t, g in zip(hours, noisy)]
                day += timedelta(days=1)
            m = s["metric"]
            series(m["namespace"], m["name"], m["unit"], {"NatGatewayId": s["nat_gateway_id"]}, pts)

    # Tags for every billed resource plus the deployed service.
    tag_map: dict[str, dict] = {}
    for line in bill_lines + next_day_bill["variants"]["no_action"]:
        rid = line["line_item_resource_id"]
        tag_map.setdefault(rid, next(i["spec"]["tags"] for i in spike_items if i["spec"]["resource_id"] == rid))
    if nat:
        tag_map[nat["deploy"]["resource_id"]] = nat["deploy"]["tags"]
    tags = [{"evidence_id": f"tag-{n:03d}", "resource_id": r, "tags": t} for n, (r, t) in enumerate(tag_map.items(), 1)]

    # Tickets are free text: no structured resource IDs, so linking them to resources is investigative work.
    tickets = [{"evidence_id": s["ticket"]["key"], **s["ticket"]} for s in sc["scenes"] if "ticket" in s]
    tickets += [{"evidence_id": t["key"], **t} for t in sc.get("tickets_extra", [])]

    # Deployment records (CI/CD) and NAT traffic destinations (VPC Flow Logs) for the network scene.
    deployments, flows = [], []
    if nat:
        d = nat["deploy"]
        [deploy_event] = [e for e in events if e["EventName"] == d["event_name"]
                          and any(r["ResourceName"] == d["resource_id"] for r in e["Resources"])]
        deployments.append({"evidence_id": "dep-001", "event_id": deploy_event["evidence_id"], "deployed_at": d["time"],
                            "resource_id": d["resource_id"], **d["record"]})
        extra_bytes = nat_extra_per_hour / nat_bytes["spec"]["rate"] * 1e9 * ((spike_end - parse_time(nat["data_start"])) / HOUR)
        flows.append({
            "evidence_id": "net-001",
            "nat_gateway_id": nat["nat_gateway_id"],
            "window_start": nat["data_start"],
            "window_end": iso(spike_end),
            "source": "VPC Flow Logs (aggregated, traffic added after the window start)",
            "total_bytes": round(extra_bytes),
            "destinations": [{"destination": x["destination"], "bytes": round(extra_bytes * x["share"]),
                              "share_pct": round(100 * x["share"], 1)} for x in nat["traffic"]["destinations"]],
        })

    budgets = [
        {
            "evidence_id": f"bud-{n:03d}",
            "BudgetName": b["name"],
            "BudgetType": "COST",
            "TimeUnit": "MONTHLY",
            "CostFilters": {"TagKeyValue": [f"user:project${b['project']}"]},
            "BudgetLimit": {"Amount": b["limit"], "Unit": "USD"},
            "CalculatedSpend": {
                "ActualSpend": {"Amount": b["actual_spend"], "Unit": "USD"},
                "ForecastedSpend": {"Amount": b["forecasted_spend"], "Unit": "USD"},
            },
        }
        for n, b in enumerate(sc["budgets"], 1)
    ]

    # Upstream anomaly (mirrors Cost Anomaly Detection) and the answer key.
    spike_total = round(sum(i["cost"] for i in spike_items), 2)
    increase = round(spike_total - baseline_mean, 2)
    pct = round(100 * increase / baseline_mean, 2)
    anomaly = {
        "evidence_id": "anom-001",
        "AnomalyId": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "AnomalyStartDate": spike_day,
        "DimensionValue": "AWS services",
        "Impact": {"TotalActualSpend": spike_total, "TotalExpectedSpend": baseline_mean,
                   "TotalImpact": increase, "TotalImpactPercentage": pct},
        "RootCauses": [
            {"Service": "Amazon Elastic Compute Cloud - Compute", "Region": region, "LinkedAccount": acct,
             "UsageType": f"USE1-BoxUsage:{s['instances']['instance_type']}" if s["kind"] == "new_instances" else "USE1-NatGateway-Bytes",
             "Impact": {"Contribution": s["daily_cost"]}}
            for s in sorted(sc["scenes"], key=lambda s: -s["daily_cost"])
        ],
    }

    spike_lines = [l for l in bill_lines if l["line_item_usage_start_date"].startswith(spike_day)]

    drivers = []
    for s in sc["scenes"]:
        if s["kind"] == "new_instances":
            rids = {i["spec"]["resource_id"] for i in instance_items[s["scene_id"]]}
            changed, start, usage_suffix = rids, s["launch"]["time"], ""
        else:
            rids = {s["nat_gateway_id"]}
            changed, start, usage_suffix = {s["deploy"]["resource_id"]}, s["data_start"], "NatGateway-Bytes"
        drivers.append({
            "scene_id": s["scene_id"],
            "title": s["title"],
            "resource_ids": sorted(rids),
            "daily_increase": s["daily_cost"],
            "start_time": start,
            "evidence": {
                "bill_lines": [l["evidence_id"] for l in spike_lines
                               if l["line_item_resource_id"] in rids and l["line_item_usage_type"].endswith(usage_suffix)],
                "change_events": [e["evidence_id"] for e in events if {r["ResourceName"] for r in e["Resources"]} & changed],
                "tags": [t["evidence_id"] for t in tags if t["resource_id"] in rids | changed],
                "metrics": [m["evidence_id"] for m in metrics if set(m["dimensions"].values()) & rids],
                "tickets": [t["evidence_id"] for t in tickets if t["key"] == s.get("tags", {}).get("change-id")],
                "deployments": [x["evidence_id"] for x in deployments] if s is nat else [],
                "traffic": [x["evidence_id"] for x in flows] if s is nat else [],
            },
            "expected": s["expected"],
            "saving_per_day": s["daily_cost"] if s["scene_id"] in stopped else 0.0,
        })

    answer_key = {
        "scenario_id": sc["scenario_id"],
        "case_prefix": sc["case_prefix"],
        "description": sc["description"],
        "distractor_change_events": distractor_ids,
        "injected_tickets": [t["key"] for t in sc.get("tickets_extra", [])],
        "spike_date": spike_day,
        "baseline_mean": baseline_mean,
        "baseline_sd": round(statistics.stdev(sc["baseline_days"].values()), 2),
        "spike_total": spike_total,
        "increase": increase,
        "increase_pct": pct,
        "materiality_passed": increase > 100 and pct > 20,
        "anomaly_id": anomaly["evidence_id"],
        "drivers": drivers,
        "ignored": [
            {"resource_id": n["resource_id"], "daily_increase": n["daily_cost"], "reason": "not a top driver"}
            for n in sc["noise"]
        ],
    }

    out = f"fixtures/{sc['scenario_id']}"
    return {
        f"{out}/anomaly.json": anomaly,
        f"{out}/bill_lines.json": bill_lines,
        f"{out}/next_day_bill.json": next_day_bill,
        f"{out}/cloudtrail_events.json": events,
        f"{out}/metrics.json": metrics,
        f"{out}/tags.json": tags,
        f"{out}/tickets.json": tickets,
        f"{out}/budgets.json": budgets,
        f"{out}/exceptions.json": sc["exceptions"],
        f"{out}/deployments.json": deployments,
        f"{out}/flow_destinations.json": flows,
        f"evals/answer_keys/{sc['scenario_id']}.json": answer_key,
    }


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        out[key] = deep_merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load_scenario(path: Path) -> dict:
    """Load a scenario; variants `extends` a base scenario, drop scenes and deep-merge scene overrides."""
    path = Path(path)
    sc = json.loads(path.read_text())
    if "extends" not in sc:
        return sc
    base = load_scenario(path.parent / f"{sc['extends']}.json")
    drop, overrides = set(sc.pop("drop_scenes", [])), sc.pop("scene_overrides", {})
    sc.pop("extends")
    merged = {**base, **sc}
    merged["scenes"] = [deep_merge(s, overrides.get(s["scene_id"], {})) for s in base["scenes"] if s["scene_id"] not in drop]
    return merged


def scenario_paths() -> list[Path]:
    return sorted((ROOT / "scenarios").glob("*.json"))


def dump(data: object) -> str:
    return json.dumps(data, indent=2) + "\n"


def main(argv: list[str]) -> None:
    for path in [Path(a) for a in argv] or scenario_paths():
        for rel, data in generate(load_scenario(path)).items():
            target = ROOT / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(dump(data))
            print(f"wrote {rel}")


if __name__ == "__main__":
    main(sys.argv[1:])
