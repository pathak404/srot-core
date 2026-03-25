import streamlit as st
import requests

API_BASE = "http://localhost:8000"

st.set_page_config(page_title="AI Meeting Assistant", layout="wide")
st.title("AI Meeting Assistant")

# Fetch config  
@st.cache_data(ttl=60)
def _get_config():
    try:
        resp = requests.get(f"{API_BASE}/config", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"jira_enabled": False}

config = _get_config()
jira_enabled = config.get("jira_enabled", False)

# Upload Section
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
            st.session_state.pop("tasks", None)
            st.success(f"Meeting processed! Review the transcript below and click Save to extract tasks.")
        except requests.exceptions.RequestException as e:
            st.error(f"Error processing meeting: {e}")

# Load existing meeting
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

# Transcript Section
if "transcript" in st.session_state:
    st.divider()
    st.header("Transcript")
    edited_transcript = st.text_area(
        "Edit transcript below:",
        value=st.session_state["transcript"],
        height=400,
        key="transcript_editor",
    )
    if st.button("Save & Extract Tasks"):
        with st.spinner("Saving transcript and extracting tasks..."):
            try:
                resp = requests.put(
                    f"{API_BASE}/meeting/{st.session_state['meeting_id']}/transcript",
                    json={"content": edited_transcript},
                    timeout=300,
                )
                resp.raise_for_status()
                result = resp.json()
                st.session_state["transcript"] = edited_transcript
                st.session_state["tasks"] = result["tasks"]
                st.success("Transcript saved! Tasks extracted.")
            except requests.exceptions.RequestException as e:
                st.error(f"Error saving transcript: {e}")



def _save_task_callback(idx):
    task = st.session_state["tasks"][idx]
    title = st.session_state.get(f"title_{idx}", task.get("title", ""))
    desc = st.session_state.get(f"desc_{idx}", task.get("description", ""))
    assignee = st.session_state.get(f"assignee_{idx}", task.get("assignee", ""))
    try:
        resp = requests.put(
            f"{API_BASE}/task/{task['id']}",
            json={"title": title, "description": desc, "assignee": assignee or None},
            timeout=30,
        )
        resp.raise_for_status()
        st.session_state["tasks"][idx]["title"] = title
        st.session_state["tasks"][idx]["description"] = desc
        st.session_state["tasks"][idx]["assignee"] = assignee
        st.session_state[f"msg_{idx}"] = ("success", "Task updated!")
    except requests.exceptions.RequestException as e:
        st.session_state[f"msg_{idx}"] = ("error", f"Error: {e}")


def _create_jira_callback(idx):
    task = st.session_state["tasks"][idx]
    title = st.session_state.get(f"title_{idx}", task.get("title", ""))
    desc = st.session_state.get(f"desc_{idx}", task.get("description", ""))
    assignee = st.session_state.get(f"assignee_{idx}", task.get("assignee", ""))
    try:
        resp = requests.post(
            f"{API_BASE}/create-jira",
            json={"task_id": task["id"], "title": title, "description": desc, "assignee": assignee or None},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        st.session_state["tasks"][idx]["jira_ticket_id"] = result["ticket_id"]
        st.session_state[f"msg_{idx}"] = ("success", f"Created: {result['ticket_id']} - {result['url']}")
    except requests.exceptions.RequestException as e:
        st.session_state[f"msg_{idx}"] = ("error", f"Error creating Jira ticket: {e}")


def _dismiss_task_callback(idx):
    task = st.session_state["tasks"][idx]
    try:
        resp = requests.delete(f"{API_BASE}/task/{task['id']}", timeout=30)
        resp.raise_for_status()
        st.session_state["tasks"][idx]["dismissed"] = True
        st.session_state[f"msg_{idx}"] = ("success", "Task dismissed")
    except requests.exceptions.RequestException as e:
        st.session_state[f"msg_{idx}"] = ("error", f"Error: {e}")


# Tasks Section
if "tasks" in st.session_state and st.session_state["tasks"]:
    st.divider()
    st.header("Extracted Tasks")

    for i, task in enumerate(st.session_state["tasks"]):
        module = task.get("module", "")
        dismissed = task.get("dismissed", False)

        label = task.get("title", "Untitled")
        if module:
            label = f"{label} [{module}]"
        if dismissed:
            label = f"{label} — Dismissed"

        with st.expander(label, expanded=not dismissed):
            if dismissed:
                st.markdown(
                    f"<div style='color: #888; background: #f5f5f5; padding: 12px; border-radius: 8px; border-left: 4px solid #ccc;'>"
                    f"<div style='font-size: 15px; font-weight: 600; margin-bottom: 6px;'>{task.get('title', '')}</div>"
                    f"<div style='font-size: 13px; white-space: pre-wrap; margin-bottom: 6px;'>{task.get('description', '')}</div>"
                    f"<div style='font-size: 12px;'>Assignee: {task.get('assignee') or 'Unassigned'}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                col1, col2 = st.columns([3, 1])

                with col1:
                    st.text_input("Title", value=task.get("title", ""), key=f"title_{i}")
                    st.text_area("Description", value=task.get("description", ""), key=f"desc_{i}", height=150)
                    st.text_input("Assignee", value=task.get("assignee", "") or "", key=f"assignee_{i}")

                with col2:
                    st.write("")
                    st.write("")

                    st.button("Save Changes", key=f"save_{i}", on_click=_save_task_callback, args=(i,))

                    if jira_enabled:
                        jira_id = task.get("jira_ticket_id")
                        if jira_id:
                            st.info(f"Jira: {jira_id}")
                        else:
                            st.button("Create Jira Ticket", key=f"jira_{i}", on_click=_create_jira_callback, args=(i,))

                    st.button("Dismiss", key=f"dismiss_{i}", on_click=_dismiss_task_callback, args=(i,))

            msg_key = f"msg_{i}"
            if msg_key in st.session_state:
                msg_type, msg_text = st.session_state[msg_key]
                if msg_type == "success":
                    st.success(msg_text)
                else:
                    st.error(msg_text)
                del st.session_state[msg_key]
