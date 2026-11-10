"""Investigate the demo week (7-14 Nov 2026) on Amazon Bedrock, then optionally play the owners' decisions.

    uv run python scripts/run_week.py                       # prints the estimate only
    uv run python scripts/run_week.py --confirm             # investigate every day on Bedrock
    uv run python scripts/run_week.py --confirm --owner-actions          # + owners approve stops, next-day bill checks
    uv run python scripts/run_week.py --confirm --owner-actions --open-prs   # + real PRs on DEMO_REPO (template fixer)

Cost guard: the estimate is printed first; nothing runs without --confirm; the ledger refuses calls at the cap."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detective.config import get_settings
from detective.detect import detect
from detective.fixers import make_fixer
from detective.ledger import Ledger, estimate_usd
from detective.pipeline import approve, investigate_all, next_day
from detective.sources import FixtureSource

WEEK = [f"nov{d:02d}" for d in range(6, 15)]


def play_owner_actions(store, cases, open_prs: bool, log=print) -> None:
    """What the owners would click: approve proposed stops, check the next day's bill; optionally approve code fixes."""
    for case in cases:
        r = case.report
        if not r or case.status != "awaiting_owner" or not r.proposed_fix:
            continue
        if r.proposed_fix == "stop_instances":
            case = next_day(store, approve(store, case, reviewer=case.assigned_to))
            log(f"   {case.case_id}: {case.assigned_to} approved the stop; next day: {case.next_day.note}")
        elif open_prs:
            case = approve(store, case, reviewer=case.assigned_to, fixer=make_fixer("template"))
            a = case.actions[-1]
            log(f"   {case.case_id}: {case.assigned_to} approved the code fix: {a.url or a.detail.get('error')}")


def main(argv: list[str] | None = None, mode: str = "bedrock") -> list:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", nargs="*", default=WEEK, help="e.g. nov08 nov10")
    p.add_argument("--confirm", action="store_true", help="approve the printed Bedrock spend")
    p.add_argument("--owner-actions", action="store_true", help="approve proposed stops and run next-day checks")
    p.add_argument("--open-prs", action="store_true", help="also approve code fixes (opens real PRs on DEMO_REPO)")
    args = p.parse_args(argv)
    s = get_settings()
    drivers = sum(len(detect(FixtureSource(d)).drivers) for d in args.days)
    est = estimate_usd(s.model_fast, drivers)
    ledger = Ledger()
    print(f"{len(args.days)} day(s), {drivers} cost drivers on {s.model_fast}: estimate about ${est:.2f} "
          f"(ledger so far ${ledger.total():.4f}, guard ${s.cost_cap_usd:.0f}).")
    if mode == "bedrock":
        if not args.confirm:
            print("Not running. Re-run with --confirm to approve this spend.")
            return []
        ledger.check(upcoming_usd=est)
    out = []
    for day in args.days:
        store, det, cases = investigate_all(day, mode)
        print(f"{day}: +${det.increase:,.0f} ({det.increase_pct:.0f}%), " + (
            ", ".join(f"{c.case_id}={c.report.verdict if c.report else 'needs_review'} ({c.usage.seconds:.0f}s, "
                      f"${c.usage.cost_usd:.3f})" for c in cases) or "below the materiality gate, no alert"))
        if args.owner_actions:
            play_owner_actions(store, cases, args.open_prs)
        out.append((store, cases))
    print(f"Ledger now ${Ledger().total():.4f}")
    return out


if __name__ == "__main__":
    main()
