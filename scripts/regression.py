"""Regression runs on Amazon Bedrock across the four-week calendar (2-29 Nov 2026), then realistic owner decisions.

    uv run python scripts/regression.py                    # estimate only
    uv run python scripts/regression.py --budget 23 --confirm

Each pass investigates every alerting day once (in a seeded random order), then plays owner decisions:
most stops are approved and checked the next day, some are marked planned, rejected or left waiting; unexplained cases
are sometimes re-routed. Code fixes are never approved here: pull requests are opened on demand from the inbox.
Stops when the next day would take new spend past --budget (and the ledger guard still applies)."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detective.config import ROOT, get_settings
from detective.detect import detect
from detective.ledger import Ledger, estimate_usd
from detective.pipeline import approve, investigate_all, mark_planned, next_day, reassign, reject
from detective.sources import FixtureSource


def month_days() -> list[str]:
    return sorted(p.stem for p in (ROOT / "scenarios").glob("nov*.json") if json.loads(p.read_text()).get("week"))


def owner_decisions(store, cases, rng: random.Random) -> dict:
    tally = {"approved": 0, "planned": 0, "rejected": 0, "rerouted": 0, "waiting": 0}
    for case in cases:
        r = case.report
        if not r or case.status != "awaiting_owner":
            continue
        roll = rng.random()
        if r.proposed_fix == "stop_instances":
            if roll < 0.65:
                next_day(store, approve(store, case, reviewer=case.assigned_to))
                tally["approved"] += 1
            elif roll < 0.75:
                expiry = (date.fromisoformat(case.driver.start_date) + timedelta(days=7)).isoformat()
                mark_planned(store, case, case.assigned_to, "Team confirmed this run is part of a planned experiment.",
                             expiry)
                tally["planned"] += 1
            elif roll < 0.83:
                reject(store, case, case.assigned_to, "Will right-size it ourselves this week.")
                tally["rejected"] += 1
            else:
                tally["waiting"] += 1
        elif r.proposed_fix is None and case.assigned_to == "finops-team" and roll < 0.5:
            reassign(store, case, "finops-team", "platform-team")
            tally["rerouted"] += 1
        else:
            tally["waiting"] += 1  # code fixes wait for an owner to open the PR on demand
    return tally


def main(argv: list[str] | None = None, mode: str = "bedrock") -> dict:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--budget", type=float, default=23.0, help="max new Bedrock spend for this batch (USD)")
    p.add_argument("--max-passes", type=int, default=50)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--confirm", action="store_true")
    args = p.parse_args(argv)
    s = get_settings()
    days = [d for d in month_days() if detect(FixtureSource(d)).material]
    drivers = {d: len(detect(FixtureSource(d)).drivers) for d in days}
    per_pass = estimate_usd(s.model_fast, sum(drivers.values()))
    ledger = Ledger()
    start = ledger.total()
    print(f"{len(days)} alerting days, {sum(drivers.values())} drivers per pass, estimate ~${per_pass:.2f} per pass; "
          f"budget ${args.budget:.2f} → about {int(args.budget // per_pass)} passes. Ledger ${start:.2f}, "
          f"guard ${s.cost_cap_usd:.0f}.")
    if mode == "bedrock" and not args.confirm:
        print("Not running. Re-run with --confirm to approve this spend.")
        return {}
    rng = random.Random(args.seed)
    totals = {"investigations": 0, "correct_runs": 0, "passes": 0}
    for n in range(1, args.max_passes + 1):
        order = days[:]
        rng.shuffle(order)
        t0 = time.perf_counter()
        for day in order:
            spent = Ledger().total() - start
            if mode == "bedrock" and spent + estimate_usd(s.model_fast, drivers[day]) > args.budget:
                print(f"Budget reached: ${spent:.2f} of ${args.budget:.2f} new spend after {totals['passes']} full "
                      f"pass(es).")
                return totals
            store, _, cases = investigate_all(day, mode)
            totals["investigations"] += len(cases)
            tally = owner_decisions(store, cases, rng)
            verdicts = ", ".join(f"{c.case_id}={c.report.verdict if c.report else 'needs_review'}" for c in cases)
            print(f"  pass {n} {day}: {verdicts} | {tally}", flush=True)
        totals["passes"] = n
        print(f"pass {n} done in {time.perf_counter() - t0:.0f}s · new spend ${Ledger().total() - start:.2f} · "
              f"investigations so far {totals['investigations']}", flush=True)
        if mode != "bedrock":
            break
    return totals


if __name__ == "__main__":
    main()
