"""Code measures first: baseline, materiality gate, and breakdown of the increase into cost drivers. No AI."""

from __future__ import annotations

import statistics
from collections import defaultdict

from detective.models import DetectResult, Driver, NoiseItem
from detective.sources import FixtureSource

MATERIAL_USD = 100.0
MATERIAL_PCT = 20.0
EXPLAIN_SHARE = 0.90


def _kind(usage_type: str) -> str:
    if "BoxUsage" in usage_type:
        return "compute"
    if "NatGateway" in usage_type or "DataTransfer" in usage_type:
        return "network"
    if "Storage" in usage_type or "Snapshot" in usage_type:
        return "storage"
    return "other"


def _fingerprint(tags: dict) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(tags.items())) or "untagged"


def _title(kind: str, usage_type: str, ids: list[str], tags: dict) -> str:
    what = usage_type.split(":")[-1] if ":" in usage_type else usage_type.split("-", 1)[-1]
    if kind == "compute":
        return f"{len(ids)} x {what} instances" + (" (untagged)" if not tags else f" ({tags.get('user_project', 'tagged')})")
    if "NatGateway" in usage_type:
        return f"NAT gateway data processing ({ids[0]}" + (", shared)" if tags.get("user_shared") == "true" else ")")
    return f"{what} ({len(ids)} resource{'s' if len(ids) > 1 else ''})"


def detect(source: FixtureSource) -> DetectResult:
    spike = source.spike_date
    by_day: dict[str, list[dict]] = defaultdict(list)
    for line in source.bill_lines:
        by_day[line["line_item_usage_start_date"][:10]].append(line)
    baseline_days = sorted(d for d in by_day if d < spike)[-4:]
    totals = {d: round(sum(line["line_item_unblended_cost"] for line in by_day[d]), 2) for d in baseline_days}
    mean = round(statistics.mean(totals.values()), 2)
    sd = round(statistics.stdev(totals.values()), 2)
    spike_total = round(sum(line["line_item_unblended_cost"] for line in by_day[spike]), 2)
    increase = round(spike_total - mean, 2)
    pct = round(100 * increase / mean, 2)
    material = increase > MATERIAL_USD and pct > MATERIAL_PCT

    # Per (resource, usage type): spike-day cost and usage vs the baseline mean (absent on a day = 0).
    def key(line):
        return line["line_item_resource_id"], line["line_item_usage_type"]

    spike_lines = {key(line): line for line in by_day[spike]}
    base = defaultdict(lambda: {"cost": [], "usage": [], "rate": []})
    for d in baseline_days:
        present = {key(line): line for line in by_day[d]}
        for k in set(present) | set(spike_lines):
            line = present.get(k)
            base[k]["cost"].append(line["line_item_unblended_cost"] if line else 0.0)
            base[k]["usage"].append(line["line_item_usage_amount"] if line else 0.0)
            if line:
                base[k]["rate"].append(line["line_item_unblended_rate"])

    groups: dict[tuple, dict] = {}
    for k, line in spike_lines.items():
        old_cost = statistics.mean(base[k]["cost"])
        delta = line["line_item_unblended_cost"] - old_cost
        if abs(delta) < 0.5:
            continue
        old_usage = statistics.mean(base[k]["usage"])
        new_rate = line["line_item_unblended_rate"]
        old_rate = statistics.mean(base[k]["rate"]) if base[k]["rate"] else new_rate
        usage_part = (line["line_item_usage_amount"] - old_usage) * old_rate
        rate_part = (new_rate - old_rate) * line["line_item_usage_amount"]
        gk = (line["line_item_product_code"], line["line_item_usage_type"], _fingerprint(line["resource_tags"]))
        g = groups.setdefault(gk, {"ids": [], "delta": 0.0, "usage": 0.0, "rate": 0.0, "evidence": [], "tags": line["resource_tags"]})
        g["ids"].append(line["line_item_resource_id"])
        g["delta"] += delta
        g["usage"] += usage_part
        g["rate"] += rate_part
        g["evidence"].append(line["evidence_id"])

    ranked = sorted(groups.items(), key=lambda kv: -kv[1]["delta"])
    drivers, noise, running = [], [], 0.0
    for (product, usage_type, fp), g in ranked:
        if material and running < EXPLAIN_SHARE * increase and g["delta"] > 0:
            running += g["delta"]
            kind = _kind(usage_type)
            drivers.append(Driver(
                driver_id=f"drv-{len(drivers) + 1}", title=_title(kind, usage_type, sorted(g["ids"]), g["tags"]),
                resource_ids=sorted(g["ids"]), product_code=product, usage_type=usage_type, kind=kind, tag_fingerprint=fp,
                daily_increase=round(g["delta"], 2), share_pct=round(100 * g["delta"] / increase, 1),
                cause="rate" if abs(g["rate"]) > abs(g["usage"]) else "usage",
                usage_part=round(g["usage"], 2), rate_part=round(g["rate"], 2), start_date=spike,
                bill_evidence_ids=sorted(g["evidence"])))
        elif g["delta"] >= 1.0:
            noise += [NoiseItem(resource_id=r, usage_type=usage_type, daily_increase=round(g["delta"] / len(g["ids"]), 2))
                      for r in g["ids"]]

    return DetectResult(
        scenario_id=source.scenario_id, anomaly_id=source.anomaly["evidence_id"], spike_date=spike,
        baseline_days=totals, baseline_mean=mean, baseline_sd=sd, spike_total=spike_total, increase=increase,
        increase_pct=pct, material=material, drivers=drivers, noise=noise,
        explained_pct=round(100 * running / increase, 1) if increase else 0.0)
