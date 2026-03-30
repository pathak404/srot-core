import streamlit as st
import requests

API_BASE = "http://localhost:8000"

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


meeting_id = st.query_params.get("id") or st.session_state.get("_view_meeting_id")

if not meeting_id:
    st.warning("No meeting selected.")
    st.stop()

meeting_id = int(meeting_id)
st.query_params["id"] = meeting_id

if st.session_state.get("_loaded_mid") != meeting_id:
    try:
        resp = requests.get(f"{API_BASE}/meeting/{meeting_id}", timeout=30)
        resp.raise_for_status()
        data = resp.json()
        st.session_state["_loaded_mid"] = meeting_id
        st.session_state["m_title"] = data.get("title") or f"Meeting #{meeting_id}"
        st.session_state["m_transcript"] = data["transcript"]
        st.session_state["m_tasks"] = data["tasks"]
    except requests.exceptions.RequestException as e:
        st.error(f"Could not load meeting: {e}")
        st.stop()

st.title(f"M{meeting_id}: {st.session_state.get('m_title', f'Meeting #{meeting_id}')}")

# Transcript
st.header("Transcript")
edited_transcript = st.text_area(
    "Edit transcript:",
    value=st.session_state["m_transcript"],
    height=400,
    key="m_transcript_editor",
)

if st.button("Save & Extract Tasks"):
    with st.spinner("Saving transcript and extracting tasks..."):
        try:
            resp = requests.put(
                f"{API_BASE}/meeting/{meeting_id}/transcript",
                json={"content": edited_transcript},
                timeout=300,
            )
            resp.raise_for_status()
            result = resp.json()
            st.session_state["m_transcript"] = edited_transcript
            st.session_state["m_tasks"] = result["tasks"]
            st.success("Transcript saved! Tasks extracted.")
        except requests.exceptions.RequestException as e:
            st.error(f"Error: {e}")

# Callbacks
def _save_task_cb(idx):
    task = st.session_state["m_tasks"][idx]
    title = st.session_state.get(f"mt_{idx}", task.get("title", ""))
    desc = st.session_state.get(f"md_{idx}", task.get("description", ""))
    assignee = st.session_state.get(f"ma_{idx}", task.get("assignee", ""))
    try:
        resp = requests.put(
            f"{API_BASE}/task/{task['id']}",
            json={"title": title, "description": desc, "assignee": assignee or None},
            timeout=30,
        )
        resp.raise_for_status()
        st.session_state["m_tasks"][idx].update({"title": title, "description": desc, "assignee": assignee})
        st.session_state[f"mmsg_{idx}"] = ("success", "Task updated!")
    except requests.exceptions.RequestException as e:
        st.session_state[f"mmsg_{idx}"] = ("error", f"Error: {e}")


def _jira_cb(idx):
    task = st.session_state["m_tasks"][idx]
    title = st.session_state.get(f"mt_{idx}", task.get("title", ""))
    desc = st.session_state.get(f"md_{idx}", task.get("description", ""))
    assignee = st.session_state.get(f"ma_{idx}", task.get("assignee", ""))
    try:
        resp = requests.post(
            f"{API_BASE}/create-jira",
            json={"task_id": task["id"], "title": title, "description": desc, "assignee": assignee or None},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        st.session_state["m_tasks"][idx]["jira_ticket_id"] = result["ticket_id"]
        st.session_state[f"mmsg_{idx}"] = ("success", f"Created: {result['ticket_id']} - {result['url']}")
    except requests.exceptions.RequestException as e:
        st.session_state[f"mmsg_{idx}"] = ("error", f"Error: {e}")


def _dismiss_cb(idx):
    task = st.session_state["m_tasks"][idx]
    try:
        resp = requests.delete(f"{API_BASE}/task/{task['id']}", timeout=30)
        resp.raise_for_status()
        st.session_state["m_tasks"][idx]["dismissed"] = True
        st.session_state[f"mmsg_{idx}"] = ("success", "Task dismissed")
    except requests.exceptions.RequestException as e:
        st.session_state[f"mmsg_{idx}"] = ("error", f"Error: {e}")


# Tasks
tasks = st.session_state.get("m_tasks", [])

if tasks:
    st.divider()
    st.header("Extracted Tasks")

    for i, task in enumerate(tasks):
        module = task.get("module", "")
        dismissed = task.get("dismissed", False)

        label = f"T{task.get('task_seq', i + 1)}: {task.get('title', 'Untitled')}"
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
                    st.text_input("Title", value=task.get("title", ""), key=f"mt_{i}")
                    st.text_area("Description", value=task.get("description", ""), key=f"md_{i}", height=150)
                    st.text_input("Assignee", value=task.get("assignee", "") or "", key=f"ma_{i}")

                with col2:
                    st.write("")
                    st.write("")

                    st.button("Save Changes", key=f"ms_{i}", on_click=_save_task_cb, args=(i,))

                    if jira_enabled:
                        jira_id = task.get("jira_ticket_id")
                        if jira_id:
                            st.info(f"Jira: {jira_id}")
                        else:
                            st.button("Create Jira Ticket", key=f"mj_{i}", on_click=_jira_cb, args=(i,))

                    st.button("Dismiss", key=f"mx_{i}", on_click=_dismiss_cb, args=(i,))

            msg_key = f"mmsg_{i}"
            if msg_key in st.session_state:
                msg_type, msg_text = st.session_state[msg_key]
                if msg_type == "success":
                    st.success(msg_text)
                else:
                    st.error(msg_text)
                del st.session_state[msg_key]
