"""Fix flow against a local bare git repo (no GitHub, no network): brief, executors, code checks, branch push."""

import dataclasses
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from detective.config import ROOT, get_settings
from detective.fixers import DevinFixer, FixError, TemplateFixer
from detective.fixers.base import CodeFixer, verify_changes
from detective.fixers.brief import BriefError, build_brief, render_devin_prompt
from detective.pipeline import approve, investigate_all

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]


@pytest.fixture
def remote(tmp_path) -> Path:
    bare = tmp_path / "acme-platform.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    shutil.copytree(ROOT / "demo_repo" / "acme-platform", seed)
    for cmd in (["git", "init", "-b", "main"], ["git", "add", "-A"], [*GIT, "commit", "-m", "seed"],
                ["git", "remote", "add", "origin", str(bare)], ["git", "push", "origin", "main"]):
        subprocess.run(cmd, cwd=seed, check=True, capture_output=True)
    return bare


@pytest.fixture
def nat_case():
    store, _, cases = investigate_all("tuesday", "offline")
    return store, next(c for c in cases if c.driver.kind == "network")


class FakePR:
    def __init__(self):
        self.calls = []

    def __call__(self, repo, branch, title, body_file):
        self.calls.append((repo, branch, title, Path(body_file).read_text()))
        return f"https://github.com/{repo}/pull/1"


