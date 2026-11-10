"""Live investigation: one panel per driver with the agent's tool calls as they happen."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: I001 - sets sys.path for the detective package

import streamlit as st

from detective.pipeline import investigate_driver, make_agents, start_run
from detective.models import Case, ToolCall

ui.require_page("investigation")
st.title("Investigation")


def render_call(call: ToolCall) -> str:
    args = ", ".join(f"{k}={v if not isinstance(v, list) or len(v) < 4 else f'[{len(v)} ids]'}"
                     for k, v in call.inputs.items() if v is not None)
    ids = ", ".join(call.evidence_ids[:5]) + (f" +{len(call.evidence_ids) - 5}" if len(call.evidence_ids) > 5 else "")
    mark = ":green[OK]" if call.ok else ":red[error]"
    return f"`{call.seq:>2}` **{call.name}**({args}) → {mark} {ids or '—'}  \n<small>{call.summary}</small>"


def case_footer(case: Case) -> None:
    if case.report:
        r = case.report
        st.markdown(f"{ui.badge(r.verdict)} &nbsp; confidence **{r.confidence} of 4** · "
                    f"{ui.STATUS_LABELS[case.status]} · route: **{case.assigned_to or 'nobody (quiet)'}**",
                    unsafe_allow_html=True)
    else:
        st.error("Needs human review: " + "; ".join(case.validation_errors[:3]))
    u = case.usage
    st.caption(f"{len(case.tool_calls)} tool calls · {', '.join(ui.model_label(m) for m in u.models)} · "
               f"{u.input_tokens:,} in / {u.output_tokens:,} out tokens · ${u.cost_usd:.4f} · {u.seconds:.1f} s")


pending = st.session_state.pop("pending_run", None)
if pending:
    store, source, det = start_run(pending["scenario"], pending["mode"])
    st.session_state["run_dir"] = str(store.dir)
    st.caption(f"Run {store.run_id}")
    agents = make_agents(pending["mode"])
    for i, driver in enumerate(det.drivers, 1):
        status = st.status(f"{driver.title} · {ui.money(driver.daily_increase)}/day", expanded=True)
        trace = status.container()

        def on_event(kind, payload, status=status, trace=trace, driver=driver):
            if kind == "tool":
                trace.markdown(render_call(payload[1]), unsafe_allow_html=True)
            elif kind == "step":
                status.update(label=f"{driver.title} · {ui.money(driver.daily_increase)}/day · {payload[1]}")

        case = investigate_driver(store, source, driver, i, agents, on_event)
        with status:
            case_footer(case)
        status.update(state="complete" if case.report else "error", expanded=False)
    st.success("Investigation finished. Open the case reports or the owner inbox.")
    c1, c2 = st.columns(2)
    if c1.button("Case reports", icon=":material/description:"):
        st.switch_page("views/case.py")
    if c2.button("Inbox", icon=":material/inbox:"):
        st.switch_page("views/inbox.py")
else:
    store = ui.current_store()
    if not store:
        st.info("No run yet. Start one from the console.")
        st.stop()
    st.caption(f"Run {store.run_id}")
    for case in store.cases():
        with st.status(f"{case.case_id} · {case.driver.title} · {ui.money(case.driver.daily_increase)}/day",
                       state="complete" if case.report else "error", expanded=False):
            for call in case.tool_calls:
                st.markdown(render_call(call), unsafe_allow_html=True)
            case_footer(case)
