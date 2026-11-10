"""Quiet status text for the code-fix checks: terraform fmt / validate (before push) and CI (after the PR opens)."""

from __future__ import annotations


def check_badges(tf_checks: dict, ci: str) -> str:
    def item(label: str, state: str) -> str:
        color = {"pass": "#3C7A57", "fail": "#B4413C", "pending": "#B7791F"}.get(state, "#6B7280")
        mark = {"pass": "✓", "fail": "✗", "pending": "…", "skipped": "skipped"}.get(state, "–")
        return f"<span style='color:{color}'>{label} {mark}</span>"

    if "skipped" in tf_checks:
        fmt = val = "skipped"
    else:
        fmt = "pass" if tf_checks.get("fmt", {}).get("ok") else ("fail" if "fmt" in tf_checks else "n/a")
        val = "pass" if tf_checks.get("validate", {}).get("ok") else (
            "fail" if "validate" in tf_checks or tf_checks.get("init", {}).get("ok") is False else "n/a")
    ci_state = {"passed": "pass", "failed": "fail", "pending": "pending"}.get(ci, "n/a")
    parts = [item("terraform fmt", fmt), item("terraform validate", val), item("CI", ci_state)]
    return "<span style='font-size:.88rem'>" + " &nbsp;·&nbsp; ".join(parts) + "</span>"
