"""Devin writes the fix: the Devin CLI runs headless in the local clone with the fix brief as its prompt.

Uses the developer's existing `devin auth login` (no API key). Permission mode `accept-edits` lets Devin edit files
in the clone only; code then verifies the change before anything is committed or pushed.
Production path: the Devin API with a service-user key, or a ticket assigned to Devin."""

from __future__ import annotations

import subprocess
from pathlib import Path

from detective.fixers.base import CodeFixer, FixError, run
from detective.models import FixBrief


def devin_status(devin_bin: str) -> dict:
    if not Path(devin_bin).exists():
        return {"available": False, "logged_in": False, "detail": f"Devin CLI not found at {devin_bin}"}
    try:
        out = run([devin_bin, "auth", "status"], timeout=30)
    except FixError as exc:
        return {"available": True, "logged_in": False, "detail": str(exc)}
    logged_in = "not logged in" not in out.lower()
    user = next((line.split(":", 1)[1].strip() for line in out.splitlines() if line.strip().startswith("Name:")), "")
    return {"available": True, "logged_in": logged_in, "detail": f"logged in as {user}" if logged_in else "not logged in"}


class DevinFixer(CodeFixer):
    name = "devin"

    def apply(self, work: Path, brief: FixBrief, brief_path: Path) -> tuple[str, str]:
        s = self.settings
        if not Path(s.devin_bin).exists():
            raise FixError(f"Devin CLI not found at {s.devin_bin}")
        cmd = [s.devin_bin, "-p", "--prompt-file", str(brief_path), "--permission-mode", "accept-edits",
               "--respect-workspace-trust", "false"]
        if s.devin_model:
            cmd += ["--model", s.devin_model]
        try:
            proc = subprocess.run(cmd, check=False, cwd=work, capture_output=True, text=True, timeout=s.devin_timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise FixError(f"Devin did not finish within {s.devin_timeout_s} s") from exc
        log = (proc.stdout + proc.stderr)[-2000:]
        if proc.returncode != 0:
            raise FixError(f"Devin CLI exited with {proc.returncode}: {log[-400:]}")
        summary_file = work / "PR_SUMMARY.md"
        if not summary_file.exists():
            raise FixError("Devin finished without writing PR_SUMMARY.md")
        summary = summary_file.read_text()
        summary_file.unlink()
        if brief.case_id not in summary:
            summary = f"Case {brief.case_id}\n\n{summary}"
        return summary, log

    def label(self) -> str:
        return "Devin (CLI, headless)"
