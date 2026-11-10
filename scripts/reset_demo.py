"""Reset the demo to a clean state before a rehearsal or recording.

    uv run python scripts/reset_demo.py              # dry run: shows what would be removed
    uv run python scripts/reset_demo.py --yes        # delete local runs (keeps the cost ledger)
    uv run python scripts/reset_demo.py --yes --github   # also close open finops/* PRs in DEMO_REPO and delete their branches

Never touches AWS. On GitHub it only acts on DEMO_REPO and only on branches named finops/*."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detective.cases import list_runs
from detective.config import get_settings


def local_runs() -> list[Path]:
    return [r.dir for r in list_runs()]


def reset_local(yes: bool) -> list[Path]:
    runs = local_runs()
    for path in runs:
        print(("removing " if yes else "would remove ") + str(path))
        if yes:
            shutil.rmtree(path)
    return runs


def open_demo_prs(repo: str) -> list[dict]:
    out = subprocess.run(["gh", "pr", "list", "--repo", repo, "--state", "open", "--json", "number,headRefName,url"],
                         check=True, capture_output=True, text=True).stdout
    return [p for p in json.loads(out) if p["headRefName"].startswith("finops/")]


def reset_github(yes: bool) -> None:
    repo = get_settings().demo_repo
    for pr in open_demo_prs(repo):
        print(("closing " if yes else "would close ") + f"{pr['url']} and delete {pr['headRefName']}")
        if yes:
            subprocess.run(["gh", "pr", "close", str(pr["number"]), "--repo", repo, "--delete-branch",
                            "--comment", "Closed by the demo reset script."], check=True)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--yes", action="store_true", help="actually delete / close (default is a dry run)")
    p.add_argument("--github", action="store_true", help="also close open finops/* PRs in DEMO_REPO")
    args = p.parse_args(argv)
    reset_local(args.yes)
    if args.github:
        reset_github(args.yes)
    if not args.yes:
        print("Dry run only. Add --yes to apply.")


if __name__ == "__main__":
    main()
