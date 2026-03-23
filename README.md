# AI Meeting Assistant MVP

Processes long meeting recordings (up to ~3 hours), generates structured transcripts in romanized Hinglish, extracts actionable tasks grouped by module, and creates Jira tickets.

## Tech Stack

- **Backend:** FastAPI
- **Frontend:** Streamlit
- **LLM:** Gemini 2.5 Flash (transcription, task extraction, Jira suggestion)
- **Audio:** pydub + ffmpeg (chunking, noise gate, 16kHz normalization)
- **Database:** MySQL
- **Integration:** Jira REST API

## Prerequisites

- Python 3.12+
- MySQL
- ffmpeg

## Setup

### 1. Create virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

Create a `.env` file in the project root:

```
GEMINI_API_KEY=your_gemini_api_key

# MySQL
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=your_mysql_user
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=meeting_assistant_mvp

# Jira
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=your_email
JIRA_API_TOKEN=your_jira_api_token
JIRA_PROJECT_KEY=your_project_key

# Transcription tuning
TRANSCRIPTION_EXTRA_PROMPT="Describe your meeting context here to help the LLM."
TRANSCRIPTION_GLOSSARY=Term1, Term2, Term3
```

### 4. Create the database

```sql
CREATE DATABASE IF NOT EXISTS meeting_assistant_mvp;
```

Tables are created automatically on server startup.

## Running

**Terminal 1 — Backend:**

```bash
source venv/bin/activate
uvicorn backend.main:app --reload
```

Backend runs at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

**Terminal 2 — Frontend:**

```bash
source venv/bin/activate
streamlit run frontend/app.py
```

Frontend runs at `http://localhost:8501`.

## Usage

1. Open the Streamlit UI
2. Upload a meeting audio file (mp3, wav, m4a, ogg, flac, webm)
3. Optionally add context (agenda, speaker names, notes)
4. Click **Process Meeting** — audio is chunked, transcribed, tasks are extracted and grouped by module
5. Edit the transcript — saving re-extracts tasks automatically
6. Review suggested Jira tickets, edit title/description/assignee as needed
7. **Dismiss** tasks you don't want to track
8. Click **Create Jira Ticket** to push to Jira

## Features

- **Expert transcription** — System instruction persona with low temperature for accuracy
- **Glossary support** — Domain terms via `TRANSCRIPTION_GLOSSARY` env var to fix phonetic errors
- **Multi-pass refinement** — Second LLM pass corrects technical term misspellings
- **Audio pre-processing** — 16kHz mono normalization + noise gate before transcription
- **Smart Jira suggestions** — LLM groups related tasks by module, drops trivial items
- **Transcript sync** — Editing transcript re-runs task extraction and Jira suggestions
- **Dismiss tasks** — Soft-delete tasks you don't need; shown grayed out in UI

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/process-meeting` | Upload and process meeting audio |
| GET | `/meeting/{id}` | Get transcript and tasks |
| PUT | `/meeting/{id}/transcript` | Update transcript (re-extracts tasks) |
| PUT | `/task/{id}` | Update a task |
| DELETE | `/task/{id}` | Dismiss a task |
| POST | `/create-jira` | Create a Jira ticket from a task |

## Project Structure

```
mvp/
├── backend/
│   ├── main.py                  # FastAPI app
│   ├── models/
│   │   └── llm.py               # Shared Gemini client
│   └── services/
│       ├── chunking.py          # Audio splitting + pre-processing
│       ├── transcription.py     # Gemini transcription + refinement
│       ├── task_extractor.py    # Task extraction from transcript
│       ├── jira_evaluator.py    # LLM-based Jira ticket suggestions
│       └── jira.py              # Jira REST API
├── frontend/
│   └── app.py                   # Streamlit UI
├── storage/
│   ├── models.py                # MySQL schema
│   └── db.py                    # CRUD operations
├── requirements.txt
└── .env
```
