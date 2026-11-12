"""The AI part: an investigator (Strands agent with read-only tools) and a writer (no tools), both on Amazon Bedrock.

Offline doubles with the same interface (ScriptedInvestigator, TemplateWriter) are used by the default tests and the
UI's offline mode. They contain no AI and are labelled as such wherever they appear."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from detective.config import Settings, get_settings
from detective.ledger import CostCapReached, Ledger
from detective.models import VERDICT_LABELS, Claim, Driver, Explanation, Findings, Report, Sentence
from detective.tools import Toolbox, ts

PROMPTS = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> tuple[str, str]:
    text = (PROMPTS / f"{name}.md").read_text()
    version = text.splitlines()[0].split(":", 1)[1].strip()
    return text, version


@dataclass
class AgentRun:
    output: Findings | Explanation
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    stop_reason: str = ""
    notes: list[dict] = field(default_factory=list)


class AgentIncomplete(RuntimeError):
    """The model stopped (e.g. a token or turn limit) without structured output. Carries the usage, which was billed."""

    def __init__(self, message: str, run: AgentRun):
        super().__init__(message)
        self.run = run


class Investigator(Protocol):
    model_id: str

    def investigate(self, driver: Driver, toolbox: Toolbox, feedback: list[str] | None = None) -> AgentRun: ...


class Writer(Protocol):
    model_id: str

    def write(self, driver: Driver, report: Report, pool: dict, feedback: list[str] | None = None) -> AgentRun: ...


def window(toolbox: Toolbox) -> tuple[str, str]:
    spike_start = ts(f"{toolbox.source.spike_date}T00:00:00Z")
    lo = spike_start - timedelta(hours=toolbox.lookback_hours)
    hi = spike_start + timedelta(days=1)
    return lo.strftime("%Y-%m-%dT%H:%M:%SZ"), hi.strftime("%Y-%m-%dT%H:%M:%SZ")


def investigation_prompt(driver: Driver, toolbox: Toolbox, feedback: list[str] | None) -> str:
    lo, hi = window(toolbox)
    brief = {"driver_id": driver.driver_id, "title": driver.title, "resource_ids": driver.resource_ids,
             "service": driver.product_code, "usage_type": driver.usage_type, "kind": driver.kind,
             "daily_increase_usd": driver.daily_increase, "spike_date": toolbox.source.spike_date,
             "look_back_window": {"start": lo, "end": hi}, "bill_evidence_ids": driver.bill_evidence_ids}
    text = f"Investigate this cost driver and return your findings.\n\n{json.dumps(brief, indent=2)}"
    if feedback:
        text += "\n\nYour previous findings were rejected by code for these reasons. Fix them:\n- " + "\n- ".join(feedback)
    return text


def writer_facts(driver: Driver, report: Report, pool: dict) -> dict:
    def summary(eid: str) -> dict | None:
        r = pool.get(eid)
        if r is None:
            return None
        if eid.startswith("ct-"):
            return {"id": eid, "time": r["EventTime"], "event": r["EventName"], "actor": r["Username"],
                    "resources": [x["ResourceName"] for x in r["Resources"]][:8]}
        if eid.startswith("tag-"):
            return {"id": eid, "resource": r["resource_id"], "tags": r["tags"]}
        if eid.startswith("met-"):
            return {"id": eid, "resource": next(iter(r["dimensions"].values())), "metric": r["metric_name"]}
        if "key" in r and "status" in r:
            return {"id": eid, "status": r["status"], "dates": [r["start_date"], r["end_date"]],
                    "summary": f"<untrusted_data>{r['summary']}</untrusted_data>"}
        if eid.startswith("dep-"):
            return {"id": eid, "service": r["service"], "version": r["version"], "path": r["path"], "team": r["team"],
                    "note": f"<untrusted_data>{r['note']}</untrusted_data>"}
        if eid.startswith("net-"):
            return {"id": eid, "destinations": r["destinations"]}
        return {"id": eid}

    cited = [report.primary_change_id] if report.primary_change_id else []
    cited += report.planned_evidence_ids + report.usage_evidence_ids[:4]
    cited += [k for k in pool if k.startswith(("tag-", "dep-", "net-", "bud-")) or ("key" in pool[k] and "status" in pool[k])]
    seen, evidence = set(), []
    for eid in cited:
        if eid not in seen and (s := summary(eid)):
            seen.add(eid)
            evidence.append(s)
    return {
        "driver": {"title": driver.title, "resource_ids": driver.resource_ids, "daily_increase_usd": driver.daily_increase,
                   "bill_evidence_ids": driver.bill_evidence_ids},
        "verdict": VERDICT_LABELS[report.verdict], "confidence": f"{report.confidence} of 4",
        "checks": report.checks.model_dump(), "planned": report.planned, "used": report.used,
        "usage_summary": report.usage_summary, "owner": report.owner, "owner_source": report.owner_source,
        "route_to": report.route_to, "primary_change_id": report.primary_change_id, "cost_start": report.cost_start,
        "already_spent_usd": report.already_spent, "avoidable_per_day_usd": report.avoidable_per_day,
        "proposed_fix": report.proposed_fix, "budget": report.budget_note, "gaps": report.gaps, "evidence": evidence,
    }


# --- Bedrock (real AI) ---------------------------------------------------------------------------------

class _Guard:
    """Strands hooks: hard tool-call cap and the cost guard, enforced in code during the agent loop."""

    def __init__(self, toolbox: Toolbox | None, ledger: Ledger, token_cap: int):
        self.toolbox, self.ledger, self.token_cap, self.projected = toolbox, ledger, token_cap, 0

    def register_hooks(self, registry) -> None:
        from strands.hooks import BeforeModelCallEvent, BeforeToolCallEvent
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(BeforeModelCallEvent, self.before_model)

    def before_tool(self, event) -> None:
        if self.toolbox and len(self.toolbox.calls) >= self.toolbox.cap:
            event.cancel_tool = f"Tool-call limit ({self.toolbox.cap}) reached. Return your findings now."

    def before_model(self, event) -> None:
        self.projected += event.projected_input_tokens or 0
        try:
            self.ledger.check()
        except CostCapReached as exc:
            event.cancel = str(exc)
            return
        if self.projected > self.token_cap:
            event.cancel = f"Investigation token cap ({self.token_cap}) reached."


def _bedrock_model(model_id: str, settings: Settings):
    import boto3
    from strands.models import BedrockModel
    session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
    return BedrockModel(boto_session=session, model_id=model_id, temperature=0.0, max_tokens=settings.max_tokens,
                        streaming=False)


def _usage(result) -> tuple[int, int]:
    usage = result.metrics.accumulated_usage
    return int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))


def strands_tools(toolbox: Toolbox) -> list:
    from strands import tool

    def out(result: dict) -> str:
        return json.dumps(result, default=str)

    @tool
    def get_bill_lines(resource_ids: list[str], date: str | None = None) -> str:
        """CUR 2.0 bill lines (usage, rate, cost, tags) for resources on one day.

        Args:
            resource_ids: Resource IDs to look up.
            date: Day as YYYY-MM-DD. Defaults to the spike day.
        """
        return out(toolbox.get_bill_lines(resource_ids, date))

    @tool
    def lookup_change_events(start: str, end: str, resource_id: str | None = None) -> str:
        """CloudTrail write events in a time window, optionally only those naming one resource.

        Args:
            start: Window start, ISO 8601 UTC (e.g. 2026-10-18T00:00:00Z).
            end: Window end, ISO 8601 UTC.
            resource_id: Optional resource ID to filter on.
        """
        return out(toolbox.lookup_change_events(start, end, resource_id))

    @tool
    def get_tags(resource_ids: list[str]) -> str:
        """Resource tags (owner, project, change-id, shared, DR or schedule markers).

        Args:
            resource_ids: Resource IDs to look up.
        """
        return out(toolbox.get_tags(resource_ids))

    @tool
    def find_ticket(query: str) -> str:
        """Search change tickets (Jira / ServiceNow) by key or words such as instance type, service or project.

        Args:
            query: A ticket key (e.g. CHG-1042) or a few search words.
        """
        return out(toolbox.find_ticket(query))

    @tool
    def get_budget(project: str) -> str:
        """AWS Budgets cost budget for a project tag value.

        Args:
            project: The value of the resource's project tag.
        """
        return out(toolbox.get_budget(project))

    @tool
    def check_exception(resource_ids: list[str]) -> str:
        """Recorded exceptions (spend previously approved as planned) for resources.

        Args:
            resource_ids: Resource IDs to check.
        """
        return out(toolbox.check_exception(resource_ids))

    @tool
    def get_utilization(resource_ids: list[str], metric: str | None = None) -> str:
        """CloudWatch utilization summary (average and peak per resource; NAT bytes with step-up time).

        Args:
            resource_ids: Resource IDs (several at once is fine).
            metric: Optional filter such as 'cpu', 'gpu' or 'bytes'.
        """
        return out(toolbox.get_utilization(resource_ids, metric))

    @tool
    def get_deployment(event_id: str) -> str:
        """CI/CD deployment record linked to a CloudTrail change event (service, version, repo path, team, note).

        Args:
            event_id: The CloudTrail evidence ID, e.g. ct-006.
        """
        return out(toolbox.get_deployment(event_id))

    @tool
    def get_traffic_destinations(nat_gateway_id: str) -> str:
        """VPC Flow Logs aggregate: top destinations of traffic through a NAT gateway.

        Args:
            nat_gateway_id: The NAT gateway ID, e.g. nat-0c1.
        """
        return out(toolbox.get_traffic_destinations(nat_gateway_id))

    return [get_bill_lines, lookup_change_events, get_tags, find_ticket, get_budget, check_exception,
            get_utilization, get_deployment, get_traffic_destinations]


def agent_notes(messages: list) -> list[dict]:
    """The agent's short plain-English notes, each paired with the tool it called next (for the investigation view)."""
    notes = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        blocks = m.get("content", [])
        text = " ".join(b["text"].strip() for b in blocks if isinstance(b, dict) and b.get("text"))
        tools = [b["toolUse"]["name"] for b in blocks if isinstance(b, dict) and b.get("toolUse")]
        if text and tools:
            notes.append({"text": text[:300], "tool": tools[0]})
    return notes


