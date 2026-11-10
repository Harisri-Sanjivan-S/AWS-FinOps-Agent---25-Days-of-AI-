"""Typed records shared by detect, tools, agent, rules, pipeline and UI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["expected", "oversized", "unbudgeted", "likely_waste", "unexplained"]
CaseStatus = Literal["quiet", "awaiting_owner", "planned", "rejected", "fix_in_progress", "fixed", "needs_review"]

VERDICT_LABELS: dict[str, str] = {
    "expected": "Expected",
    "oversized": "Oversized",
    "unbudgeted": "Unbudgeted",
    "likely_waste": "Likely waste",
    "unexplained": "Unexplained",
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Driver(BaseModel):
    driver_id: str
    title: str
    resource_ids: list[str]
    product_code: str
    usage_type: str
    kind: Literal["compute", "network", "storage", "other"]
    tag_fingerprint: str
    daily_increase: float
    share_pct: float
    cause: Literal["usage", "rate"]
    usage_part: float
    rate_part: float
    start_date: str
    bill_evidence_ids: list[str]


class NoiseItem(BaseModel):
    resource_id: str
    usage_type: str
    daily_increase: float


class DetectResult(BaseModel):
    scenario_id: str
    anomaly_id: str
    anomaly_source: str = "AWS Cost Anomaly Detection"
    spike_date: str
    baseline_days: dict[str, float]
    baseline_mean: float
    baseline_sd: float
    spike_total: float
    increase: float
    increase_pct: float
    material: bool
    drivers: list[Driver]
    noise: list[NoiseItem]
    explained_pct: float


class ToolCall(BaseModel):
    seq: int
    name: str
    inputs: dict
    ok: bool
    evidence_ids: list[str]
    summary: str
    ms: int
    error: str | None = None


class Claim(BaseModel):
    text: str = Field(description="One factual statement about this cost driver.")
    evidence_ids: list[str] = Field(min_length=1, description="Evidence IDs (returned by tools) that support the statement.")


class Findings(BaseModel):
    """What the investigator found for one driver. Facts only, every claim cited; no verdict and no arithmetic."""

    linked_change_ids: list[str] = Field(default_factory=list, description=(
        "CloudTrail evidence IDs (ct-...) of the change(s) that most plausibly caused this cost, most likely first. "
        "Empty if no plausible change was found."))
    ignored_change_ids: list[str] = Field(default_factory=list, description="Change events you looked at and judged unrelated.")
    intent_evidence_ids: list[str] = Field(default_factory=list, description=(
        "Ticket, budget or exception evidence IDs that relate to whether this spend was planned (supporting or not)."))
    usage_evidence_ids: list[str] = Field(default_factory=list, description="Utilization metric evidence IDs (met-...).")
    deployment_evidence_ids: list[str] = Field(default_factory=list, description="Deployment record IDs (dep-...).")
    traffic_evidence_ids: list[str] = Field(default_factory=list, description="Traffic destination record IDs (net-...).")
    owner_hint: str | None = Field(default=None, description="Who appears responsible, as written in the evidence (tag or identity).")
    claims: list[Claim] = Field(default_factory=list, description="3 to 8 short factual statements, each citing evidence IDs.")
    gaps: list[str] = Field(default_factory=list, description="Evidence you looked for but could not find (e.g. 'no ticket found').")


class Checks(BaseModel):
    size: bool
    timing: bool
    exact_id: bool
    owner: bool
    notes: dict[str, str] = Field(default_factory=dict)

    @property
    def score(self) -> int:
        return sum([self.size, self.timing, self.exact_id, self.owner])


class Report(BaseModel):
    """Everything code decided for one driver."""

    checks: Checks
    confidence: int
    planned: bool
    planned_evidence_ids: list[str]
    used: bool | None
    usage_summary: str
    usage_evidence_ids: list[str]
    quiet_markers: list[str]
    verdict: Verdict
    needs_confirmation: bool
    action: Literal["simulate_stop", "ask_owner", "none"]
    proposed_fix: Literal["stop_instances", "s3_gateway_endpoint"] | None
    route_to: str | None
    owner: str | None
    owner_source: str
    primary_change_id: str | None
    cost_start: str
    hours_running: float
    already_spent: float
    avoidable_per_day: float
    budget_note: str | None
    gaps: list[str]
    fix_target: dict | None = None


class Sentence(BaseModel):
    text: str = Field(description="One plain-English sentence for the resource owner.")
    evidence_ids: list[str] = Field(default_factory=list, description="Evidence IDs supporting the sentence (required for facts).")


class Explanation(BaseModel):
    """Plain-English write-up of code's decision. Uses only the facts provided; never changes the verdict or numbers."""

    headline: str = Field(description="One short line summarising the case, without numbers.")
    sentences: list[Sentence] = Field(description="3 to 7 sentences, each citing evidence IDs.")


class Decision(BaseModel):
    action: Literal["planned", "not_mine", "reject", "approve"]
    reviewer: str
    at: str = Field(default_factory=now_iso)
    reason: str | None = None
    expiry: str | None = None
    new_owner: str | None = None


class ActionRecord(BaseModel):
    kind: Literal["stop", "pull_request", "exception", "reassign"]
    action_id: str
    status: str
    at: str = Field(default_factory=now_iso)
    url: str | None = None
    detail: dict = Field(default_factory=dict)


class NextDayResult(BaseModel):
    date: str
    expected_saving: float
    actual_saving: float
    confirmed: bool
    evidence_ids: list[str]
    note: str


class FixBrief(BaseModel):
    case_id: str
    repo: str
    branch: str
    target_folder: str
    owner_team: str
    verdict: str
    confidence: int
    daily_increase: float
    avoidable_per_day: float
    findings: list[Claim]
    required_change: str
    acceptance_criteria: list[str]
    constraints: list[str]
    pr_title: str


class UsageTotals(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    models: list[str] = Field(default_factory=list)

    def add(self, model: str, input_tokens: int, output_tokens: int, cost: float) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd = round(self.cost_usd + cost, 6)
        if model not in self.models:
            self.models.append(model)


class AuditEvent(BaseModel):
    at: str = Field(default_factory=now_iso)
    actor: str
    event: str
    detail: dict = Field(default_factory=dict)


class Case(BaseModel):
    case_id: str
    run_id: str
    scenario_id: str
    driver: Driver
    status: CaseStatus
    findings: Findings | None = None
    report: Report | None = None
    explanation: Explanation | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: UsageTotals = Field(default_factory=UsageTotals)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    assigned_to: str | None = None
    decisions: list[Decision] = Field(default_factory=list)
    actions: list[ActionRecord] = Field(default_factory=list)
    next_day: NextDayResult | None = None
    validation_errors: list[str] = Field(default_factory=list)
    audit: list[AuditEvent] = Field(default_factory=list)

    def log(self, actor: str, event: str, **detail) -> None:
        self.audit.append(AuditEvent(actor=actor, event=event, detail=detail))