def branch_files(bare: Path, branch: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", "main", branch], cwd=bare, capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_brief_is_built_from_validated_facts_only(nat_case):
    import yaml
    _, case = nat_case
    catalog = yaml.safe_load((ROOT / "demo_repo" / "acme-platform" / "catalog.yaml").read_text())
    brief = build_brief(case, catalog, "Harisri-Sanjivan-S/acme-platform")
    assert brief.target_folder == "services/orders-service/infra" and brief.owner_team == "orders-team"
    assert brief.branch == "finops/tue-03-s3-gateway-endpoint" and brief.avoidable_per_day == 130.0
    text = render_devin_prompt(brief)
    assert "<untrusted_data>" not in text and "SEC-311" not in text, "no raw ticket/deploy-note text in the brief"
    assert "TUE-03" in text and "PR_SUMMARY.md" in text and "terraform apply" in text
    gpus = next(c for c in investigate_all("tuesday", "offline")[2] if c.driver.kind == "compute")
    with pytest.raises(BriefError):
        build_brief(gpus, catalog, "x/y")


def test_template_fix_opens_pr_with_only_the_target_file(nat_case, remote):
    store, case = nat_case
    pr = FakePR()
    case = approve(store, case, reviewer="orders-team", fixer=TemplateFixer(remote_url=str(remote), pr_creator=pr))
    [action] = case.actions
    assert action.kind == "pull_request" and action.url.endswith("/pull/1") and case.status == "fix_in_progress"
    assert branch_files(remote, "finops/tue-03-s3-gateway-endpoint") == ["services/orders-service/infra/s3_gateway_endpoint.tf"]
    assert '+resource "aws_vpc_endpoint" "s3"' in action.detail["diff"], "the diff is kept as proof of the change"
    repo, branch, title, body = pr.calls[0]
    assert repo == get_settings().demo_repo and branch.startswith("finops/") and "[TUE-03]" in title
    assert "Evidence:" in body and "ct-006" in body and "A human must review and merge" in body


class StrayFixer(CodeFixer):
    name = "stray"

    def apply(self, work, brief, brief_path):
        (work / "platform" / "network" / "main.tf").write_text("# changed\n")
        (work / brief.target_folder / "notes.txt").write_text("x")
        return "summary", ""


def test_changes_outside_the_target_folder_are_rejected_and_not_pushed(nat_case, remote):
    store, case = nat_case
    pr = FakePR()
    case = approve(store, case, reviewer="orders-team", fixer=StrayFixer(remote_url=str(remote), pr_creator=pr))
    [action] = case.actions
    assert action.status == "failed" and "outside the target folder" in action.detail["error"]
    assert "not a Terraform file" in action.detail["error"] and case.status == "awaiting_owner" and not pr.calls
    refs = subprocess.run(["git", "branch", "--list"], check=True, cwd=remote, capture_output=True, text=True).stdout
    assert "finops/" not in refs


def test_refuses_any_repo_but_the_demo_repo(nat_case):
    _, case = nat_case
    with pytest.raises(FixError):
        TemplateFixer(remote_url="https://github.com/someone/else.git").run(case)


def test_verify_changes_rules():
    assert verify_changes([], "a/infra") == ["No files were changed."]
    assert verify_changes(["a/infra/x.tf"], "a/infra") == []
    assert len(verify_changes(["a/infra/x.py", "b/y.tf"], "a/infra")) == 2


def fake_devin(tmp_path: Path, script: str) -> str:
    path = tmp_path / "devin"
    path.write_text("#!/bin/bash\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_devin_fixer_runs_cli_headless_and_uses_its_summary(nat_case, remote, tmp_path):
    store, case = nat_case
    script = """
[ "$1" = "-p" ] && [ "$2" = "--prompt-file" ] && [ "$4" = "--permission-mode" ] && [ "$5" = "accept-edits" ] || exit 9
grep -q "TUE-03" "$3" || exit 8
cat > services/orders-service/infra/s3_endpoint.tf <<'EOF'
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.private_route_table_ids
}
EOF
printf '## What changed\\nS3 gateway endpoint for TUE-03.\\n' > PR_SUMMARY.md
"""
    settings = dataclasses.replace(get_settings(), devin_bin=fake_devin(tmp_path, script))
    pr = FakePR()
    case = approve(store, case, reviewer="orders-team", fixer=DevinFixer(settings=settings, remote_url=str(remote), pr_creator=pr))
    [action] = case.actions
    assert action.url and action.detail["executor"] == "devin", action.detail
    assert branch_files(remote, action.action_id) == ["services/orders-service/infra/s3_endpoint.tf"]
    assert "S3 gateway endpoint for TUE-03" in pr.calls[0][3] and "Devin (CLI, headless)" in pr.calls[0][3]


FAKE_TF_OK = """
case "$1" in fmt|init|validate) echo "$1 ok"; exit 0;; esac; exit 2
"""
FAKE_TF_BAD_VALIDATE = """
case "$1" in fmt|init) exit 0;; validate) echo 'Error: Reference to undeclared input variable "nope"' >&2; exit 1;; esac
"""


def tf_settings(tmp_path, script):
    return dataclasses.replace(get_settings(), tf_validate=True, terraform_bin=fake_devin(tmp_path, script), ci_wait_s=5)


def test_terraform_gate_and_ci_are_recorded_as_proof(nat_case, remote, tmp_path):
    store, case = nat_case
    states = iter([{"ci": "pending"}, {"ci": "passed"}])
    fixer = TemplateFixer(settings=tf_settings(tmp_path, FAKE_TF_OK), remote_url=str(remote), pr_creator=FakePR(),
                          ci_checker=lambda url: next(states), poll_s=0)
    case = approve(store, case, reviewer="orders-team", fixer=fixer)
    [action] = case.actions
    assert action.detail["tf_checks"]["fmt"]["ok"] and action.detail["tf_checks"]["validate"]["ok"]
    assert action.detail["ci"] == "passed" and action.status == "open, CI passed, awaiting review"


def test_terraform_validate_failure_blocks_the_push(nat_case, remote, tmp_path):
    store, case = nat_case
    pr = FakePR()
    fixer = TemplateFixer(settings=tf_settings(tmp_path, FAKE_TF_BAD_VALIDATE), remote_url=str(remote), pr_creator=pr)
    case = approve(store, case, reviewer="orders-team", fixer=fixer)
    [action] = case.actions
    assert action.status == "failed" and "Terraform validate failed, so nothing was pushed" in action.detail["error"]
    assert not pr.calls and case.status == "awaiting_owner"
    refs = subprocess.run(["git", "branch", "--list"], check=True, cwd=remote, capture_output=True, text=True).stdout
    assert "finops/" not in refs


def test_ci_failure_and_refresh(nat_case, remote, tmp_path):
    from detective.pipeline import refresh_ci
    store, case = nat_case
    fixer = TemplateFixer(settings=tf_settings(tmp_path, FAKE_TF_OK), remote_url=str(remote), pr_creator=FakePR(),
                          ci_checker=lambda url: {"ci": "failed"}, poll_s=0)
    case = approve(store, case, reviewer="orders-team", fixer=fixer)
    assert case.actions[0].status == "open, CI failed: needs attention"
    case = refresh_ci(store, case, checker=lambda url: {"ci": "passed", "merged": False})
    assert case.actions[0].detail["ci"] == "passed" and store.load_case(case.case_id).actions[0].status.startswith("open, CI passed")


def test_devin_failure_is_reported_not_pushed(nat_case, remote, tmp_path):
    store, case = nat_case
    settings = dataclasses.replace(get_settings(), devin_bin=fake_devin(tmp_path, "echo 'auth error' >&2; exit 3"))
    case = approve(store, case, reviewer="orders-team", fixer=DevinFixer(settings=settings, remote_url=str(remote), pr_creator=FakePR()))
    assert case.actions[0].status == "failed" and "exited with 3" in case.actions[0].detail["error"]
