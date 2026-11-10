"""Naive baseline: the same model gets all the planted data in one prompt per driver, with no tools and no rules,
and is asked for the verdict directly. Used only to compare against the agent + rules design.

    uv run python -m evals.baseline                  # prints the estimate and prompt sizes; no calls
    uv run python -m evals.baseline --runs 3 --confirm
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from datetime import datetime
from pathlib import Path

from detective.config import get_settings
from detective.detect import detect
from detective.ledger import Ledger
from detective.sources import FixtureSource
from evals.run import RESULTS, SETS, answer_key

QUESTION = """You are a FinOps analyst. One cost driver from today's AWS bill is below, followed by ALL the raw data we have
(bill lines for the spike day, CloudTrail events, tags, tickets, budgets, exceptions, CloudWatch metrics, deployment
records, flow logs). Decide the verdict for this driver.

Verdicts: expected (planned and used), oversized (planned but idle), unbudgeted (unplanned but used),
likely_waste (unplanned and idle), unexplained (not enough evidence).

Reply with JSON only: {"verdict": "...", "owner": "...", "planned": true/false, "used": true/false,
"evidence_ids": ["..."], "reason": "one sentence"}"""


def build_prompt(source: FixtureSource, driver) -> str:
    spike = source.spike_date
    data = {
        "driver": driver.model_dump(include={"title", "resource_ids", "usage_type", "daily_increase"}),
        "bill_lines_spike_day": [line for line in source.bill_lines if line["line_item_usage_start_date"].startswith(spike)],
        "cloudtrail_events": [{k: v for k, v in e.items() if k != "CloudTrailEvent"} for e in source.events],
        "tags": list(source.tags.values()), "tickets": source.tickets, "budgets": source.budgets,
        "exceptions": source.exceptions(), "metrics": source.metrics, "deployments": source.deployments, "flow_logs": source.flows,
    }
    return QUESTION + "\n\nDATA:\n" + json.dumps(data, separators=(",", ":"))


def call_bedrock(model_id: str, prompt: str) -> tuple[str, int, int]:
    import boto3
    s = get_settings()
    client = boto3.Session(profile_name=s.aws_profile, region_name=s.aws_region).client("bedrock-runtime")
    resp = client.converse(modelId=model_id, messages=[{"role": "user", "content": [{"text": prompt}]}],
                           inferenceConfig={"maxTokens": 400, "temperature": 0})
    usage = resp["usage"]
    return resp["output"]["message"]["content"][0]["text"], usage["inputTokens"], usage["outputTokens"]


def main(argv: list[str] | None = None, results_dir: Path = RESULTS, caller=call_bedrock) -> dict | None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--confirm", action="store_true")
    args = p.parse_args(argv)
    s = get_settings()
    ledger = Ledger()
    jobs = [(sc, d) for sc in SETS for d in detect(FixtureSource(sc)).drivers]
    prompts = {(sc, d.driver_id): build_prompt(FixtureSource(sc), d) for sc, d in jobs}
    approx_tokens = sum(len(t) / 3.5 for t in prompts.values()) * args.runs
    est = ledger.cost(s.model_fast, int(approx_tokens), 300 * len(jobs) * args.runs)
    print(f"Baseline: {len(jobs)} drivers x {args.runs} runs, ~{approx_tokens / 1000:.0f}k input tokens on {s.model_fast} "
          f"= about ${est:.2f}. Ledger so far ${ledger.total():.4f}.")
    if not args.confirm:
        print("Not running. Re-run with --confirm to approve this spend.")
        return None
    ledger.check(upcoming_usd=est)

    rows = []
    for run in range(1, args.runs + 1):
        for sc, d in jobs:
            source = FixtureSource(sc)
            exp = next(k for k in answer_key(sc)["drivers"] if sorted(k["resource_ids"]) == sorted(d.resource_ids))["expected"]
            started = time.perf_counter()
            text, tin, tout = caller(s.model_fast, prompts[(sc, d.driver_id)])
            ledger.record(s.model_fast, tin, tout, purpose=f"baseline {sc} {d.driver_id}")
            match = re.search(r"\{.*\}", text, re.DOTALL)
            try:
                answer = json.loads(match.group(0)) if match else {}
            except json.JSONDecodeError:
                answer = {}
            ids = answer.get("evidence_ids", []) if isinstance(answer.get("evidence_ids"), list) else []
            rows.append({"run": run, "scenario": sc, "driver": d.title, "expected": exp["verdict"],
                         "verdict": answer.get("verdict", "unparseable"), "correct": answer.get("verdict") == exp["verdict"],
                         "citations_valid": bool(ids) and all(source.record(i) is not None for i in ids),
                         "seconds": round(time.perf_counter() - started, 2), "cost_usd": ledger.cost(s.model_fast, tin, tout)})
    data = {"created": datetime.now().isoformat(timespec="seconds"), "mode": "baseline", "model": s.model_fast, "runs": args.runs,
            "summary": {"metrics": {
                "Verdict accuracy": f"{100 * sum(r['correct'] for r in rows) / len(rows):.0f}%",
                "Valid citations": f"{100 * sum(r['citations_valid'] for r in rows) / len(rows):.0f}%",
                "Seconds per driver": f"{statistics.mean(r['seconds'] for r in rows):.1f}",
                "Cost per driver": f"${statistics.mean(r['cost_usd'] for r in rows):.4f}"}},
            "cases": [], "baseline": rows}
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{datetime.now():%Y%m%d-%H%M%S}-baseline.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data["summary"], indent=2), f"\nSaved {path}")
    return data


if __name__ == "__main__":
    main()
