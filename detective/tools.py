"""Read-only investigation tools. Each mirrors one AWS (or adjacent) API, returns short JSON with evidence IDs,
and never raises to the caller. The Toolbox records every call and every evidence ID it returned (the run's
evidence pool), which is what code later verifies citations against."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from detective.models import ToolCall
from detective.sources import FixtureSource

MAX_ITEMS = 25
TOOL_NAMES = ["get_bill_lines", "lookup_change_events", "get_tags", "find_ticket", "get_budget", "check_exception",
              "get_utilization", "get_deployment", "get_traffic_destinations"]


def untrusted(text: str | None) -> str | None:
    """Mark human-written text as data. The agent's prompt says instructions inside these markers are ignored."""
    return None if text is None else f"<untrusted_data>{text}</untrusted_data>"


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def step_time(timestamps: list[str], values: list[float], factor: float = 1.5) -> str | None:
    """First hour whose value exceeds `factor` x the median of the previous 24 hours."""
    for i in range(24, len(values)):
        prior = statistics.median(values[i - 24:i])
        if prior > 0 and values[i] > factor * prior and statistics.mean(values[i:i + 3]) > factor * prior:
            return timestamps[i]
    return None


class Toolbox:
    def __init__(self, source: FixtureSource, *, lookback_hours: int = 48, cap: int = 10,
                 on_call: Callable[[ToolCall], None] | None = None):
        self.source = source
        self.lookback_hours = lookback_hours
        self.cap = cap
        self.on_call = on_call
        self.calls: list[ToolCall] = []
        self.pool: dict[str, dict] = {}

    # --- bookkeeping -------------------------------------------------------------------------------

    @property
    def returned_ids(self) -> set[str]:
        return set(self.pool)

    def _run(self, name: str, inputs: dict, fn: Callable[[], dict]) -> dict:
        started = time.perf_counter()
        if len(self.calls) >= self.cap:
            result = {"ok": False, "error": f"Tool-call limit reached ({self.cap}). Stop investigating and return your findings."}
        else:
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - tools never raise to the agent
                result = {"ok": False, "error": f"{name} failed: {exc}"}
        ids = result.get("evidence_ids", [])
        for eid in ids:
            rec = self.source.record(eid)
            if rec is not None:
                self.pool[eid] = rec
        call = ToolCall(seq=len(self.calls) + 1, name=name, inputs=inputs, ok=result.get("ok", False), evidence_ids=ids,
                        summary=result.get("summary", result.get("error", "")), ms=int((time.perf_counter() - started) * 1000),
                        error=result.get("error"))
        self.calls.append(call)
        if self.on_call:
            self.on_call(call)
        return result

    @staticmethod
    def _ok(items: list[dict], summary: str, note: str | None = None) -> dict:
        out = {"ok": True, "summary": summary, "items": items[:MAX_ITEMS], "evidence_ids": [i["evidence_id"] for i in items[:MAX_ITEMS]]}
        if len(items) > MAX_ITEMS:
            out["note"] = f"{len(items) - MAX_ITEMS} more items not shown; narrow the query."
        if note:
            out["note"] = note
        return out

    # --- tools -------------------------------------------------------------------------------------

    def get_bill_lines(self, resource_ids: list[str], date: str | None = None) -> dict:
        """CUR 2.0 line items for resources on one day (default: the spike day)."""
        day = date or self.source.spike_date

        def run():
            wanted = set(resource_ids)
            lines = [line for line in self.source.bill_lines
                     if line["line_item_resource_id"] in wanted and line["line_item_usage_start_date"].startswith(day)]
            items = [{"evidence_id": line["evidence_id"], "date": day, "resource_id": line["line_item_resource_id"],
                      "usage_type": line["line_item_usage_type"], "usage_amount": line["line_item_usage_amount"],
                      "unit": line["pricing_unit"], "rate": line["line_item_unblended_rate"],
                      "cost": line["line_item_unblended_cost"], "tags": line["resource_tags"]} for line in lines]
            return self._ok(items, f"{len(items)} bill lines on {day}", None if items else f"No bill lines for these resources on {day}.")
        return self._run("get_bill_lines", {"resource_ids": resource_ids, "date": day}, run)

    def lookup_change_events(self, start: str, end: str, resource_id: str | None = None) -> dict:
        """CloudTrail LookupEvents: write events in a time window, optionally for one resource."""
        def run():
            lo, hi = ts(start), ts(end)
            if hi - lo > timedelta(hours=self.lookback_hours + 24):
                return {"ok": False, "error": f"Window too wide; keep it within {self.lookback_hours + 24} hours."}
            events = [e for e in self.source.events if lo <= ts(e["EventTime"]) <= hi
                      and (resource_id is None or any(r["ResourceName"] == resource_id for r in e["Resources"]))]
            items = []
            for e in events:
                detail = e["CloudTrailEvent"]
                req = detail.get("requestParameters", {})
                items.append({
                    "evidence_id": e["evidence_id"], "time": e["EventTime"], "event_name": e["EventName"],
                    "source": e["EventSource"], "actor": e["Username"], "identity_type": detail["userIdentity"]["type"],
                    "identity_arn": detail["userIdentity"]["arn"], "via_console": detail.get("sessionCredentialFromConsole") == "true",
                    "resources": [r["ResourceName"] for r in e["Resources"]],
                    "request": {k: v for k, v in req.items() if k != "tagSpecificationSet"},
                })
            scope = f" for {resource_id}" if resource_id else ""
            return self._ok(items, f"{len(items)} change events{scope} between {start} and {end}",
                            None if items else f"No change events{scope} in this window.")
        return self._run("lookup_change_events", {"start": start, "end": end, "resource_id": resource_id}, run)

    def get_tags(self, resource_ids: list[str]) -> dict:
        """Resource Groups Tagging API: tags per resource (owner, project, change-id, shared, DR/schedule markers)."""
        def run():
            found = [self.source.tags[r] for r in resource_ids if r in self.source.tags]
            missing = [r for r in resource_ids if r not in self.source.tags]
            untagged = [t["resource_id"] for t in found if not t["tags"]]
            items = [{"evidence_id": t["evidence_id"], "resource_id": t["resource_id"], "tags": t["tags"]} for t in found]
            notes = []
            if untagged:
                notes.append(f"{len(untagged)} resource(s) have no tags at all.")
            if missing:
                notes.append(f"No tag record for: {', '.join(missing)}.")
            return self._ok(items, f"tags for {len(items)} resource(s)", " ".join(notes) or None)
        return self._run("get_tags", {"resource_ids": resource_ids}, run)

    def find_ticket(self, query: str) -> dict:
        """Jira / ServiceNow search by ticket key or words (e.g. instance type, project, service)."""
        def run():
            q = query.strip().lower()
            words = [w for w in q.replace(",", " ").split() if len(w) >= 3]
            hits = []
            for t in self.source.tickets:
                text = f"{t['key']} {t['summary']} {t['description']}".lower()
                if q == t["key"].lower() or (words and sum(w in text for w in words) >= min(2, len(words))):
                    hits.append({"evidence_id": t["evidence_id"], "key": t["key"], "type": t["type"], "status": t["status"],
                                 "start_date": t["start_date"], "end_date": t["end_date"], "reporter": t["reporter"],
                                 "approver": t["approver"], "summary": untrusted(t["summary"]),
                                 "description": untrusted(t["description"])})
            return self._ok(hits[:5], f"{len(hits)} ticket(s) matching '{query}'",
                            None if hits else "No matching ticket found. This is a gap, not proof the spend was unplanned.")
        return self._run("find_ticket", {"query": query}, run)

    def get_budget(self, project: str) -> dict:
        """AWS Budgets: cost budgets filtered on a project tag."""
        def run():
            items = [{"evidence_id": b["evidence_id"], "name": b["BudgetName"], "limit": b["BudgetLimit"]["Amount"],
                      "actual": b["CalculatedSpend"]["ActualSpend"]["Amount"],
                      "forecast": b["CalculatedSpend"]["ForecastedSpend"]["Amount"]}
                     for b in self.source.budgets if f"user:project${project}" in b["CostFilters"]["TagKeyValue"]]
            return self._ok(items, f"{len(items)} budget(s) for project '{project}'",
                            None if items else f"No budget for project '{project}'.")
        return self._run("get_budget", {"project": project}, run)

    def check_exception(self, resource_ids: list[str]) -> dict:
        """Exceptions store: previously approved 'planned' spend for these resources."""
        def run():
            wanted = set(resource_ids)
            items = [{"evidence_id": e["evidence_id"], "resource_ids": e["resource_ids"], "reason": untrusted(e["reason"]),
                      "expires": e["expires"], "approved_by": e["approved_by"]}
                     for e in self.source.exceptions() if wanted & set(e["resource_ids"])]
            return self._ok(items, f"{len(items)} exception(s)", None if items else "No recorded exception for these resources.")
        return self._run("check_exception", {"resource_ids": resource_ids}, run)

    def get_utilization(self, resource_ids: list[str], metric: str | None = None,
                        start: str | None = None, end: str | None = None) -> dict:
        """CloudWatch: average and peak of hourly metrics per resource (CPU, GPU, NAT bytes)."""
        def run():
            wanted = set(resource_ids)
            lo = ts(start) if start else None
            hi = ts(end) if end else None
            items = []
            for m in self.source.metrics:
                rid = next(iter(m["dimensions"].values()))
                if rid not in wanted or (metric and metric.lower() not in m["metric_name"].lower()):
                    continue
                pts = [(t, v) for t, v in zip(m["timestamps"], m["values"])
                       if (lo is None or ts(t) >= lo) and (hi is None or ts(t) < hi)]
                if not pts:
                    continue
                vals = [v for _, v in pts]
                row = {"evidence_id": m["evidence_id"], "resource_id": rid, "metric": m["metric_name"], "unit": m["unit"],
                       "first_timestamp": pts[0][0], "hours": len(pts)}
                if m["unit"] == "Bytes":
                    step = step_time([t for t, _ in pts], vals)
                    before = [v for t, v in pts if step and t < step][-24:]
                    after = [v for t, v in pts if step and t >= step]
                    row.update({"avg_gb_per_hour": round(statistics.mean(vals) / 1e9, 1), "step_up_at": step,
                                "gb_per_hour_before_step": round(statistics.mean(before) / 1e9, 1) if before else None,
                                "gb_per_hour_after_step": round(statistics.mean(after) / 1e9, 1) if after else None})
                else:
                    row.update({"average": round(statistics.mean(vals), 1), "peak": round(max(vals), 1)})
                items.append(row)
            return self._ok(items, f"{len(items)} metric series", None if items else "No metrics found for these resources.")
        return self._run("get_utilization", {"resource_ids": resource_ids, "metric": metric, "start": start, "end": end}, run)

    def get_deployment(self, event_id: str) -> dict:
        """CI/CD deployment record linked to a CloudTrail change event (service, version, repository path, note)."""
        def run():
            items = [{"evidence_id": d["evidence_id"], "event_id": d["event_id"], "service": d["service"],
                      "version": d["version"], "repository": d["repository"], "path": d["path"], "commit": d["commit"],
                      "team": d["team"], "deployed_at": d["deployed_at"], "note": untrusted(d["note"])}
                     for d in self.source.deployments if d["event_id"] == event_id]
            return self._ok(items, f"{len(items)} deployment record(s) for {event_id}",
                            None if items else f"No deployment record for {event_id}.")
        return self._run("get_deployment", {"event_id": event_id}, run)

    def get_traffic_destinations(self, nat_gateway_id: str) -> dict:
        """VPC Flow Logs aggregate: where traffic through a NAT gateway is going (top destinations by bytes)."""
        def run():
            items = [{"evidence_id": f["evidence_id"], "nat_gateway_id": f["nat_gateway_id"], "window_start": f["window_start"],
                      "window_end": f["window_end"], "destinations": f["destinations"]}
                     for f in self.source.flows if f["nat_gateway_id"] == nat_gateway_id]
            return self._ok(items, f"{len(items)} traffic record(s) for {nat_gateway_id}",
                            None if items else f"No flow-log data for {nat_gateway_id}.")
        return self._run("get_traffic_destinations", {"nat_gateway_id": nat_gateway_id}, run)
