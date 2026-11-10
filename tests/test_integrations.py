"""Settings → Integrations: connect to the Devin CLI already signed in on this machine. Fake CLIs only."""

import stat

import pytest
from streamlit.testing.v1 import AppTest

from detective import integrations, rbac
from detective.config import ROOT
from detective.fixers import DevinFixer

SIGNED_IN = """
case "$1" in
  --version) echo "devin 3000.12.43";;
  auth) echo "Logged in (via Devin)."; echo "  Name:              tester"; echo "  Tier:              Devin Enterprise";;
esac
"""
SIGNED_OUT = """
case "$1" in
  --version) echo "devin 3000.12.43";;
  auth) echo "Not logged in."; echo "Run devin auth login to authenticate.";;
esac
"""


def cli(tmp_path, script, name="devin"):
    p = tmp_path / name
    p.write_text("#!/bin/bash\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_status_of_signed_in_signed_out_and_missing_cli(tmp_path):
    ok = integrations.cli_status(cli(tmp_path, SIGNED_IN))
    assert ok["logged_in"] and ok["user"] == "tester" and ok["tier"] == "Devin Enterprise" and ok["version"]
    out = integrations.cli_status(cli(tmp_path, SIGNED_OUT, "devin2"))
    assert out["found"] and not out["logged_in"] and out["detail"] == "found, not signed in"
    assert not integrations.cli_status(None)["found"]


def test_connect_saves_metadata_only_and_writer_options(tmp_path):
    assert integrations.writer_options() == (["template"], "template")
    status = integrations.connect_devin("finops-team", cli(tmp_path, SIGNED_IN))
    assert status["logged_in"]
    conn = integrations.devin_connection()
    assert conn["user"] == "tester" and conn["connected_by"] == "finops-team" and conn["default_writer"] == "template"
    saved = (integrations._path()).read_text()
    assert "token" not in saved.lower() and "secret" not in saved.lower()
    assert integrations.writer_options() == (["template", "devin"], "template"), "Devin offered; default stays Template"
    integrations.set_default_writer("finops-team", "devin")
    assert integrations.writer_options()[1] == "devin"
    integrations.disconnect_devin("finops-team")
    assert integrations.devin_connection() is None and integrations.writer_options() == (["template"], "template")


def test_signed_out_cli_does_not_connect(tmp_path):
    status = integrations.connect_devin("finops-team", cli(tmp_path, SIGNED_OUT))
    assert not status["logged_in"] and integrations.devin_connection() is None


def test_only_finops_can_change_integrations(tmp_path):
    with pytest.raises(rbac.PermissionDenied):
        integrations.connect_devin("ravi.m", cli(tmp_path, SIGNED_IN))
    with pytest.raises(rbac.PermissionDenied):
        integrations.disconnect_devin("orders-team")


def test_devin_fixer_uses_the_connected_cli(tmp_path):
    path = cli(tmp_path, SIGNED_IN)
    integrations.connect_devin("finops-team", path)
    assert DevinFixer(remote_url=str(tmp_path)).cli() == path


def test_integrations_tab_renders_both_states(tmp_path):
    page = str(ROOT / "app" / "views" / "results.py")
    at = AppTest.from_file(page, default_timeout=60)
    at.session_state["settings_tab"] = "Integrations"
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    assert "Connect to Devin CLI" in [b.label for b in at.button]
    integrations.connect_devin("finops-team", cli(tmp_path, SIGNED_IN))
    at = AppTest.from_file(page, default_timeout=60)
    at.session_state["settings_tab"] = "Integrations"
    at.run()
    labels = [b.label for b in at.button]
    assert "Disconnect" in labels and "Check again" in labels
    assert any("tester" in m.value for m in at.markdown)
    assert any("Devin is successfully connected as tester" in s.value for s in at.success)
