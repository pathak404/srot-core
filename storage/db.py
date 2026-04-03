import json
from storage.models import get_connection


def get_all_meetings(page: int = 1, per_page: int = 20) -> dict:
    offset = (page - 1) * per_page
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT COUNT(*) as total FROM meetings")
    total = cursor.fetchone()["total"]
    cursor.execute(
        "SELECT m.id, m.title, m.filename, m.status, m.source, m.created_at, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.meeting_id = m.id AND t.dismissed = FALSE) as task_count "
        "FROM meetings m ORDER BY m.created_at DESC LIMIT %s OFFSET %s",
        (per_page, offset),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return {
        "items": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


def get_meeting(meeting_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, title, filename, created_at FROM meetings WHERE id = %s", (meeting_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def update_meeting_status(meeting_id: int, status: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE meetings SET status = %s WHERE id = %s", (status, meeting_id))
    conn.commit()
    cursor.close()
    conn.close()


def save_meeting(filename: str, title: str = "", source: str = "upload", status: str = "completed") -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO meetings (title, filename, status, source) VALUES (%s, %s, %s, %s)", (title, filename, status, source))
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


def save_meeting_summary(meeting_id: int, summary_md: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE meetings SET summary_md = %s WHERE id = %s",
        (summary_md, meeting_id),
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_meeting_summary(meeting_id: int) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT summary_md FROM meetings WHERE id = %s", (meeting_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return (row[0] or "") if row else ""


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
        "SELECT id, title, description, assignee, jira_ticket_id, jira_worthy, jira_reason, module, is_grouped, dismissed FROM tasks WHERE meeting_id = %s ORDER BY id",
        (meeting_id,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for seq, row in enumerate(rows, start=1):
        row["task_seq"] = seq
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


# Code Entities (manual)

def get_all_code_entities() -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, type, service, values_json, description FROM code_entities ORDER BY type, name")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def save_code_entity(name: str, entity_type: str, service: str | None = None, values_json: str | None = None, description: str | None = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO code_entities (name, type, service, values_json, description) VALUES (%s, %s, %s, %s, %s)",
        (name, entity_type, service, values_json, description),
    )
    conn.commit()
    entity_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return entity_id


def delete_code_entity(entity_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM code_entities WHERE id = %s", (entity_id,))
    conn.commit()
    cursor.close()
    conn.close()


# Index Jobs

def create_index_job(project_name: str, root_path: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO index_jobs (project_name, root_path, status) VALUES (%s, %s, 'pending')",
        (project_name, root_path),
    )
    conn.commit()
    job_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return job_id


def update_index_job(
    job_id: int,
    status: str,
    node_count: int = 0,
    edge_count: int = 0,
    error: str | None = None,
    progress: int = 0,
    current_step: str = "",
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE index_jobs SET status=%s, node_count=%s, edge_count=%s, error=%s, progress=%s, current_step=%s WHERE id=%s",
        (status, node_count, edge_count, error, progress, current_step, job_id),
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_index_jobs() -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, project_name, root_path, status, node_count, edge_count, "
        "error, progress, current_step, created_at, updated_at "
        "FROM index_jobs ORDER BY created_at DESC"
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_latest_completed_project() -> str | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT project_name FROM index_jobs WHERE status = 'completed' ORDER BY updated_at DESC LIMIT 1"
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row["project_name"] if row else None


def get_latest_job_by_path(root_path: str) -> dict | None:
    """Return the most recent job for this root_path regardless of status."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, project_name, root_path, status, node_count, edge_count, progress, current_step "
        "FROM index_jobs WHERE root_path = %s ORDER BY created_at DESC LIMIT 1",
        (root_path,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def get_index_job(job_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, project_name, root_path, status, node_count, edge_count, "
        "error, progress, current_step, created_at, updated_at "
        "FROM index_jobs WHERE id=%s",
        (job_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


# Domain Entities (MySQL CRUD)

def get_domain_entities(project_name: str | None = None) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    if project_name:
        cursor.execute(
            "SELECT id, name, project_name, description, created_at FROM domain_entities "
            "WHERE project_name = %s ORDER BY name",
            (project_name,),
        )
    else:
        cursor.execute(
            "SELECT id, name, project_name, description, created_at FROM domain_entities "
            "ORDER BY project_name, name"
        )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def save_domain_entity(name: str, project_name: str | None, description: str | None = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO domain_entities (name, project_name, description) VALUES (%s, %s, %s)",
        (name, project_name, description),
    )
    conn.commit()
    entity_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return entity_id


def delete_domain_entity(entity_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM domain_entities WHERE id = %s", (entity_id,))
    conn.commit()
    cursor.close()
    conn.close()


def get_domain_entity(entity_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, name, project_name, description FROM domain_entities WHERE id = %s",
        (entity_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def get_task(task_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, meeting_id, title, description, assignee, jira_ticket_id FROM tasks WHERE id = %s",
        (task_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def get_task_by_jira_id(jira_ticket_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, meeting_id, title, description, assignee, jira_ticket_id FROM tasks WHERE jira_ticket_id = %s",
        (jira_ticket_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


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


def save_intelligence_state(meeting_id: int, state: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO intelligence_state (meeting_id, state_json) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE state_json = VALUES(state_json), updated_at = CURRENT_TIMESTAMP",
        (meeting_id, json.dumps(state))
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_intelligence_state(meeting_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT state_json FROM intelligence_state WHERE meeting_id = %s ORDER BY updated_at DESC LIMIT 1",
        (meeting_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return json.loads(row["state_json"])
    return None


def save_live_tickets(meeting_id: int, tickets: list[dict]):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM jira_live_tickets WHERE meeting_id = %s", (meeting_id,))
    for ticket in tickets:
        cursor.execute(
            "INSERT INTO jira_live_tickets (meeting_id, ticket_json) VALUES (%s, %s)",
            (meeting_id, json.dumps(ticket))
        )
    conn.commit()
    cursor.close()
    conn.close()


def get_live_tickets(meeting_id: int) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT ticket_json FROM jira_live_tickets WHERE meeting_id = %s ORDER BY created_at",
        (meeting_id,)
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [json.loads(r["ticket_json"]) for r in rows]


def get_live_meetings() -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM meetings WHERE status = 'live'")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows
