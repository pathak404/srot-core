import os
import streamlit as st
import requests
from datetime import datetime

API_BASE = os.getenv("BACKEND_URL", "http://localhost:8000")
PAGE_SIZE = 20

st.title("Meetings")
st.write("")

if "_meetings_page" not in st.session_state:
    st.session_state["_meetings_page"] = 1

page = st.session_state["_meetings_page"]

try:
    resp = requests.get(
        f"{API_BASE}/meetings",
        params={"page": page, "per_page": PAGE_SIZE},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
except requests.exceptions.RequestException as e:
    st.error(f"Could not load meetings: {e}")
    st.stop()

meetings = data.get("items", [])
total = data.get("total", 0)
total_pages = data.get("total_pages", 1)

if page > total_pages:
    st.session_state["_meetings_page"] = total_pages
    st.rerun()

if not meetings and total == 0:
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

    if total_pages > 1:
        col_prev, col_info, col_next = st.columns([1, 3, 1])
        with col_prev:
            if st.button("← Prev", disabled=(page <= 1), use_container_width=True):
                st.session_state["_meetings_page"] -= 1
                st.rerun()
        with col_info:
            st.markdown(
                f"<div style='text-align:center; padding-top:6px; color:#666; font-size:14px;'>"
                f"Page {page} of {total_pages} &nbsp;·&nbsp; {total} meetings"
                f"</div>",
                unsafe_allow_html=True,
            )
        with col_next:
            if st.button("Next →", disabled=(page >= total_pages), use_container_width=True):
                st.session_state["_meetings_page"] += 1
                st.rerun()
