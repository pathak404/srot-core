import os
import shutil
import asyncio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from storage.models import create_tables
from storage.db import (
    get_all_meetings, get_meeting, save_meeting, save_transcript, get_transcript, update_transcript,
    update_meeting_status, save_tasks, get_tasks, update_task, set_jira_ticket_id, delete_tasks, dismiss_task,
    get_all_code_entities, save_code_entity, delete_code_entity,
    create_index_job, update_index_job, get_index_jobs, get_index_job, get_latest_job_by_path,
    get_domain_entities, save_domain_entity, delete_domain_entity, get_domain_entity,
)
from backend.services.chunking import split_audio
from backend.services.transcription import transcribe_meeting
from backend.services.task_extractor import extract_tasks
from backend.services.jira_evaluator import suggest_jira_tickets
from backend.services.jira import create_jira_ticket, is_jira_configured
from backend.services.live_transcription import LiveTranscriptionSession
from backend.services.code_indexer import run_indexing

app = FastAPI(title="AI Meeting Assistant MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.on_event("startup")
def startup():
    create_tables()


@app.get("/config")
def get_config():
    return {"jira_enabled": is_jira_configured()}


def _extract_and_suggest(transcript: str, meeting_id: int) -> list[dict]:
    raw_tasks = extract_tasks(transcript)

    if is_jira_configured():
        suggestions = suggest_jira_tickets(raw_tasks)
    else:
        suggestions = raw_tasks

    task_ids = save_tasks(meeting_id, suggestions)
    for i, tid in enumerate(task_ids):
        suggestions[i]["id"] = tid
    return suggestions


@app.post("/process-meeting")
async def process_meeting(
    audio: UploadFile = File(...),
    title: str = Form(default=""),
    context: str = Form(default=""),
):
    file_path = os.path.join(UPLOAD_DIR, audio.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    meeting_title = title.strip() or audio.filename
    meeting_id = save_meeting(audio.filename, meeting_title)

    chunk_dir = os.path.join(UPLOAD_DIR, f"meeting_{meeting_id}")
    chunks = split_audio(file_path, chunk_minutes=10, output_dir=chunk_dir)

    transcript = transcribe_meeting(chunks, context=context)
    save_transcript(meeting_id, transcript)

    shutil.rmtree(chunk_dir, ignore_errors=True)

    return {
        "meeting_id": meeting_id,
        "transcript": transcript,
    }


@app.get("/meetings")
def list_meetings():
    return get_all_meetings()


@app.get("/meeting/{meeting_id}")
def get_meeting_endpoint(meeting_id: int):
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    transcript = get_transcript(meeting_id)
    tasks = get_tasks(meeting_id)
    return {
        "meeting_id": meeting_id,
        "title": meeting.get("title") or meeting.get("filename", ""),
        "filename": meeting.get("filename", ""),
        "transcript": transcript or "",
        "tasks": tasks,
    }


class TranscriptUpdate(BaseModel):
    content: str


@app.put("/meeting/{meeting_id}/transcript")
def update_meeting_transcript(meeting_id: int, body: TranscriptUpdate):
    update_transcript(meeting_id, body.content)

    delete_tasks(meeting_id)
    tasks = _extract_and_suggest(body.content, meeting_id)

    return {"status": "updated", "tasks": tasks}


class TaskUpdateBody(BaseModel):
    title: str | None = None
    description: str | None = None
    assignee: str | None = None


@app.put("/task/{task_id}")
def update_task_endpoint(task_id: int, body: TaskUpdateBody):
    update_task(task_id, title=body.title, description=body.description, assignee=body.assignee)
    return {"status": "updated"}


@app.delete("/task/{task_id}")
def dismiss_task_endpoint(task_id: int):
    dismiss_task(task_id)
    return {"status": "dismissed"}


class JiraCreate(BaseModel):
    task_id: int
    title: str
    description: str
    assignee: str | None = None


@app.post("/create-jira")
def create_jira(body: JiraCreate):
    result = create_jira_ticket(body.title, body.description, body.assignee)
    set_jira_ticket_id(body.task_id, result["ticket_id"])
    return result



# Live Transcription 

active_sessions: dict[int, LiveTranscriptionSession] = {}


class LiveMeetingCreate(BaseModel):
    title: str = ""


@app.post("/start-live-meeting")
def start_live_meeting(body: LiveMeetingCreate):
    title = body.title.strip() or "Live Meeting"
    meeting_id = save_meeting("live_recording", title, source="live", status="live")
    save_transcript(meeting_id, "")
    return {"meeting_id": meeting_id}


@app.websocket("/ws/live-transcribe/{meeting_id}")
async def live_transcribe(websocket: WebSocket, meeting_id: int):
    await websocket.accept()

    session = LiveTranscriptionSession(meeting_id)
    active_sessions[meeting_id] = session

    try:
        await session.start()

        async def receive_audio():
            try:
                while True:
                    data = await websocket.receive_bytes()
                    await session.send_audio(data)
            except (WebSocketDisconnect, Exception):
                pass

        async def send_transcript():
            try:
                async for text in session.receive_text():
                    try:
                        await websocket.send_json({"type": "transcript", "text": text})
                    except Exception:
                        return
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        async def periodic_save():
            try:
                while True:
                    await asyncio.sleep(30)
                    if session.get_buffer():
                        update_transcript(meeting_id, session.get_buffer())
            except asyncio.CancelledError:
                pass

        audio_task = asyncio.create_task(receive_audio())
        transcript_task = asyncio.create_task(send_transcript())
        save_task = asyncio.create_task(periodic_save())

        # Wait for browser to disconnect, then cancel the rest
        await audio_task
        transcript_task.cancel()
        save_task.cancel()
        await asyncio.gather(transcript_task, save_task, return_exceptions=True)

    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass
    finally:
        buffer = await session.stop()
        if buffer:
            update_transcript(meeting_id, buffer)
        active_sessions.pop(meeting_id, None)


@app.post("/finalize-meeting/{meeting_id}")
def finalize_meeting(meeting_id: int):
    transcript = get_transcript(meeting_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="No transcript found")

    from backend.services.transcription import refine_transcript
    refined = refine_transcript(transcript)
    update_transcript(meeting_id, refined)
    update_meeting_status(meeting_id, "completed")

    tasks = _extract_and_suggest(refined, meeting_id)

    return {"status": "finalized", "meeting_id": meeting_id, "tasks": tasks}


# Code Knowledge - Index Jobs + Manual Entities

class IndexProjectRequest(BaseModel):
    project_name: str
    root_path: str
    force: bool = False


@app.post("/index-project")
async def index_project(body: IndexProjectRequest, background_tasks: BackgroundTasks):
    # Dedup check based on most recent job for this path
    if not body.force:
        latest = get_latest_job_by_path(body.root_path)
        if latest:
            if latest["status"] == "completed":
                return {
                    "job_id": latest["id"],
                    "status": "already_indexed",
                    "project_name": latest["project_name"],
                    "node_count": latest["node_count"],
                    "edge_count": latest["edge_count"],
                }
            if latest["status"] in ("running", "pending"):
                return {
                    "job_id": latest["id"],
                    "status": "already_running",
                    "project_name": latest["project_name"],
                }
            # status == "failed" -> fall through and re-index

    job_id = create_index_job(body.project_name, body.root_path)
    background_tasks.add_task(run_indexing, job_id, body.root_path, body.project_name)
    return {"job_id": job_id, "status": "started"}


@app.get("/index-jobs")
def list_index_jobs():
    return get_index_jobs()


@app.get("/index-jobs/{job_id}")
def get_index_job_status(job_id: int):
    job = get_index_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


class CodeEntityCreate(BaseModel):
    name: str
    type: str
    service: str | None = None
    values_json: str | None = None
    description: str | None = None


@app.get("/code-knowledge")
def list_code_knowledge():
    return get_all_code_entities()


@app.post("/code-knowledge")
def create_code_knowledge(body: CodeEntityCreate):
    entity_id = save_code_entity(body.name, body.type, body.service, body.values_json, body.description)
    return {"id": entity_id}


@app.delete("/code-knowledge/{entity_id}")
def remove_code_knowledge(entity_id: int):
    delete_code_entity(entity_id)
    return {"status": "deleted"}


# Domain Entities

class DomainEntityCreate(BaseModel):
    name: str
    project_name: str | None = None
    description: str | None = None


class DomainEntityLink(BaseModel):
    domain_name: str
    service_name: str
    project_name: str
    rel_type: str = "HANDLES"


@app.get("/domain-entities")
def list_domain_entities(project: str | None = None):
    entities = get_domain_entities(project)
    # Enrich with linked services from Neo4j
    from backend.services import graph_store
    if graph_store.is_available():
        try:
            neo4j_map = {e["name"]: e for e in graph_store.get_domain_entities(project)}
            for entity in entities:
                neo_entry = neo4j_map.get(entity["name"], {})
                entity["linked_services"] = neo_entry.get("linked_services", [])
        except Exception:
            for entity in entities:
                entity["linked_services"] = []
    return entities


@app.post("/domain-entities")
def create_domain_entity_endpoint(body: DomainEntityCreate):
    entity_id = save_domain_entity(body.name, body.project_name, body.description)
    if body.project_name:
        from backend.services.graph_store import create_domain_entity
        try:
            create_domain_entity(body.project_name, body.name, body.description or "")
        except Exception:
            pass
    return {"id": entity_id}


@app.delete("/domain-entities/{entity_id}")
def delete_domain_entity_endpoint(entity_id: int):
    entity = get_domain_entity(entity_id)
    delete_domain_entity(entity_id)
    if entity and entity.get("project_name"):
        from backend.services.graph_store import delete_domain_entity_node
        try:
            delete_domain_entity_node(entity["project_name"], entity["name"])
        except Exception:
            pass
    return {"status": "deleted"}


@app.post("/domain-entities/link")
def link_domain_entity_endpoint(body: DomainEntityLink):
    from backend.services.graph_store import link_domain_entity
    try:
        link_domain_entity(body.project_name, body.domain_name, body.service_name, body.rel_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "linked"}


@app.get("/projects")
def list_projects():
    from backend.services.graph_store import get_all_projects
    return get_all_projects()


@app.get("/services/{project_name}")
def list_services(project_name: str):
    from backend.services.graph_store import get_services
    return get_services(project_name)


@app.get("/live")
def live_page():
    live_html_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "static", "live.html")
    with open(live_html_path, "r") as f:
        return HTMLResponse(content=f.read())
