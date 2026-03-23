# AI Meeting Assistant MVP

Processes long meeting recordings (up to ~3 hours), generates structured transcripts in romanized Hinglish, extracts actionable tasks, and creates Jira tickets.

## Tech Stack

- **Backend:** FastAPI
- **Frontend:** Streamlit
- **LLM:** Gemini 2.5 Flash
- **Audio:** pydub + ffmpeg
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
TRANSCRIPTION_EXTRA_PROMPT=help the llm understand your business.
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
3. Optionally add context (agenda, notes)
4. Click **Process Meeting** — audio is chunked, transcribed, and tasks are extracted
5. Edit the transcript and tasks as needed
6. Click **Create Jira Ticket** on any task to push it to Jira

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/process-meeting` | Upload and process meeting audio |
| GET | `/meeting/{id}` | Get transcript and tasks |
| PUT | `/meeting/{id}/transcript` | Update transcript |
| PUT | `/task/{id}` | Update a task |
| POST | `/create-jira` | Create a Jira ticket from a task |

