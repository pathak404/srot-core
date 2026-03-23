import streamlit as st
import requests

API_BASE = "http://localhost:8000"

st.set_page_config(page_title="AI Meeting Assistant", layout="wide")
st.title("AI Meeting Assistant")

# --- Upload Section ---
st.header("Upload Meeting Audio")

audio_file = st.file_uploader("Choose an audio file", type=["mp3", "wav", "m4a", "ogg", "flac", "webm"])
context_text = st.text_area("Optional context (agenda, notes, etc.)", height=100)

if st.button("Process Meeting", disabled=audio_file is None):
    with st.spinner("Processing meeting... This may take a few minutes for long recordings."):
        files = {"audio": (audio_file.name, audio_file.getvalue(), audio_file.type)}
        data = {"context": context_text}
        try:
            resp = requests.post(f"{API_BASE}/process-meeting", files=files, data=data, timeout=600)
            resp.raise_for_status()
            result = resp.json()
            st.session_state["meeting_id"] = result["meeting_id"]
            st.session_state["transcript"] = result["transcript"]
            st.session_state["tasks"] = result["tasks"]
            st.success(f"Meeting processed! Meeting ID: {result['meeting_id']}")
        except requests.exceptions.RequestException as e:
            st.error(f"Error processing meeting: {e}")

# --- Load existing meeting ---
st.divider()
col_load1, col_load2 = st.columns([1, 3])
with col_load1:
    load_id = st.number_input("Load Meeting ID", min_value=1, step=1, value=1)
with col_load2:
    st.write("")
    st.write("")
    if st.button("Load Meeting"):
        try:
            resp = requests.get(f"{API_BASE}/meeting/{load_id}", timeout=30)
            resp.raise_for_status()
            result = resp.json()
            st.session_state["meeting_id"] = result["meeting_id"]
            st.session_state["transcript"] = result["transcript"]
            st.session_state["tasks"] = result["tasks"]
            st.success(f"Loaded meeting {load_id}")
        except requests.exceptions.RequestException as e:
            st.error(f"Error loading meeting: {e}")

# --- Transcript Section ---
if "transcript" in st.session_state:
    st.divider()
    st.header("Transcript")
    edited_transcript = st.text_area(
        "Edit transcript below:",
        value=st.session_state["transcript"],
        height=400,
        key="transcript_editor",
    )
    if st.button("Save Transcript Changes"):
        try:
            resp = requests.put(
                f"{API_BASE}/meeting/{st.session_state['meeting_id']}/transcript",
                json={"content": edited_transcript},
                timeout=30,
            )
            resp.raise_for_status()
            st.session_state["transcript"] = edited_transcript
            st.success("Transcript saved!")
        except requests.exceptions.RequestException as e:
            st.error(f"Error saving transcript: {e}")

# --- Tasks Section ---
if "tasks" in st.session_state:
    st.divider()
    st.header("Extracted Tasks")

    for i, task in enumerate(st.session_state["tasks"]):
        with st.expander(f"Task {i + 1}: {task.get('title', 'Untitled')}", expanded=True):
            col1, col2 = st.columns([3, 1])

            with col1:
                new_title = st.text_input("Title", value=task.get("title", ""), key=f"title_{i}")
                new_desc = st.text_area("Description", value=task.get("description", ""), key=f"desc_{i}", height=100)
                new_assignee = st.text_input("Assignee", value=task.get("assignee", "") or "", key=f"assignee_{i}")

            with col2:
                st.write("")
                st.write("")

                # Save task changes
                if st.button("Save Changes", key=f"save_{i}"):
                    try:
                        resp = requests.put(
                            f"{API_BASE}/task/{task['id']}",
                            json={
                                "title": new_title,
                                "description": new_desc,
                                "assignee": new_assignee or None,
                            },
                            timeout=30,
                        )
                        resp.raise_for_status()
                        st.session_state["tasks"][i]["title"] = new_title
                        st.session_state["tasks"][i]["description"] = new_desc
                        st.session_state["tasks"][i]["assignee"] = new_assignee
                        st.success("Task updated!")
                    except requests.exceptions.RequestException as e:
                        st.error(f"Error: {e}")

                # Create Jira ticket
                jira_id = task.get("jira_ticket_id")
                if jira_id:
                    st.info(f"Jira: {jira_id}")
                else:
                    if st.button("Create Jira Ticket", key=f"jira_{i}"):
                        try:
                            resp = requests.post(
                                f"{API_BASE}/create-jira",
                                json={
                                    "task_id": task["id"],
                                    "title": new_title,
                                    "description": new_desc,
                                    "assignee": new_assignee or None,
                                },
                                timeout=30,
                            )
                            resp.raise_for_status()
                            result = resp.json()
                            st.session_state["tasks"][i]["jira_ticket_id"] = result["ticket_id"]
                            st.success(f"Created: {result['ticket_id']} - {result['url']}")
                        except requests.exceptions.RequestException as e:
                            st.error(f"Error creating Jira ticket: {e}")