class BedrockInvestigator:
    def __init__(self, model_id: str, settings: Settings | None = None, ledger: Ledger | None = None):
        self.model_id = model_id
        self.settings = settings or get_settings()
        self.ledger = ledger or Ledger(settings=self.settings)

    def investigate(self, driver: Driver, toolbox: Toolbox, feedback: list[str] | None = None) -> AgentRun:
        from strands import Agent
        system, version = load_prompt("investigator")
        self.ledger.check()
        started = time.perf_counter()
        agent = Agent(model=_bedrock_model(self.model_id, self.settings), tools=strands_tools(toolbox), system_prompt=system,
                      callback_handler=None, hooks=[_Guard(toolbox, self.ledger, self.settings.investigation_cap_tokens)])
        result = agent(investigation_prompt(driver, toolbox, feedback), structured_output_model=Findings,
                       limits={"turns": self.settings.turns, "total_tokens": self.settings.total_tokens})
        tin, tout = _usage(result)
        self.ledger.record(self.model_id, tin, tout, purpose=f"investigate {driver.driver_id}")
        run = AgentRun(result.structured_output, self.model_id, version, tin, tout, time.perf_counter() - started,
                       str(result.stop_reason), notes=agent_notes(agent.messages))
        if run.output is None:
            raise AgentIncomplete(f"Investigator returned no structured findings (stop reason: {result.stop_reason}).", run)
        return run


