"""Entry point: actor switcher at the top of the sidebar, then the pages that actor's role allows."""

import ui  # noqa: I001 - sets sys.path

import streamlit as st

st.set_page_config(page_title="Agentic FinOps on AWS", page_icon=":material/query_stats:", layout="wide")
ui.actor_switcher()
ui.navigation()
