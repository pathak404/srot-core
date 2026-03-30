import streamlit as st
import requests
from datetime import datetime

API_BASE = "http://localhost:8000"

st.title("Meetings")
st.write("")

try:
    resp = requests.get(f"{API_BASE}/meetings", timeout=10)
    resp.raise_for_status()
    meetings = resp.json()
except requests.exceptions.RequestException as e:
    st.error(f"Could not load meetings: {e}")
    meetings = []

if not meetings:
    st.info("No meetings yet. Upload one from the Upload page.")
else:
    for m in meetings:
        title = m.get("title") or m.get("filename", "Untitled")
        filename = m.get("filename", "")
        task_count = m.get("task_count", 0)
        raw = m.get("created_at", "")
        try:
            dt = datetime.fromisoformat(str(raw))
            created = dt.strftime("%d %b %Y %I:%M %p")
        except (ValueError, TypeError):
            created = str(raw)[:16]

        with st.container():
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"### M{m['id']}: {title}")
                st.caption(f"{filename}  &middot;  {created}  &middot;  {task_count} task{'s' if task_count != 1 else ''}")
            with col2:
                st.write("")
                if st.button("View", key=f"view_{m['id']}", use_container_width=True):
                    st.session_state["_view_meeting_id"] = m["id"]
                    st.switch_page(st.session_state["_meeting_detail_page"])
        st.divider()
