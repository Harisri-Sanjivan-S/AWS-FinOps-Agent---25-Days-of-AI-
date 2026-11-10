"""Orchestration used by the UI and the eval runner: detect -> investigate -> verify -> decide -> write -> route,
then the owner actions. The UI only calls this module."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from detective import actions
from detective.agent import (
    AgentRun,
    BedrockInvestigator,
    BedrockWriter,
    Investigator,
    ScriptedInvestigator,
    TemplateWriter,
    Writer,
)
from detective.cases import RunStore
from detective.config import Settings, get_settings
from detective.detect import detect
from detective.ledger import CostCapReached, Ledger
from detective.models import Case, Decision, DetectResult, Driver
from detective.rules import evaluate, validate_explanation, validate_findings
from detective.sources import FixtureSource
from detective.tools import Toolbox

EventFn = Callable[[str, object], None]


def _noop(kind: str, payload: object) -> None:
    pass


@dataclass
class Agents:
    """Model routing: fast tier first, one retry with code's feedback, then one escalation to the strong tier."""

    investigators: list[Investigator]
    writers: list[Writer]
    mode: str


def make_agents(mode: str = "offline", settings: Settings | None = None, ledger: Ledger | None = None,
                model: str | None = None) -> Agents:
    settings = settings or get_settings()
    if mode == "offline":
        return Agents([ScriptedInvestigator()], [TemplateWriter()], mode)
    if mode != "bedrock":
        raise ValueError(f"Unknown mode {mode!r}")
    ledger = ledger or Ledger(settings=settings)
    fast = model or settings.model_fast
    tiers = [fast, fast] + ([settings.model_strong] if settings.model_strong != fast else [])
    return Agents([BedrockInvestigator(m, settings, ledger) for m in tiers],
                  [BedrockWriter(m, settings, ledger) for m in tiers], mode)


def source_for(store: RunStore, scenario_id: str) -> FixtureSource:
    return FixtureSource(scenario_id, exceptions_path=store.exceptions_path)


def start_run(scenario_id: str, mode: str = "offline") -> tuple[RunStore, FixtureSource, DetectResult]:
    store = RunStore.create(scenario_id, mode)
    source = source_for(store, scenario_id)
    result = detect(source)
    store.save_detect(result)
    return store, source, result


def _record_usage(case: Case, run: AgentRun, ledger: Ledger | None) -> None:
    cost = ledger.cost(run.model, run.input_tokens, run.output_tokens) if ledger and run.model != "scripted" else 0.0
    case.usage.add(run.model, run.input_tokens, run.output_tokens, cost)


