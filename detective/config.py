"""Settings from environment variables and an optional `.env` file (non-secret values only)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# USD per 1M tokens (input, output). Verify against https://aws.amazon.com/bedrock/pricing/ before measured runs.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus": (5.00, 25.00),
    "scripted": (0.0, 0.0),
}


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


@dataclass(frozen=True)
class Settings:
    aws_profile: str = "finops-poc"
    aws_region: str = "ap-southeast-2"
    model_fast: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    model_strong: str = "global.anthropic.claude-sonnet-4-6"
    model_opus: str = ""
    max_tokens: int = 2000
    turns: int = 16
    total_tokens: int = 120_000
    tool_call_cap: int = 10
    lookback_hours: int = 48
    cost_cap_usd: float = 25.0
    cost_warn_usd: tuple[float, ...] = (10.0, 20.0)
    investigation_cap_usd: float = 0.50
    investigation_cap_tokens: int = 300_000
    cost_override: bool = False
    demo_repo: str = "Harisri-Sanjivan-S/acme-platform"
    devin_bin: str = "/Applications/Devin.app/Contents/Resources/app/extensions/windsurf/devin/bin/devin"
    devin_model: str = ""
    devin_timeout_s: int = 600
    fixer: str = "template"
    tf_validate: bool = True
    terraform_bin: str = "terraform"
    ci_wait_s: int = 150
    runs_dir: Path = field(default=ROOT / "runs")

    def price(self, model_id: str) -> tuple[float, float]:
        for key, price in PRICES.items():
            if key in model_id:
                return price
        raise ValueError(f"No price configured for model {model_id!r}; add it to detective.config.PRICES")


@lru_cache
def get_settings() -> Settings:
    env = {**_load_dotenv(ROOT / ".env"), **os.environ}

    def get(name: str, default):
        value = env.get(name)
        if value is None or value == "":
            return default
        if isinstance(default, bool):
            return value.lower() in {"1", "true", "yes"}
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, Path):
            return Path(value)
        return value

    d = Settings()
    return Settings(
        aws_profile=get("AWS_PROFILE", d.aws_profile),
        aws_region=get("AWS_REGION", d.aws_region),
        model_fast=get("BEDROCK_MODEL_FAST", d.model_fast),
        model_strong=get("BEDROCK_MODEL_STRONG", d.model_strong),
        model_opus=get("BEDROCK_MODEL_OPUS", d.model_opus),
        cost_cap_usd=get("FINOPS_COST_CAP_USD", d.cost_cap_usd),
        cost_override=get("FINOPS_COST_OVERRIDE", d.cost_override),
        demo_repo=get("DEMO_REPO", d.demo_repo),
        devin_bin=get("DEVIN_BIN", d.devin_bin),
        devin_model=get("DEVIN_MODEL", d.devin_model),
        fixer=get("FINOPS_FIXER", d.fixer),
        tf_validate=get("FINOPS_TF_VALIDATE", d.tf_validate),
        terraform_bin=get("TERRAFORM_BIN", d.terraform_bin),
        ci_wait_s=get("FINOPS_CI_WAIT_S", d.ci_wait_s),
        runs_dir=get("FINOPS_RUNS_DIR", d.runs_dir),
    )
