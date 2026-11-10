"""Cost ledger for every Bedrock call, with the project's spending guards ($25 refusal, $10/$20 warnings)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from detective.config import Settings, get_settings


class CostCapReached(RuntimeError):
    """Raised when the cumulative Bedrock spend has reached the configured cap."""


class Ledger:
    def __init__(self, path: Path | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.path = Path(path or self.settings.runs_dir / "ledger.jsonl")

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def total(self) -> float:
        return round(sum(e["cost_usd"] for e in self.entries()), 6)

    def cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        price_in, price_out = self.settings.price(model_id)
        return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)

    def record(self, model_id: str, input_tokens: int, output_tokens: int, *, purpose: str, run_id: str = "") -> dict:
        entry = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_id": run_id,
            "purpose": purpose,
            "model": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": self.cost(model_id, input_tokens, output_tokens),
        }
        if model_id != "scripted":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        return entry

    def status(self) -> dict:
        total, s = self.total(), self.settings
        if total >= s.cost_cap_usd:
            level = "blocked"
        elif any(total >= w for w in s.cost_warn_usd):
            level = "warning"
        else:
            level = "ok"
        return {"total_usd": total, "cap_usd": s.cost_cap_usd, "level": level,
                "passed_warnings": [w for w in s.cost_warn_usd if total >= w]}

    def check(self, upcoming_usd: float = 0.0) -> None:
        """Refuse new Bedrock work once the cap is (or would be) reached, unless the user set the override."""
        if self.settings.cost_override:
            return
        total = self.total()
        if total + upcoming_usd >= self.settings.cost_cap_usd:
            raise CostCapReached(
                f"Bedrock spend ${total:.2f} (+${upcoming_usd:.2f} planned) reaches the ${self.settings.cost_cap_usd:.2f} cap. "
                "Set FINOPS_COST_OVERRIDE=1 only if the user approves.")


def estimate_usd(model_id: str, investigations: int, settings: Settings | None = None,
                 input_tokens: int = 45_000, output_tokens: int = 3_000) -> float:
    """Rough upfront estimate for a batch of driver investigations (investigator + writer)."""
    settings = settings or get_settings()
    price_in, price_out = settings.price(model_id)
    return round(investigations * (input_tokens * price_in + output_tokens * price_out) / 1_000_000, 2)
