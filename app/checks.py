"""Badges for the code-fix checks: terraform fmt / validate (before push) and CI (after the PR opens)."""

from __future__ import annotations


def check_badges(tf_checks: dict, ci: str) -> str:
    def b(label: str, state: str) -> str:
        color = {"pass": "#2e7d32", "fail": "#c62828", "pending": "#ef6c00"}.get(state, "#757575")
        mark = {"pass": "✓", "fail": "✗", "pending": "…"}.get(state, "–")
        return (f"<span style='border:1px solid {color};color:{color};padding:1px 8px;border-radius:10px;"
                f"font-size:0.8rem;margin-right:6px'>{label} {mark}</span>")

    if "skipped" in tf_checks:
        fmt = val = "skipped"
    else:
        fmt = "pass" if tf_checks.get("fmt", {}).get("ok") else ("fail" if "fmt" in tf_checks else "n/a")
        val = "pass" if tf_checks.get("validate", {}).get("ok") else (
            "fail" if "validate" in tf_checks or tf_checks.get("init", {}).get("ok") is False else "n/a")
    ci_state = {"passed": "pass", "failed": "fail", "pending": "pending"}.get(ci, "n/a")
    return b("terraform fmt", fmt) + b("terraform validate", val) + b("CI", ci_state)
