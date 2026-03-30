import streamlit as st
import requests

API_BASE = "http://localhost:8000"

st.title("Upload Meeting")

meeting_title = st.text_input("Meeting title", placeholder="e.g. Sprint 12 Planning, Weekly Sync")
audio_file = st.file_uploader("Choose an audio file", type=["mp3", "wav", "m4a", "ogg", "flac", "webm"])
context_text = st.text_area("Optional context (agenda, speaker names, notes)", height=100)

if st.button("Process Meeting", disabled=audio_file is None):
    with st.spinner("Transcribing meeting... This may take a few minutes."):
        files = {"audio": (audio_file.name, audio_file.getvalue(), audio_file.type)}
        data = {"title": meeting_title, "context": context_text}
        try:
            resp = requests.post(f"{API_BASE}/process-meeting", files=files, data=data, timeout=600)
            resp.raise_for_status()
            result = resp.json()
            st.success(f"Meeting processed! ID: {result['meeting_id']}")
            st.info("Go to **Meetings** in the sidebar to review the transcript.")
        except requests.exceptions.RequestException as e:
            st.error(f"Error processing meeting: {e}")

# --- Live Meeting ---
st.divider()
st.subheader("Or: Start a Live Meeting")
live_title = st.text_input("Live meeting title", placeholder="e.g. Daily Standup", key="live_title")

if st.button("Start Live Recording"):
    title = live_title.strip() or "Live Meeting"
    try:
        resp = requests.post(f"{API_BASE}/start-live-meeting", json={"title": title}, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        mid = result["meeting_id"]
        ui_port = st.context.headers.get("host", "localhost:8501").split(":")[-1]
        live_url = f"{API_BASE}/live?meeting_id={mid}&ui_port={ui_port}"
        st.success(f"Live meeting created! ID: {mid}")
        st.markdown(f"### [Open Live Transcription]({live_url})")
        st.caption("Opens in a new tab. Come back here after the meeting to review.")
    except requests.exceptions.RequestException as e:
        st.error(f"Error: {e}")