class BedrockWriter:
    def __init__(self, model_id: str, settings: Settings | None = None, ledger: Ledger | None = None):
        self.model_id = model_id
        self.settings = settings or get_settings()
        self.ledger = ledger or Ledger(settings=self.settings)

    def write(self, driver: Driver, report: Report, pool: dict, feedback: list[str] | None = None) -> AgentRun:
        from strands import Agent
        system, version = load_prompt("writer")
        self.ledger.check()
        started = time.perf_counter()
        agent = Agent(model=_bedrock_model(self.model_id, self.settings), tools=[], system_prompt=system,
                      callback_handler=None, hooks=[_Guard(None, self.ledger, self.settings.investigation_cap_tokens)])
        prompt = "Facts decided by code (JSON):\n" + json.dumps(writer_facts(driver, report, pool), indent=1, default=str)
        if feedback:
            prompt += "\n\nYour previous explanation was rejected by code for these reasons. Fix them:\n- " + "\n- ".join(feedback)
        result = agent(prompt, structured_output_model=Explanation, limits={"turns": 3, "total_tokens": 20_000})
        tin, tout = _usage(result)
        self.ledger.record(self.model_id, tin, tout, purpose=f"write {driver.driver_id}")
        run = AgentRun(result.structured_output, self.model_id, version, tin, tout, time.perf_counter() - started,
                       str(result.stop_reason))
        if run.output is None:
            raise AgentIncomplete(f"Writer returned no structured explanation (stop reason: {result.stop_reason}).", run)
        return run


