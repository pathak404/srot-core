from storage.models import get_connection


def get_all_meetings() -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT m.id, m.title, m.filename, m.created_at, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.meeting_id = m.id AND t.dismissed = FALSE) as task_count "
        "FROM meetings m ORDER BY m.created_at DESC"
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_meeting(meeting_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, title, filename, created_at FROM meetings WHERE id = %s", (meeting_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def save_meeting(filename: str, title: str = "") -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO meetings (title, filename) VALUES (%s, %s)", (title, filename))
    conn.commit()
    meeting_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return meeting_id


def save_transcript(meeting_id: int, content: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO transcripts (meeting_id, content) VALUES (%s, %s)",
        (meeting_id, content),
    )
    conn.commit()
    transcript_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return transcript_id


def get_transcript(meeting_id: int) -> str | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT content FROM transcripts WHERE meeting_id = %s ORDER BY id DESC LIMIT 1",
        (meeting_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else None


def update_transcript(meeting_id: int, content: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE transcripts SET content = %s WHERE meeting_id = %s",
        (content, meeting_id),
    )
    conn.commit()
    cursor.close()
    conn.close()


def save_tasks(meeting_id: int, tasks_list: list[dict]) -> list[int]:
    conn = get_connection()
    cursor = conn.cursor()
    task_ids = []
    for task in tasks_list:
        desc = task.get("description", "")
        if isinstance(desc, list):
            desc = "\n".join(f"- {item}" if not str(item).startswith("- ") else str(item) for item in desc)
        cursor.execute(
            "INSERT INTO tasks (meeting_id, title, description, assignee, jira_worthy, jira_reason, module, is_grouped) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (meeting_id, str(task.get("title", "")), str(desc), task.get("assignee"), task.get("jira_worthy", True), str(task.get("jira_reason", "")), str(task.get("module", "General")), task.get("is_grouped", False)),
        )
        task_ids.append(cursor.lastrowid)
    conn.commit()
    cursor.close()
    conn.close()
    return task_ids


def get_tasks(meeting_id: int) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, title, description, assignee, jira_ticket_id, jira_worthy, jira_reason, module, is_grouped, dismissed FROM tasks WHERE meeting_id = %s",
        (meeting_id,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def update_task(task_id: int, title: str | None = None, description: str | None = None, assignee: str | None = None):
    conn = get_connection()
    cursor = conn.cursor()
    updates = []
    values = []
    if title is not None:
        updates.append("title = %s")
        values.append(title)
    if description is not None:
        updates.append("description = %s")
        values.append(description)
    if assignee is not None:
        updates.append("assignee = %s")
        values.append(assignee)
    if updates:
        values.append(task_id)
        cursor.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = %s", values)
        conn.commit()
    cursor.close()
    conn.close()


def dismiss_task(task_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET dismissed = TRUE WHERE id = %s", (task_id,))
    conn.commit()
    cursor.close()
    conn.close()


def delete_tasks(meeting_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE meeting_id = %s", (meeting_id,))
    conn.commit()
    cursor.close()
    conn.close()


def set_jira_ticket_id(task_id: int, ticket_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE tasks SET jira_ticket_id = %s WHERE id = %s",
        (ticket_id, task_id),
    )
    conn.commit()
    cursor.close()
    conn.close()
