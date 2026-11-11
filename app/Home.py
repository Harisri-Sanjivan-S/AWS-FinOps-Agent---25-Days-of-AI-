"""Entry point: actor switcher at the top of the sidebar, then the pages that actor's role allows."""

import ui  # noqa: I001 - sets sys.path

import streamlit as st

st.set_page_config(page_title="Agentic FinOps with AWS Bedrock and Devin", layout="wide", initial_sidebar_state="collapsed")
ui.actor_switcher()
ui.navigation()
