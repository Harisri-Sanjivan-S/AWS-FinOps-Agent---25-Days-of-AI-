"""Settings (FinOps): eval results, models and limits, access, cost ledger, reset."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import json

import streamlit as st

from detective import integrations, rbac
from detective.config import ROOT, get_settings
from detective.ledger import Ledger

me = ui.require_page("results")
s = get_settings()
tab = st.segmented_control("Section", ["Results", "Models", "Integrations", "Access", "Cost", "Reset"],
                           default="Results", key="settings_tab", label_visibility="collapsed") or "Results"
ui.header("Settings", "Settings", tab)
with st.container(border=True, key="fill_main_settings"):

    if tab == "Results":
        results = sorted((ROOT / "evals" / "results").glob("*.json"), reverse=True)
        if not results:
            st.markdown("No measured results yet.")
        else:
            choice = st.selectbox("Result file", results, format_func=lambda p: p.name)
            data = json.loads(choice.read_text())
            ui.kv([(k, str(v)) for k, v in data["summary"]["metrics"].items()])
            rows = data.get("cases") or data.get("baseline") or []
            if rows:
                st.dataframe(rows, hide_index=True, width="stretch")
            ui.muted(f"{data['mode']} · {ui.model_label(data['model'])} · {data['runs']} run(s) · {data['created']}")

    elif tab == "Models":
        conn = integrations.devin_connection()
        ui.kv([("Region", s.aws_region), ("Model", ui.model_label(s.model_fast)),
               ("Escalation model", ui.model_label(s.model_strong) + " (only when code rejects an answer)"),
               ("Agent limits", f"{s.tool_call_cap} tool calls · {s.turns} turns · {s.lookback_hours} h look-back · temperature 0"),
               ("Code-fix writer", "Template" + (" · Devin available (see Integrations)" if conn else "")),
               ("Demo repository", s.demo_repo)])

    elif tab == "Integrations":
        conn = integrations.devin_connection()
        st.subheader("Devin")
        ui.muted("Writes code fixes for approved cases, using the Devin CLI already signed in on this machine. "
                 "Only the connection details are saved here; the login stays in the Devin CLI.")
        if conn:
            st.success(f"Devin is successfully connected as {conn['user']}."
                       + (f" ({conn['tier']})" if conn.get("tier") else ""))
            ui.kv([("Status", "<span class='dot' style='background:#3C7A57'></span>Connected"),
                   ("Signed in as", conn["user"] + (f" · {conn['tier']}" if conn.get("tier") else "")),
                   ("Devin CLI", conn["version"] or "Detected"),
                   ("Connected", f"{conn['connected_at'][:16].replace('T', ' ')} UTC by {ui.actor_name(conn['connected_by'])}")])
            writer = st.radio("Default writer for code fixes", list(integrations.WRITERS), horizontal=True,
                              index=list(integrations.WRITERS).index(conn.get("default_writer", "template")),
                              format_func={"template": "Template", "devin": "Devin"}.get, key="default_writer")
            if writer != conn.get("default_writer", "template"):
                integrations.set_default_writer(me.actor_id, writer)
                st.rerun()
            ui.muted("Owners can choose Template or Devin for each fix in the inbox.")
            c1, c2, _ = st.columns([1, 1, 3])
            if c1.button("Check again"):
                status = integrations.connect_devin(me.actor_id, conn["path"])
                if not status["logged_in"]:
                    st.warning("The Devin CLI is no longer signed in. Run `devin auth login` in your Terminal.")
                else:
                    st.rerun()
            if c2.button("Disconnect"):
                integrations.disconnect_devin(me.actor_id)
                st.rerun()
        else:
            found = integrations.find_devin_cli()
            status = integrations.cli_status(found)
            ui.kv([("Status", "<span class='dot' style='background:#6B7280'></span>Not connected"),
                   ("Devin CLI", "Detected" if status["found"] else "Not found"), ("Login", status["detail"])])
            if st.button("Connect to Devin CLI", type="primary"):
                result = integrations.connect_devin(me.actor_id)
                if result["logged_in"]:
                    st.rerun()
                elif not result["found"]:
                    st.warning("Devin CLI not found. Install Devin Desktop or the Devin CLI, then connect again.")
                else:
                    st.warning("The Devin CLI is not signed in. Run `devin auth login` in your Terminal, then connect again. "
                               "The app must run under the same user that signed in.")

    elif tab == "Access":
        st.dataframe([{"Actor": a.name, "Role": a.role, "Pages": ", ".join(ui.PAGES[p][1] for p in a.pages),
                       "Sees": "All cases" if a.sees_all_cases else "Own cases",
                       "Can act on": "Own cases; re-route any" if a.can_reroute_any else "Own cases"}
                      for a in rbac.ACTORS.values()], hide_index=True, width="stretch")

    elif tab == "Cost":
        ledger = Ledger()
        status = ledger.status()
        ui.kv([("Model spend so far", f"${status['total_usd']:.2f}"), ("Spending guard", f"${status['cap_usd']:.0f}"),
               ("Project ceiling", "$30")])
        entries = ledger.entries()
        if entries:
            with st.expander("Ledger"):
                st.dataframe(entries[::-1], hide_index=True, width="stretch")

    else:
        ui.muted("Deletes local runs and cases. The cost ledger is kept.")
        if st.checkbox("I want to delete all local runs") and st.button("Reset local runs"):
            from scripts.reset_demo import reset_local
            removed = reset_local(yes=True)
            st.success(f"Removed {len(removed)} run(s).")
