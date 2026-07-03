"""
app.py
------
Continuous chat UI, Instagram-DM style: pick a relationship, keep chatting,
history persists across messages. When the Wingman notices something worth
remembering, it shows up as a small accept/discard chip under that message —
nothing is saved to memory.json until you approve it.

The sidebar's "Context Window" panel shows exactly the human-readable memory
block the model would see for whoever's selected, with a last-updated time
that refreshes on every rerun. Selecting "— none —" clears it.

Run (with backend.py already running on port 8000):
    streamlit run app.py

Falls back to calling wingman.py in-process if the backend isn't reachable.
"""

import os

import requests
import streamlit as st

from memory import MemoryManager
from wingman import get_wingman_response

BACKEND_URL = os.environ.get("WINGMAN_BACKEND_URL", "http://127.0.0.1:8000")
NONE_OPTION = "— none —"

st.set_page_config(page_title="Sparkeefy Wingman v0", page_icon="❤️")
st.title("❤️ Sparkeefy AI Wingman — v0 ❤️")

mm = MemoryManager()
relationships = mm.list_relationships() or ["Aisha"]


def backend_health() -> dict:
    try:
        r = requests.get(f"{BACKEND_URL}/health", timeout=2)
        if r.status_code == 200:
            return {"alive": True, **r.json()}
    except requests.exceptions.RequestException:
        pass
    return {"alive": False}


HEALTH = backend_health()
USE_BACKEND = HEALTH["alive"]


def call_chat(relationship: str, message: str) -> dict:
    if USE_BACKEND:
        r = requests.post(f"{BACKEND_URL}/chat", json={"relationship": relationship, "message": message}, timeout=40)
        if r.status_code != 200:
            raise RuntimeError(r.json())
        return r.json()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    result = get_wingman_response(relationship, message, api_key=api_key, mock=not api_key, memory_manager=mm)
    if not result["schema_valid"]:
        raise RuntimeError(result.get("error"))
    return {
        "output": result["output"],
        "response_time_sec": result["response_time_sec"],
        "mock_mode": not api_key,
        "cached": result.get("cached", False),
        "warning": result.get("warning"),
    }


def approve_candidate(relationship: str, category: str, value: str) -> tuple:
    """Returns (success: bool, error_message: str or None). Never silently
    swallows a failed persist — a false 'Saved' toast when nothing actually
    got written is worse than no toast at all."""
    if USE_BACKEND:
        try:
            r = requests.post(
                f"{BACKEND_URL}/memory/approve",
                json={"relationship": relationship, "category": category, "value": value},
                timeout=5,
            )
            if r.status_code == 200:
                return True, None
            return False, f"Backend rejected the save (HTTP {r.status_code}): {r.text[:200]}"
        except requests.exceptions.RequestException as e:
            return False, f"Could not reach backend to save: {e}"
    else:
        try:
            mm.approve_candidate(relationship, category, value)
            return True, None
        except Exception as e:
            return False, str(e)


# ---- Sidebar ----
with st.sidebar:
    st.subheader("Relationship")
    options = [NONE_OPTION] + relationships
    relationship_choice = st.selectbox("Who is this chat about?", options, key="relationship_select")
    relationship = None if relationship_choice == NONE_OPTION else relationship_choice
    st.caption("Each relationship has its own memory and its own chat thread.")
    st.divider()

    # ---- API status: key configured vs. actually reachable are different facts.
    # When USE_BACKEND is true, the backend process is the one actually making
    # calls — trust ITS env, not this Streamlit process's, which may simply
    # not have DEEPSEEK_API_KEY exported in whatever terminal launched it.
    if USE_BACKEND:
        api_key_set = bool(HEALTH.get("api_key_configured"))
    else:
        api_key_set = bool(os.environ.get("DEEPSEEK_API_KEY"))

    if api_key_set:
        st.success("DEEPSEEK_API_KEY detected")
    else:
        st.warning("No DEEPSEEK_API_KEY set — running in mock mode")

    if USE_BACKEND:
        st.caption("Backend: connected")
        last_call = HEALTH.get("last_api_call", {})
        if last_call.get("attempted"):
            if last_call.get("success"):
                st.caption("✅ Last live DeepSeek call succeeded")
            else:
                st.caption(f"⚠️ Last live call failed, falling back to mock: {last_call.get('last_error')}")
        elif api_key_set:
            st.caption("No live call made yet this session")

        cstats = HEALTH.get("cache", {})
        if cstats:
            st.caption(f"Cache: {cstats.get('live_entries', 0)} live entr{'y' if cstats.get('live_entries')==1 else 'ies'} (TTL {cstats.get('ttl_seconds', 0)}s)")

        pricing = HEALTH.get("pricing", {})
        if pricing.get("peak_pricing_active"):
            st.caption(f"💰 {pricing.get('note')} ({pricing.get('multiplier')}x)")
        elif pricing:
            st.caption(f"💰 {pricing.get('note')}")

        # Diagnostic for the most likely cause of "memory doesn't update":
        # backend and this Streamlit process resolving to two DIFFERENT
        # memory.json files (e.g. two separate copies/extractions of the
        # project directory running side by side).
        backend_mem_path = HEALTH.get("storage", {}).get("memory_path")
        local_mem_path = os.path.abspath(mm.path)
        if backend_mem_path and backend_mem_path != local_mem_path:
            st.error(
                "⚠️ Backend and this app are reading DIFFERENT memory.json files — "
                "approved memories won't show up here. Run both from the same project folder.\n\n"
                f"Backend: {backend_mem_path}\nHere: {local_mem_path}"
            )
    else:
        st.caption("Backend: not running — using in-process fallback")

    st.divider()

    # ---- Context Window: exactly what the model would see, human-readable, live ----
    st.subheader("🧠 Context Window")
    if relationship is None:
        st.caption("No relationship selected. Nothing loaded.")
    else:
        summary = mm.sidebar_summary(relationship)
        if summary["has_memory"]:
            st.text(summary["context_text"])
        else:
            st.caption(f"No stored memory yet for {relationship}.")
        st.caption(f"Last updated: {summary['last_updated_human']}")

    st.divider()

    if relationship and st.button("Clear this chat"):
        if USE_BACKEND:
            requests.post(f"{BACKEND_URL}/history/{relationship}/clear", timeout=5)
        else:
            mm.clear_history(relationship)
        st.session_state.pop(f"messages_{relationship}", None)
        st.rerun()


