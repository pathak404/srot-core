import os
import shutil
import asyncio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from storage.models import create_tables
from storage.db import (
    get_all_meetings, get_meeting, save_meeting, save_transcript, get_transcript, update_transcript,
    update_meeting_status, save_tasks, get_tasks, update_task, set_jira_ticket_id, delete_tasks, dismiss_task,
)
from backend.services.chunking import split_audio
from backend.services.transcription import transcribe_meeting
from backend.services.task_extractor import extract_tasks
from backend.services.jira_evaluator import suggest_jira_tickets
from backend.services.jira import create_jira_ticket, is_jira_configured
from backend.services.live_transcription import LiveTranscriptionSession

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


@app.get("/live")
def live_page():
    live_html_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "static", "live.html")
    with open(live_html_path, "r") as f:
        return HTMLResponse(content=f.read())
