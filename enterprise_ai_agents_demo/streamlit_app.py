from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_ai_agents_demo.oci_enterprise_agent_demo import (
    DEFAULT_MEMORY_FILE,
    DEFAULT_LOG_FILE,
    DemoConfig,
    JsonMemoryStore,
    LOGGER,
    answer_question,
    build_agent_trace,
    classify_chat_route,
    format_answer,
    stream_basic_answer_question,
    setup_logging,
)


st.set_page_config(page_title="OCI Enterprise AI Agent", layout="wide")

st.title("OCI Enterprise AI Agent")
st.caption(
    "Lightweight Streamlit demo with OCI Responses, OCI Conversations memory, "
    "Function Calling, and Code Interpreter."
)

with st.sidebar:
    st.header("OCI")
    region = st.text_input("Region", value=os.getenv("OCI_GENAI_REGION", "us-chicago-1"))
    project = st.text_input("Project OCID", value=os.getenv("OCI_GENAI_PROJECT_OCID", ""))
    model = st.text_input("Model", value=os.getenv("OCI_GENAI_MODEL", "openai.gpt-oss-120b"))
    auth = st.selectbox(
        "Auth",
        ["instance_principal", "session", "user_principal", "resource_principal", "api_key"],
        index=["instance_principal", "session", "user_principal", "resource_principal", "api_key"].index(
            os.getenv("OCI_GENAI_AUTH", "instance_principal")
        )
        if os.getenv("OCI_GENAI_AUTH", "instance_principal")
        in ["instance_principal", "session", "user_principal", "resource_principal", "api_key"]
        else 0,
    )
    profile = st.text_input("OCI CLI profile", value=os.getenv("OCI_CLI_PROFILE", "DEFAULT"))

    st.header("Memory")
    session_id = st.text_input("Session id", value=st.session_state.get("session_id", "demo"))
    memory_file = st.text_input("Memory file", value=os.getenv("OCI_AGENT_MEMORY_FILE", str(DEFAULT_MEMORY_FILE)))
    log_file = st.text_input("Log file", value=os.getenv("OCI_AGENT_LOG_FILE", str(DEFAULT_LOG_FILE)))
    stream_basic = st.toggle("Stream basic responses", value=False)

    store = JsonMemoryStore(memory_file)
    session = store.get_session(session_id)
    conversations = store.list_conversations(session_id)
    active = store.get_active_conversation(session_id)
    option_labels = {
        conv["local_id"]: (
            f"{conv.get('title') or 'Untitled'}"
            f" · {conv.get('conversation_id') or 'new'}"
        )
        for conv in conversations
    }
    selected_local_id = st.selectbox(
        "Conversation",
        options=[conv["local_id"] for conv in conversations],
        index=[conv["local_id"] for conv in conversations].index(active["local_id"]),
        format_func=lambda local_id: option_labels.get(local_id, local_id),
    )
    if selected_local_id != active["local_id"]:
        store.set_active_conversation(session_id, selected_local_id)
        st.rerun()

    active = store.get_active_conversation(session_id)
    memory_subject_id = st.text_input(
        "OCI memory subject id",
        value=active.get("memory_subject_id", session_id),
    )
    st.caption(
        "Local JSON stores UI state and OCI ids. Short-term memory uses the OCI "
        "conversation id. Long-term memory metadata is opt-in with "
        "OCI_AGENT_ENABLE_LONG_TERM_MEMORY=true."
    )
    col_a, col_b = st.columns(2)
    if col_a.button("Add conv", use_container_width=True):
        store.add_conversation(session_id, title="New conversation")
        st.session_state["session_id"] = session_id
        st.rerun()
    if col_b.button("Add conv same subject", use_container_width=True):
        store.set_session_metadata(session_id, memory_subject_id=memory_subject_id)
        store.start_new_conversation(session_id)
        st.session_state["session_id"] = session_id
        st.rerun()
    col_c, col_d = st.columns(2)
    if col_c.button("Delete Conv", use_container_width=True):
        store.delete_active_conversation(session_id)
        st.session_state["session_id"] = session_id
        st.rerun()
    if col_d.button("Clear this session", use_container_width=True):
        store.clear(session_id)
        st.session_state["session_id"] = session_id
        st.rerun()

config = DemoConfig(
    region=region,
    project=project,
    model=model,
    auth=auth,
    profile=profile,
    api_key=os.getenv("OCI_GENAI_API_KEY"),
    dry_run=False,
)
setup_logging(log_file)

store.set_session_metadata(session_id, memory_subject_id=memory_subject_id)
session = store.get_session(session_id)
active = store.get_active_conversation(session_id)
st.session_state["session_id"] = session_id

memory_cols = st.columns(3)
memory_cols[0].metric("OCI conversation id", active.get("conversation_id") or "new")
memory_cols[1].metric("OCI memory subject", active.get("memory_subject_id", session_id))
memory_cols[2].metric(
    "Memory metadata",
    "long-term on"
    if os.getenv("OCI_AGENT_ENABLE_LONG_TERM_MEMORY", "false").lower() == "true"
    else "short-term only",
)

st.info(
    "Memory demo: ask a few follow-ups to show short-term memory in the same OCI "
    "conversation. Enable OCI_AGENT_ENABLE_LONG_TERM_MEMORY=true only after the "
    "basic conversation diagnostic passes."
)

def render_agent_trace(trace: dict | None) -> None:
    if not trace:
        return
    with st.expander("Agent trace"):
        st.json(trace)


for message in active.get("messages", []):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_agent_trace(message.get("trace"))

prompt = st.chat_input("Ask a question, request contacts, or ask for a calculation...")
if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)

    try:
        route = classify_chat_route(prompt)
        with st.chat_message("assistant"):
            if stream_basic and route == "basic_chat":
                try:
                    stream = stream_basic_answer_question(
                        prompt,
                        config,
                        session_id=session_id,
                        memory_file=memory_file,
                        memory_subject_id=memory_subject_id,
                    )
                    st.write_stream(stream)
                    result = stream.result
                except Exception:
                    LOGGER.exception(
                        "streaming failed; falling back to non-streaming session_id=%s",
                        session_id,
                    )
                    with st.spinner("Streaming failed; retrying without streaming..."):
                        result = answer_question(
                            prompt,
                            config,
                            session_id=session_id,
                            memory_file=memory_file,
                            memory_subject_id=memory_subject_id,
                        )
                    st.markdown(format_answer(result["turn"]["answer"]))
            else:
                with st.spinner("Calling the enterprise agent..."):
                    result = answer_question(
                        prompt,
                        config,
                        session_id=session_id,
                        memory_file=memory_file,
                        memory_subject_id=memory_subject_id,
                    )
                st.markdown(format_answer(result["turn"]["answer"]))

            if result:
                render_agent_trace(result.get("trace") or build_agent_trace(result))
    except Exception as exc:
        LOGGER.exception("streamlit chat turn failed session_id=%s", session_id)
        st.error(f"{type(exc).__name__}: {exc}")
        st.caption(f"Details logged to `{log_file}`")
        st.caption(
            "For OCI 500/429 errors, the app retries automatically. If the error "
            "persists, check the selected model, project OCID, region, and whether "
            "the feature used by this turn is supported in that region."
        )