def investigate_driver(store: RunStore, source: FixtureSource, driver: Driver, index: int, agents: Agents,
                       on_event: EventFn = _noop, settings: Settings | None = None) -> Case:
    settings = settings or get_settings()
    ledger = Ledger(settings=settings) if agents.mode == "bedrock" else None
    case = Case(case_id=f"{source.case_prefix}-{index:02d}", run_id=store.run_id, scenario_id=source.scenario_id,
                driver=driver, status="needs_review")
    case.log("code", "driver detected", daily_increase=driver.daily_increase, resources=len(driver.resource_ids))
    started = time.perf_counter()
    toolbox = Toolbox(source, lookback_hours=settings.lookback_hours, cap=settings.tool_call_cap,
                      on_call=lambda call: on_event("tool", (case.case_id, call)))
    try:
        on_event("step", (case.case_id, "Investigating"))
        feedback, findings = None, None
        for attempt, investigator in enumerate(agents.investigators, 1):
            toolbox.cap = len(toolbox.calls) + settings.tool_call_cap
            run = investigator.investigate(driver, toolbox, feedback)
            _record_usage(case, run, ledger)
            case.prompt_versions["investigator"] = run.prompt_version
            on_event("step", (case.case_id, "Verifying"))
            errors = validate_findings(run.output, toolbox.pool)
            case.log(run.model, "findings returned", attempt=attempt, tool_calls=len(toolbox.calls), errors=errors)
            if not errors:
                findings = run.output
                break
            feedback = errors
            case.validation_errors = errors
        case.tool_calls = toolbox.calls
        if findings is None:
            case.log("code", "findings rejected; needs human review")
            return _finish(store, case, started)
        case.findings, case.validation_errors = findings, []

        on_event("step", (case.case_id, "Deciding"))
        report = evaluate(driver, findings, toolbox.pool, source, settings.lookback_hours)
        case.report = report
        case.log("code", "decision", verdict=report.verdict, confidence=report.confidence, route_to=report.route_to)

        on_event("step", (case.case_id, "Writing"))
        feedback = None
        for attempt, writer in enumerate(agents.writers, 1):
            run = writer.write(driver, report, toolbox.pool, feedback)
            _record_usage(case, run, ledger)
            case.prompt_versions["writer"] = run.prompt_version
            errors = validate_explanation(run.output, driver, report, toolbox.pool)
            case.log(run.model, "explanation returned", attempt=attempt, errors=errors)
            if not errors:
                case.explanation = run.output
                break
            feedback = errors
        if case.explanation is None:
            case.explanation = TemplateWriter().write(driver, report, toolbox.pool).output
            case.log("code", "AI write-up rejected; deterministic write-up used", errors=feedback)

        case.status = "quiet" if report.verdict == "expected" else "awaiting_owner"
        case.assigned_to = report.route_to
        case.log("code", "routed", to=report.route_to, status=case.status)
    except CostCapReached as exc:
        case.validation_errors = [str(exc)]
        case.log("code", "stopped by cost guard", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any failure becomes a case that needs human review
        case.validation_errors = [f"{type(exc).__name__}: {exc}"]
        case.log("code", "investigation failed", error=str(exc))
    return _finish(store, case, started, on_event)


def _finish(store: RunStore, case: Case, started: float, on_event: EventFn = _noop) -> Case:
    case.usage.seconds = round(time.perf_counter() - started, 2)
    store.save_case(case)
    on_event("step", (case.case_id, "Done"))
    on_event("case", case)
    return case


def investigate_all(scenario_id: str, mode: str = "offline", on_event: EventFn = _noop,
                    agents: Agents | None = None) -> tuple[RunStore, DetectResult, list[Case]]:
    store, source, result = start_run(scenario_id, mode)
    on_event("detect", result)
    if not result.material:
        return store, result, []
    agents = agents or make_agents(mode)
    cases = [investigate_driver(store, source, d, i, agents, on_event) for i, d in enumerate(result.drivers, 1)]
    return store, result, cases


# --- owner actions ------------------------------------------------------------------------------------

class ActionError(ValueError):
    pass


def _require(case: Case, *statuses: str) -> None:
    if case.status not in statuses:
        raise ActionError(f"{case.case_id} is '{case.status}'; this action needs {' or '.join(statuses)}.")


def mark_planned(store: RunStore, case: Case, reviewer: str, reason: str, expiry: str) -> Case:
    _require(case, "awaiting_owner")
    record = actions.record_exception(source_for(store, case.scenario_id), case, reviewer, reason, expiry)
    case.decisions.append(Decision(action="planned", reviewer=reviewer, reason=reason, expiry=expiry))
    case.actions.append(record)
    case.status = "planned"
    case.log(reviewer, "marked planned", exception=record.action_id, expiry=expiry)
    store.save_case(case)
    return case


def reassign(store: RunStore, case: Case, reviewer: str, new_owner: str, reason: str = "") -> Case:
    _require(case, "awaiting_owner")
    if not new_owner or new_owner == case.assigned_to:
        raise ActionError("Pick a different owner.")
    case.decisions.append(Decision(action="not_mine", reviewer=reviewer, new_owner=new_owner, reason=reason or None))
    case.actions.append(actions.ActionRecord(kind="reassign", action_id=actions._id("re", case.case_id, new_owner),
                                             status="reassigned", detail={"from": case.assigned_to, "to": new_owner}))
    case.log(reviewer, "not mine", new_owner=new_owner)
    case.assigned_to = new_owner
    store.save_case(case)
    return case


def reject(store: RunStore, case: Case, reviewer: str, reason: str) -> Case:
    _require(case, "awaiting_owner")
    if not reason.strip():
        raise ActionError("A reason is required to reject.")
    case.decisions.append(Decision(action="reject", reviewer=reviewer, reason=reason))
    case.status = "rejected"
    case.log(reviewer, "rejected", reason=reason)
    store.save_case(case)
    return case


def approve(store: RunStore, case: Case, reviewer: str, fixer=None, on_event: EventFn = _noop) -> Case:
    """The only path to an action: a human approval. Stop is simulated; a code fix becomes a pull request."""
    _require(case, "awaiting_owner")
    fix = case.report.proposed_fix if case.report else None
    if fix is None:
        raise ActionError("There is no proposed fix for this case. Use Planned, Not mine or Reject.")
    case.decisions.append(Decision(action="approve", reviewer=reviewer))
    case.log(reviewer, "approved fix", fix=fix)
    if fix == "stop_instances":
        record = actions.simulate_stop(case)
        case.actions.append(record)
        case.status = "fixed"
        case.log("code", "stop simulated", action_id=record.action_id)
    else:
        if fixer is None:
            raise ActionError("A code fix needs a fixer (template or Devin).")
        case.status = "fix_in_progress"
        store.save_case(case)
        result = fixer.run(case, on_event=on_event)
        case.actions.append(actions.ActionRecord(kind="pull_request", action_id=result.branch, status=result.status,
                                                 url=result.pr_url, detail=result.model_dump(exclude={"pr_url"})))
        case.log(result.executor, "pull request " + ("opened" if result.pr_url else "not opened"), url=result.pr_url,
                 status=result.status)
        if not result.pr_url:
            case.status = "awaiting_owner"
    store.save_case(case)
    return case


def next_day(store: RunStore, case: Case) -> Case:
    if not any(a.kind in ("stop", "pull_request") for a in case.actions):
        raise ActionError("Run the next-day check after a fix has been approved.")
    case.next_day = actions.next_day_check(source_for(store, case.scenario_id), case)
    case.log("code", "next-day check", confirmed=case.next_day.confirmed, actual_saving=case.next_day.actual_saving)
    store.save_case(case)
    return case
