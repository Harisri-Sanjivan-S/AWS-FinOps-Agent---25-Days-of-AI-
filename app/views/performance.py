"""AI performance (now a tab in Settings); kept as a direct page for links and tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import perf_view

ui.require_page("results")
ui.header("AI performance", "Settings", "AI performance")
perf_view.render()
