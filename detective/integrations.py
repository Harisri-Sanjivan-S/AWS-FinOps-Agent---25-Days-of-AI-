"""Integrations (Settings → Integrations). Today: Devin, through the Devin CLI already signed in on this machine.

Only metadata is saved (CLI path, user, plan, time, default writer) in runs/integrations.json; no tokens or secrets.
The login itself stays in the Devin CLI. Future: GitHub and AWS."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from detective import rbac
from detective.config import Settings, get_settings
from detective.models import now_iso

DESKTOP_CLI = "/Applications/Devin.app/Contents/Resources/app/extensions/windsurf/devin/bin/devin"
WRITERS = ("template", "devin")


def _path(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).runs_dir / "integrations.json"


def load(settings: Settings | None = None) -> dict:
    p = _path(settings)
    return json.loads(p.read_text()) if p.exists() else {}


def _save(state: dict, settings: Settings | None = None) -> None:
    p = _path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n")


def find_devin_cli(settings: Settings | None = None, override: str | None = None) -> str | None:
    """First existing CLI among: an explicit path, PATH, the DEVIN_BIN setting, the Devin Desktop app."""
    s = settings or get_settings()
    for candidate in (override, shutil.which("devin"), s.devin_bin, DESKTOP_CLI):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _field(out: str, name: str) -> str:
    return next((line.split(":", 1)[1].strip() for line in out.splitlines() if line.strip().startswith(f"{name}:")), "")


def cli_status(cli: str | None) -> dict:
    """Read-only check of the Devin CLI: found? version? signed in as whom? No Devin usage."""
    if not cli:
        return {"found": False, "logged_in": False, "path": None, "detail": "Devin CLI not found on this machine"}

    def call(*args: str) -> tuple[int, str]:
        try:
            proc = subprocess.run([cli, *args], check=False, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, str(exc)
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    _, version = call("--version")
    code, out = call("auth", "status")
    logged_in = code == 0 and "not logged in" not in out.lower()
    user, tier = _field(out, "Name"), _field(out, "Tier") or _field(out, "Plan")
    detail = f"signed in as {user}" + (f" ({tier})" if tier else "") if logged_in else "found, not signed in"
    return {"found": True, "logged_in": logged_in, "path": cli, "version": version.splitlines()[0] if version else "",
            "user": user, "tier": tier, "detail": detail}


def _require_admin(actor_id: str) -> None:
    if not rbac.get_actor(actor_id).can_change_settings:
        raise rbac.PermissionDenied(f"{actor_id} may not change integrations.")


def connect_devin(actor_id: str, override: str | None = None, settings: Settings | None = None) -> dict:
    """Connect to the Devin CLI that is already signed in. Saves metadata only."""
    _require_admin(actor_id)
    status = cli_status(find_devin_cli(settings, override))
    if not status["logged_in"]:
        return status
    state = load(settings)
    state["devin"] = {"connected": True, "path": status["path"], "user": status["user"], "tier": status["tier"],
                      "version": status["version"], "connected_by": actor_id, "connected_at": now_iso(),
                      "default_writer": state.get("devin", {}).get("default_writer", "template")}
    _save(state, settings)
    return status


def disconnect_devin(actor_id: str, settings: Settings | None = None) -> None:
    _require_admin(actor_id)
    state = load(settings)
    state.pop("devin", None)
    _save(state, settings)


def set_default_writer(actor_id: str, writer: str, settings: Settings | None = None) -> None:
    _require_admin(actor_id)
    if writer not in WRITERS:
        raise ValueError(f"Unknown writer {writer!r}")
    state = load(settings)
    if "devin" in state:
        state["devin"]["default_writer"] = writer
        _save(state, settings)


def devin_connection(settings: Settings | None = None) -> dict | None:
    conn = load(settings).get("devin")
    return conn if conn and conn.get("connected") else None


def writer_options(settings: Settings | None = None) -> tuple[list[str], str]:
    """Writers offered in the inbox, and the default. Devin only when connected; default stays Template."""
    conn = devin_connection(settings)
    if not conn:
        return ["template"], "template"
    return ["template", "devin"], conn.get("default_writer", "template")
