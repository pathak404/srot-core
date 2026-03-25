import os
import shutil
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from storage.models import create_tables
from storage.db import (
    save_meeting, save_transcript, get_transcript, update_transcript,
    save_tasks, get_tasks, update_task, set_jira_ticket_id, delete_tasks, dismiss_task,
)
from backend.services.chunking import split_audio
from backend.services.transcription import transcribe_meeting
from backend.services.task_extractor import extract_tasks
from backend.services.jira_evaluator import suggest_jira_tickets
from backend.services.jira import create_jira_ticket, is_jira_configured

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
    context: str = Form(default=""),
):
    file_path = os.path.join(UPLOAD_DIR, audio.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    meeting_id = save_meeting(audio.filename)

    chunk_dir = os.path.join(UPLOAD_DIR, f"meeting_{meeting_id}")
    chunks = split_audio(file_path, chunk_minutes=10, output_dir=chunk_dir)

    transcript = transcribe_meeting(chunks, context=context)
    save_transcript(meeting_id, transcript)

    shutil.rmtree(chunk_dir, ignore_errors=True)

    return {
        "meeting_id": meeting_id,
        "transcript": transcript,
    }


@app.get("/meeting/{meeting_id}")
def get_meeting(meeting_id: int):
    transcript = get_transcript(meeting_id)
    if transcript is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    tasks = get_tasks(meeting_id)
    return {
        "meeting_id": meeting_id,
        "transcript": transcript,
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
