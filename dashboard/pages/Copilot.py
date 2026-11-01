"""The Copilot chat page — the operator's face for the AI layer.

The copilot existed only as an API endpoint before this page; the
website sells it, this page LETS YOU USE IT. Same rules as everywhere
else in the platform: the chat requires sign-in (any role — the copilot
gate is authentication, not role), every answer is scoped to the
signer's tenant by RLS on the API side, and the response metadata
(provider, tool calls, degraded reason) is shown, not hidden — the
operator should always know which rung served and why.

Streamlit multipage: this file is auto-discovered as a page beside the
KPI dashboard. Run (compose): http://localhost:8501 → Copilot.
"""

import os

import httpx
import streamlit as st

API_BASE = os.environ.get("API_BASE_URL", "http://api:8000")

st.set_page_config(page_title="Ops Copilot", page_icon="🤖", layout="wide")
st.title("🤖 Ops Copilot")
st.caption(
    "Ask about your operation in plain language — the agent calls the governed "
    "API with your credentials, so every answer is scoped to your tenant."
)

# ── sign-in (the demo trio is documented: ops/ops, admin/admin) ─────────
with st.sidebar:
    st.header("Copilot sign-in")
    if "token" not in st.session_state:
        username = st.text_input("Username", value="ops")
        password = st.text_input("Password", value="ops", type="password")
        if st.button("Sign in", type="primary"):
            try:
                response = httpx.post(
                    f"{API_BASE}/auth/token",
                    data={"username": username, "password": password},
                    timeout=10,
                )
            except httpx.HTTPError as exc:
                st.error(f"the API is unreachable ({exc.__class__.__name__})")
            else:
                if response.status_code == 200:
                    st.session_state.token = response.json()["access_token"]
                    st.rerun()
                else:
                    st.error("sign-in failed — check the credentials")
    else:
        st.success("signed in")
        if st.button("Sign out"):
            del st.session_state["token"]
            st.session_state.pop("messages", None)
            st.rerun()

# ── the chat ─────────────────────────────────────────────────────────────
if "token" not in st.session_state:
    st.info("Sign in from the sidebar to start chatting.")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

if question := st.chat_input("Ask the copilot… (e.g. how did we do today?)"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner("the agent is working…"):
            try:
                response = httpx.post(
                    f"{API_BASE}/copilot/chat",
                    headers={"Authorization": f"Bearer {st.session_state['token']}"},
                    json={"question": question},
                    timeout=120,
                )
            except httpx.HTTPError as exc:
                st.error(f"the API is unreachable ({exc.__class__.__name__})")
            else:
                if response.status_code == 200:
                    data = response.json()
                    st.write(data["answer"])
                    meta = (
                        f"provider `{data['provider']}` · tool calls "
                        f"{data['tool_calls_used']} · "
                        + ("⚠️ degraded" if data["degraded"] else "served")
                    )
                    if data.get("degraded_reason"):
                        meta += f" · {data['degraded_reason']}"
                    st.caption(meta)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": data["answer"]}
                    )
                else:
                    detail = (
                        response.json().get("detail", response.text)
                        if response.headers.get("content-type", "").startswith("application/json")
                        else response.text
                    )
                    st.error(f"{response.status_code}: {str(detail)[:200]}")
