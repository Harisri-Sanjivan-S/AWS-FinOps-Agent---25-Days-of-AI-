"""Data-source layer. `FixtureSource` reads planted, AWS-shaped JSON; a live source (boto3 / AWS MCP servers)
would implement the same methods. Never reads answer keys."""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

from detective.config import ROOT
from detective.fixturegen import load_scenario


class FixtureSource:
    def __init__(self, scenario_id: str, root: Path = ROOT, exceptions_path: Path | None = None):
        self.scenario_id = scenario_id
        self.root = root
        self.dir = root / "fixtures" / scenario_id
        if not self.dir.exists():
            raise FileNotFoundError(f"No fixtures for scenario {scenario_id!r} in {self.dir}")
        self.exceptions_path = exceptions_path

    def _load(self, name: str):
        return json.loads((self.dir / f"{name}.json").read_text())

    @cached_property
    def scenario(self) -> dict:
        return load_scenario(self.root / "scenarios" / f"{self.scenario_id}.json")

    @property
    def spike_date(self) -> str:
        return self.scenario["spike_date"]

    @property
    def case_prefix(self) -> str:
        return self.scenario["case_prefix"]

    @cached_property
    def anomaly(self) -> dict:
        return self._load("anomaly")

    @cached_property
    def bill_lines(self) -> list[dict]:
        return self._load("bill_lines")

    @cached_property
    def next_day_bill(self) -> dict:
        return self._load("next_day_bill")

    @cached_property
    def events(self) -> list[dict]:
        return self._load("cloudtrail_events")

    @cached_property
    def metrics(self) -> list[dict]:
        return self._load("metrics")

    @cached_property
    def tags(self) -> dict[str, dict]:
        return {t["resource_id"]: t for t in self._load("tags")}

    @cached_property
    def tickets(self) -> list[dict]:
        return self._load("tickets")

    @cached_property
    def budgets(self) -> list[dict]:
        return self._load("budgets")

    @cached_property
    def deployments(self) -> list[dict]:
        return self._load("deployments")

    @cached_property
    def flows(self) -> list[dict]:
        return self._load("flow_destinations")

    def exceptions(self) -> list[dict]:
        planted = self._load("exceptions")
        recorded = json.loads(self.exceptions_path.read_text()) if self.exceptions_path and self.exceptions_path.exists() else []
        return planted + recorded

    def add_exception(self, record: dict) -> None:
        if not self.exceptions_path:
            raise RuntimeError("This source has no writable exceptions store")
        recorded = json.loads(self.exceptions_path.read_text()) if self.exceptions_path.exists() else []
        self.exceptions_path.parent.mkdir(parents=True, exist_ok=True)
        self.exceptions_path.write_text(json.dumps(recorded + [record], indent=2) + "\n")

    @cached_property
    def _index(self) -> dict[str, dict]:
        records = [self.anomaly, *self.bill_lines, *self.events, *self.metrics, *self.tags.values(), *self.tickets,
                   *self.budgets, *self.deployments, *self.flows]
        records += [line for v in self.next_day_bill["variants"].values() for line in v]
        return {r["evidence_id"]: r for r in records}

    def record(self, evidence_id: str) -> dict | None:
        if evidence_id.startswith("exc-"):
            return next((e for e in self.exceptions() if e["evidence_id"] == evidence_id), None)
        return self._index.get(evidence_id)
