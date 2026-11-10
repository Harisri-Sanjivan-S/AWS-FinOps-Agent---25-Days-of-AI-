"""Shared flow for every code-fix executor:
clone the demo repo -> build the brief -> branch -> executor edits -> code verifies -> commit, push, open the PR.

Safety: only the configured DEMO_REPO; never push to main; never merge; changed files must stay inside the target
folder and be Terraform files."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from detective.config import Settings, get_settings
from detective.fixers.brief import build_brief
from detective.models import Case, FixBrief

BOT_NAME = "Agentic FinOps (demo)"
BOT_EMAIL = "agentic-finops-demo@users.noreply.github.com"


class FixError(RuntimeError):
    pass


class FixResult(BaseModel):
    executor: str
    status: str
    branch: str
    target_folder: str = ""
    pr_url: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    diff: str = ""
    summary: str = ""
    error: str | None = None
    brief_path: str = ""
    log_tail: str = ""


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    proc = subprocess.run(cmd, check=False, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise FixError(f"{' '.join(cmd[:3])} failed: {(proc.stderr or proc.stdout).strip()[-500:]}")
    return proc.stdout.strip()


def gh_create_pr(repo: str, branch: str, title: str, body_file: Path) -> str:
    return run(["gh", "pr", "create", "--repo", repo, "--base", "main", "--head", branch, "--title", title,
                "--body-file", str(body_file)]).splitlines()[-1]


def gh_pr_status(pr_url: str) -> dict:
    out = run(["gh", "pr", "view", pr_url, "--json", "state,url,statusCheckRollup,mergedAt"])
    data = json.loads(out)
    checks = data.get("statusCheckRollup") or []
    states = {c.get("conclusion") or c.get("status") for c in checks}
    ci = "pending" if not checks or {"IN_PROGRESS", "QUEUED", "PENDING", None} & states else \
        ("passed" if states <= {"SUCCESS", "SKIPPED", "NEUTRAL"} else "failed")
    return {"state": data["state"], "ci": ci, "merged": bool(data.get("mergedAt"))}


def changed_files(work: Path) -> list[str]:
    out = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=work)
    return sorted(line[3:].strip().strip('"') for line in out.splitlines() if line.strip())


def verify_changes(files: list[str], target_folder: str) -> list[str]:
    errors = []
    if not files:
        errors.append("No files were changed.")
    for f in files:
        if not f.startswith(target_folder.rstrip("/") + "/"):
            errors.append(f"{f} is outside the target folder {target_folder}/")
        elif not f.endswith(".tf"):
            errors.append(f"{f} is not a Terraform file")
    return errors


def pr_body(brief: FixBrief, summary: str, executor: str) -> str:
    evidence = sorted({e for c in brief.findings for e in c.evidence_ids})
    return f"""{summary.strip()}

---
**Agentic FinOps case {brief.case_id}** · verdict: {brief.verdict} · confidence {brief.confidence} of 4
Increase: ${brief.daily_increase:,.2f}/day · expected avoidable after merge and apply: ${brief.avoidable_per_day:,.2f}/day
(the saving is only claimed once the next bill confirms it).
Evidence: {", ".join(evidence)}
Change written by: {executor}. Approved by the owning team ({brief.owner_team}) before this PR was opened.
A human must review and merge. Nothing has been applied to AWS.
"""


class CodeFixer:
    name = "base"

    def __init__(self, settings: Settings | None = None, remote_url: str | None = None,
                 pr_creator: Callable[[str, str, str, Path], str] = gh_create_pr):
        self.settings = settings or get_settings()
        self.repo = self.settings.demo_repo
        self.remote_url = remote_url or f"https://github.com/{self.repo}.git"
        self.pr_creator = pr_creator

    def apply(self, work: Path, brief: FixBrief, brief_path: Path) -> tuple[str, str]:
        """Make the edit in `work`; return (summary markdown, log tail)."""
        raise NotImplementedError

    def run(self, case: Case, on_event: Callable[[str, object], None] = lambda k, p: None) -> FixResult:
        points_at_demo_repo = self.remote_url in (f"https://github.com/{self.repo}.git", f"https://github.com/{self.repo}")
        local_test_remote = Path(self.remote_url).is_dir()
        if self.repo.count("/") != 1 or not (points_at_demo_repo or local_test_remote):
            raise FixError(f"Refusing to push anywhere except the demo repo {self.repo}.")
        stamp = datetime.now().strftime("%H%M%S")
        base = self.settings.runs_dir / case.run_id / "work"
        work = base / f"{case.case_id}-{stamp}"
        base.mkdir(parents=True, exist_ok=True)
        result = FixResult(executor=self.name, status="started", branch="")
        try:
            on_event("fix", (case.case_id, f"Cloning {self.repo}"))
            run(["git", "clone", "--depth", "1", self.remote_url, str(work)], timeout=180)
            catalog = yaml.safe_load((work / "catalog.yaml").read_text())
            brief = build_brief(case, catalog, self.repo)
            result.branch, result.target_folder = brief.branch, brief.target_folder
            brief_path = base / f"{case.case_id}-{stamp}-brief.md"
            brief_path.write_text(self.render_brief(brief))
            (base / f"{case.case_id}-{stamp}-brief.json").write_text(brief.model_dump_json(indent=2))
            result.brief_path = str(brief_path)
            run(["git", "checkout", "-b", brief.branch], cwd=work)

            on_event("fix", (case.case_id, f"{self.name}: writing the change in {brief.target_folder}/"))
            summary, result.log_tail = self.apply(work, brief, brief_path)
            result.changed_files = changed_files(work)
            errors = verify_changes(result.changed_files, brief.target_folder)
            if errors:
                raise FixError("Change rejected by code checks: " + "; ".join(errors))
            result.summary = summary

            on_event("fix", (case.case_id, "Checks passed; committing and pushing the branch"))
            run(["git", "add", "--", *result.changed_files], cwd=work)
            result.diff = run(["git", "diff", "--cached", "--no-color"], cwd=work)[:12_000]
            run(["git", "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "commit", "-m",
                 brief.pr_title], cwd=work)
            run(["git", "push", "origin", brief.branch], cwd=work, timeout=180)
            body_path = base / f"{case.case_id}-{stamp}-pr-body.md"
            body_path.write_text(pr_body(brief, summary, self.label()))
            on_event("fix", (case.case_id, "Opening the pull request"))
            result.pr_url = self.pr_creator(self.repo, brief.branch, brief.pr_title, body_path)
            result.status = "open, awaiting review"
        except Exception as exc:  # noqa: BLE001 - every failure is reported on the case; nothing is pushed half-way
            result.status, result.error = "failed", str(exc)
        on_event("fix", (case.case_id, result.status))
        return result

    def render_brief(self, brief: FixBrief) -> str:
        from detective.fixers.brief import render_devin_prompt
        return render_devin_prompt(brief)

    def label(self) -> str:
        return self.name