# --- Offline doubles (no AI) ---------------------------------------------------------------------------

class ScriptedInvestigator:
    """Deterministic playbook with the same tools and output type as the AI investigator. Not AI; for tests/offline."""

    model_id = "scripted"

    def investigate(self, driver: Driver, toolbox: Toolbox, feedback: list[str] | None = None) -> AgentRun:
        started = time.perf_counter()
        ids, lo, hi = driver.resource_ids, *window(toolbox)
        claims, gaps, intent, usage_ids, dep_ids, net_ids = [], [], [], [], [], []

        bill = toolbox.get_bill_lines(ids)
        if bill["evidence_ids"]:
            claims.append(Claim(text=f"{len(bill['items'])} bill lines on {toolbox.source.spike_date} for these resources.",
                                evidence_ids=bill["evidence_ids"][:8]))

        events = toolbox.lookup_change_events(lo, hi, ids[0]) if driver.kind == "compute" else {"items": []}
        exact = [e for e in events.get("items", []) if set(ids) <= set(e["resources"])]
        if not exact:
            events = toolbox.lookup_change_events(lo, hi)
        items = events.get("items", [])
        exact = [e for e in items if set(ids) <= set(e["resources"])]
        deploys = [e for e in items if e["event_name"] in {"UpdateService", "UpdateFunctionCode", "CreateDeployment"}]
        linked = exact[:1] or deploys[-1:]
        ignored = [e["evidence_id"] for e in items if e not in linked]
        for e in linked:
            claims.append(Claim(text=f"{e['event_name']} by {e['actor']} at {e['time']}.", evidence_ids=[e["evidence_id"]]))
        if not linked:
            gaps.append("No change event names these resources or plausibly caused this cost.")

        lead_resources = [r for e in linked for r in e["resources"] if r not in ids]
        tags = toolbox.get_tags(ids + lead_resources)
        own = [t for t in tags.get("items", []) if t["resource_id"] in ids]
        if own and all(not t["tags"] for t in own):
            claims.append(Claim(text="The resources have no tags.", evidence_ids=[t["evidence_id"] for t in own][:8]))
        change_id = next((t["tags"].get("change-id") for t in own if t["tags"].get("change-id")), None)
        project = next((t["tags"].get("project") for t in own if t["tags"].get("project")), None)

        query = change_id or (driver.usage_type.split(":")[-1] if driver.kind == "compute" else
                              next((e["request"].get("service") for e in linked if e["request"].get("service")), driver.usage_type))
        tickets = toolbox.find_ticket(query)
        intent += tickets.get("evidence_ids", [])
        if not tickets.get("evidence_ids"):
            gaps.append("No ticket found.")
        exceptions = toolbox.check_exception(ids)
        intent += exceptions.get("evidence_ids", [])
        if project:
            intent += toolbox.get_budget(project).get("evidence_ids", [])

        util = toolbox.get_utilization(ids)
        usage_ids = util.get("evidence_ids", [])
        if usage_ids:
            claims.append(Claim(text="Utilization metrics retrieved for these resources.", evidence_ids=usage_ids[:8]))

        if driver.kind == "network":
            for e in linked:
                dep_ids += toolbox.get_deployment(e["evidence_id"]).get("evidence_ids", [])
            net_ids = toolbox.get_traffic_destinations(ids[0]).get("evidence_ids", [])
            if dep_ids:
                claims.append(Claim(text="A deployment record is linked to the change.", evidence_ids=dep_ids))
            if net_ids:
                claims.append(Claim(text="Flow-log destinations for the NAT gateway retrieved.", evidence_ids=net_ids))

        owner_hint = next((t["tags"].get("owner") for t in own if t["tags"].get("owner")), None) or \
            (exact[0]["actor"] if exact else None)
        findings = Findings(linked_change_ids=[e["evidence_id"] for e in linked], ignored_change_ids=ignored,
                            intent_evidence_ids=intent, usage_evidence_ids=usage_ids, deployment_evidence_ids=dep_ids,
                            traffic_evidence_ids=net_ids, owner_hint=owner_hint, claims=claims, gaps=gaps)
        why = {"get_bill_lines": "Checking the bill lines to see whether usage or the rate went up.",
               "lookup_change_events": "Looking for the change that came just before the cost started.",
               "get_tags": "Checking the tags to find the owner and any project or change ID.",
               "find_ticket": "Searching for an approved ticket that covers this spend.",
               "check_exception": "Checking for a recorded exception.",
               "get_budget": "Checking the project's budget.",
               "get_utilization": "Checking whether the resources are actually being used.",
               "get_deployment": "Reading the deployment record behind the change.",
               "get_traffic_destinations": "Checking where the NAT traffic is going."}
        notes = [{"text": why.get(c.name, f"Calling {c.name}."), "tool": c.name} for c in toolbox.calls]
        return AgentRun(findings, self.model_id, "scripted-1", seconds=time.perf_counter() - started, stop_reason="end_turn",
                        notes=notes)


