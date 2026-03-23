import os
import shutil
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from storage.models import create_tables
from storage.db import (
    save_meeting, save_transcript, get_transcript, update_transcript,
    save_tasks, get_tasks, update_task, set_jira_ticket_id,
)
from backend.services.chunking import split_audio
from backend.services.transcription import transcribe_meeting
from backend.services.task_extractor import extract_tasks
from backend.services.jira import create_jira_ticket

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


@app.post("/process-meeting")
async def process_meeting(
    audio: UploadFile = File(...),
    context: str = Form(default=""),
):
    """Process a meeting audio file: transcribe and extract tasks."""
    # Save uploaded file
    file_path = os.path.join(UPLOAD_DIR, audio.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    # Save meeting record
    meeting_id = save_meeting(audio.filename)

    # Split audio into chunks
    chunk_dir = os.path.join(UPLOAD_DIR, f"meeting_{meeting_id}")
    chunks = split_audio(file_path, chunk_minutes=10, output_dir=chunk_dir)

    # Transcribe all chunks
    transcript = transcribe_meeting(chunks, context=context)

    # Save transcript
    save_transcript(meeting_id, transcript)

    # Extract tasks
    tasks = extract_tasks(transcript)

    # Save tasks
    task_ids = save_tasks(meeting_id, tasks)

    # Attach IDs to task dicts
    for i, tid in enumerate(task_ids):
        tasks[i]["id"] = tid

    # Clean up chunks (keep original upload)
    shutil.rmtree(chunk_dir, ignore_errors=True)

    return {
        "meeting_id": meeting_id,
        "transcript": transcript,
        "tasks": tasks,
    }


@app.get("/meeting/{meeting_id}")
def get_meeting(meeting_id: int):
    """Get transcript and tasks for a meeting."""
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
    """Update the transcript for a meeting."""
    update_transcript(meeting_id, body.content)
    return {"status": "updated"}


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    assignee: str | None = None


@app.put("/task/{task_id}")
def update_task_endpoint(task_id: int, body: TaskUpdate):
    """Update a task's fields."""
    update_task(task_id, title=body.title, description=body.description, assignee=body.assignee)
    return {"status": "updated"}


class JiraCreate(BaseModel):
    task_id: int
    title: str
    description: str
    assignee: str | None = None


@app.post("/create-jira")
def create_jira(body: JiraCreate):
    """Create a Jira ticket for a task."""
    result = create_jira_ticket(body.title, body.description, body.assignee)
    set_jira_ticket_id(body.task_id, result["ticket_id"])
    return result
