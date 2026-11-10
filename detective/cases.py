"""File-based run and case store (runs/<run_id>/). Production path: DynamoDB."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from detective.config import get_settings
from detective.models import Case, DetectResult, now_iso


class RunStore:
    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir)
        self.run_id = self.dir.name
        (self.dir / "cases").mkdir(parents=True, exist_ok=True)

    @classmethod
    def create(cls, scenario_id: str, mode: str, runs_dir: Path | None = None) -> RunStore:
        runs_dir = Path(runs_dir or get_settings().runs_dir)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        store = cls(runs_dir / f"{stamp}-{scenario_id}")
        store.write_meta({"run_id": store.run_id, "scenario_id": scenario_id, "mode": mode, "created": now_iso()})
        return store

    @property
    def exceptions_path(self) -> Path:
        return self.dir / "exceptions.json"

    def write_meta(self, meta: dict) -> None:
        current = self.meta() if (self.dir / "run.json").exists() else {}
        (self.dir / "run.json").write_text(json.dumps({**current, **meta}, indent=2, default=str) + "\n")

    def meta(self) -> dict:
        return json.loads((self.dir / "run.json").read_text())

    def save_detect(self, result: DetectResult) -> None:
        (self.dir / "detect.json").write_text(result.model_dump_json(indent=2) + "\n")

    def detect(self) -> DetectResult | None:
        path = self.dir / "detect.json"
        return DetectResult.model_validate_json(path.read_text()) if path.exists() else None

    def save_case(self, case: Case) -> None:
        (self.dir / "cases" / f"{case.case_id}.json").write_text(case.model_dump_json(indent=2) + "\n")

    def load_case(self, case_id: str) -> Case:
        return Case.model_validate_json((self.dir / "cases" / f"{case_id}.json").read_text())

    def cases(self) -> list[Case]:
        return [Case.model_validate_json(p.read_text()) for p in sorted((self.dir / "cases").glob("*.json"))]


def list_runs(runs_dir: Path | None = None) -> list[RunStore]:
    runs_dir = Path(runs_dir or get_settings().runs_dir)
    if not runs_dir.exists():
        return []
    return [RunStore(p) for p in sorted(runs_dir.iterdir(), reverse=True) if (p / "run.json").exists()]