class TemplateWriter:
    """Deterministic sentences built from code's facts. Not AI; for tests/offline."""

    model_id = "scripted"

    def write(self, driver: Driver, report: Report, pool: dict, feedback: list[str] | None = None) -> AgentRun:
        bill = driver.bill_evidence_ids[:8]
        s = [Sentence(text=f"{driver.title} added ${driver.daily_increase:,.2f} per day on the spike day.", evidence_ids=bill)]
        ev = pool.get(report.primary_change_id) if report.primary_change_id else None
        if ev:
            how = "and it names these resources" if report.checks.exact_id else "shortly before the cost started, with no exact ID match"
            s.append(Sentence(text=f"{ev['EventName']} by {ev['Username']} at {ev['EventTime']}, {how}.",
                              evidence_ids=[report.primary_change_id]))
        else:
            s.append(Sentence(text="No change event explains this cost in the look-back window.", evidence_ids=bill))
        tag_ids = [k for k, v in pool.items() if k.startswith("tag-") and v["resource_id"] in driver.resource_ids][:8]
        if report.owner:
            s.append(Sentence(text=f"Owner: {report.owner} (from the {report.owner_source}).",
                              evidence_ids=tag_ids if report.owner_source == "tag" else [report.primary_change_id]))
        else:
            s.append(Sentence(text="No single owner was found, so the case is routed for confirmation.", evidence_ids=tag_ids or bill))
        if report.planned:
            s.append(Sentence(text="An approved ticket or exception covers this spend.", evidence_ids=report.planned_evidence_ids))
        else:
            s.append(Sentence(text="No approved ticket or exception covers it; please confirm whether it was planned.", evidence_ids=bill))
        s.append(Sentence(text=report.usage_summary + ".", evidence_ids=report.usage_evidence_ids[:8] or bill))
        if report.proposed_fix:
            fix = "stopping the instances (reversible; disks are kept)" if report.proposed_fix == "stop_instances" \
                else "adding an S3 gateway endpoint so S3 traffic bypasses the NAT gateway"
            s.append(Sentence(text=f"About ${report.already_spent:,.2f} has already been spent; ${report.avoidable_per_day:,.2f} per day "
                                   f"is avoidable by {fix}. Nothing changes until the owner approves.", evidence_ids=bill))
        else:
            s.append(Sentence(text=f"About ${report.already_spent:,.2f} has been spent since the cost started.", evidence_ids=bill))
        expl = Explanation(headline=f"{driver.title}: {VERDICT_LABELS[report.verdict]}", sentences=s)
        return AgentRun(expl, self.model_id, "template-1", stop_reason="end_turn")