# ---- No relationship selected: stop here, nothing to chat about ----
if relationship is None:
    st.info("Pick a relationship from the sidebar to start chatting.")
    st.stop()

# ---- Load / init this relationship's chat thread ----
state_key = f"messages_{relationship}"
if state_key not in st.session_state:
    if USE_BACKEND:
        hist_resp = requests.get(f"{BACKEND_URL}/history/{relationship}", timeout=5).json()
        st.session_state[state_key] = hist_resp["history"]
    else:
        st.session_state[state_key] = mm.get_full_history(relationship)

messages = st.session_state[state_key]

# ---- Render chat history ----
for msg in messages:
    with st.chat_message("user" if msg["role"] == "user" else "assistant"):
        st.write(msg["content"])

# ---- New message input ----
user_input = st.chat_input(f"Message about {relationship}...")

if user_input:
    with st.chat_message("user"):
        st.write(user_input)
    messages.append({"role": "user", "content": user_input})

    with st.chat_message("assistant"):
        with st.spinner("thinking..."):
            try:
                result = call_chat(relationship, user_input)
            except Exception as e:
                st.error(f"Wingman error: {e}")
                result = None

        if result:
            out = result["output"]
            if result.get("mock_mode"):
                st.caption("⚠️ mock mode — set DEEPSEEK_API_KEY for real model output")
            if result.get("warning"):
                st.caption(f"⚠️ {result['warning']}")
            if result.get("cached"):
                st.caption("⚡ served from cache — no API call made")

            st.write(out["wingman_response"])
            messages.append({"role": "assistant", "content": out["wingman_response"]})

            if out.get("energy_read"):
                st.caption(f"Energy read: {out['energy_read']}")

            if out["suggested_messages"]:
                st.markdown("**Suggested messages:**")
                for m in out["suggested_messages"]:
                    st.code(m, language=None)

            if out["follow_up_question"]:
                st.info(out["follow_up_question"])

            if out.get("safety_flag"):
                st.warning("This one got flagged for safety review.")

            # Memory candidates — accept/discard, never auto-saved. Sidebar
            # context window picks up the change on the next rerun since it
            # reads fresh from memory.json every time.
            candidates = out.get("memory_candidates", [])
            if candidates:
                st.markdown("---")
                st.caption(f"Noticed something new about {relationship}:")
                for i, cand in enumerate(candidates):
                    c1, c2, c3 = st.columns([3, 1, 1])
                    c1.write(f"**{cand['category']}**: {cand['value']}")
                    if c2.button("✅ Remember", key=f"approve_{relationship}_{len(messages)}_{i}"):
                        success, error = approve_candidate(relationship, cand["category"], cand["value"])
                        if success:
                            st.toast(f"Saved: {cand['value']}")
                            st.rerun()
                        else:
                            st.error(f"Didn't save — {error}")
                    if c3.button("✖️ Discard", key=f"discard_{relationship}_{len(messages)}_{i}"):
                        st.toast("Discarded")
