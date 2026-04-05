# Srot Core

Srot-core is an AI-powered engineering intelligence system that turns meetings into code-aware, execution-ready developer tasks. It bridges the gap between what is discussed in a meeting and what an engineer (or AI agent) needs to actually implement it.

---

## What It Does

Engineering teams lose significant time translating meeting discussions into actionable tickets, and those tickets rarely contain enough context for an engineer to start work immediately. srot-core solves this end to end:

1. **Streaming Intelligence**: Transcribes meetings in real-time using Deepgram Nova-2 with support for Hindi.
2. **Context-Aware Extraction**: An intelligence pipeline filters noise, resolves context, and triggers LLM processing only when actionable tasks are identified.
3. **Generates Jira tickets**: Tasks are evaluated and shaped into well-formed Jira tickets with titles, descriptions, and assignees.
4. **Enriches tickets with code context**: Before generating anything, srot-core queries its hybrid knowledge graph (Neo4j + Qdrant) to find the exact services, functions, enums, ORM entities, and APIs relevant to each task.
5. **Produces dev-ready task files**: For every task, a structured markdown file is generated for use by engineers or AI coding agents. It contains the objective, affected components, required changes, constraints, and execution hints grounded in actual code.

---

## How It Works

### Intelligence Pipeline

```
Audio Stream (PCM 16kHz)
        │
        ▼
  Deepgram ASR (Nova-2)
  (Hinglish support, interim results)
        │
        ▼
  Confidence & Rule Filter
  (drops noise, confirms quality)
        │
        ▼
  Context Manager & Resolver
  (tracks project/module, enriches text)
        │
        ▼
  LLM Trigger (Gemini)
  (decides if a task is being discussed)
        │
        ▼
  Task Extraction & Jira State Builder
  (shapes tasks with code context & domain packs)
        │
        ▼
  Hybrid Summary Generation
  (LLM-corrected cumulative markdown)
```

### Code Knowledge Graph: The Code's Subconscious

srot-core doesn't just index files; it maps the silent conversations between your components.

- **The Skeleton (Neo4j)**: A structural graph tracking the invisible threads, how an Endpoint triggers a Function, or how a Resolver calls a Service.
- **The Soul (Qdrant)**: A multi-vector space where code is understood by its essence. Here, functions are "neighbors" based on their intent, not just their location.

At the moment of task extraction, these two dimensions collide, providing the LLM with a grounded reality of your system's true state.

### Dev Task Files

Each task produces a file like `dev_tasks/m1-t2.md` containing:

- Project name (resolved from graph)
- Domain entity and task type
- Affected services and APIs (with relative file paths)
- Existing state (concrete code snippets/logic from context)
- Step-by-step required changes
- Constraints and unknowns
- Validation approach
- Execution hint for AI agents

These files are readable via API (`GET /dev-task?meeting=1&task=2` or `GET /dev-task?jira=DEV-123`).

---

## Tech Stack


| Layer               | Technology                                      |
| ------------------- | ----------------------------------------------- |
| Backend             | FastAPI                                         |
| Frontend            | Streamlit                                       |
| LLM                 | Gemini 2.5 Flash Lite                           |
| Transcription       | Deepgram Nova-2 (Streaming & Batch)             |
| Embeddings          | Gemini Embedding 001                            |
| Graph database      | Neo4j                                           |
| Vector database     | Qdrant                                          |
| Relational database | MySQL                                           |
| Code parsing        | Tree-sitter (TypeScript Grammar)                |
| Audio processing    | static-ffmpeg + pydub                           |
| Jira integration    | Jira REST API v3                                |


---

## Prerequisites

- Python 3.12+
- MySQL
- Neo4j
- Qdrant
- ffmpeg

---

## Setup

### 1. Create virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file in the project root:

```env
# MySQL
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=your_mysql_user
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=srot_core

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_neo4j_password

# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY= 

# Jira
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=your_email
JIRA_API_TOKEN=your_jira_api_token
JIRA_PROJECT_KEY=your_project_key

# AI
GEMINI_API_KEY=your_gemini_api_key
DEEPGRAM_API_KEY=your_deepgram_api_key
TRANSCRIPTION_GLOSSARY=Term1, Term2, Term3
```

### 3. Create the database

```sql
CREATE DATABASE IF NOT EXISTS srot_core;
```

Tables are created automatically on first startup.

---

## Running

**Terminal 1 - Backend:**

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload
```

API available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

**Terminal 2 - Frontend:**

```bash
source .venv/bin/activate
streamlit run frontend/app.py
```

UI available at `http://localhost:8501`.

---

## Usage

### Recorded Meetings

1. Go to the **Upload** page
2. Upload an audio file (mp3, wav, m4a, ogg, flac, webm)
3. The system runs the full intelligence pipeline in batch mode
4. Review the generated **Hybrid Summary** and extracted tasks
5. Edit task titles, descriptions, and assignees as needed
6. Click **Create Jira Ticket** to push to Jira and generate the Dev Task file

### Live Meetings

1. Go to the **Live** page
2. Start a session; audio streams via WebSocket to the intelligence pipeline
3. Watch the transcript, context snapshots, and Jira suggestions update in real-time
4. After the meeting, the final transcript and summary are saved automatically

### Code Indexing

1. Go to the **Code Knowledge** tab
2. Enter the project name and root path of your TypeScript/NestJS codebase
3. Click **Index Project**. The system parses all files, builds the Neo4j graph, and creates Qdrant embeddings
4. Define **Domain Entities** (business concepts like `Calculator`, `MarksDistribution`) and link them to services to improve extraction accuracy

---

## API Reference

### Meetings


| Method | Endpoint                    | Description                                  |
| ------ | --------------------------- | -------------------------------------------- |
| POST   | `/process-meeting`          | Upload and process meeting audio (async)     |
| GET    | `/meetings`                 | List all meetings                            |
| GET    | `/meeting/{id}`             | Get transcript, tasks, and summary           |
| PUT    | `/meeting/{id}/transcript`  | Update transcript and re-extract tasks       |
| WS     | `/ws/intelligence/{id}`     | Streaming intelligence pipeline (PCM bytes)  |
| POST   | `/start-live-meeting`       | Initialize a live meeting record             |
| POST   | `/finalize-meeting/{id}`    | Finalize meeting status and extracted tasks  |


### Tasks


| Method | Endpoint       | Description                                                  |
| ------ | -------------- | ------------------------------------------------------------ |
| PUT    | `/task/{id}`   | Update task title, description, or assignee                  |
| DELETE | `/task/{id}`   | Dismiss a task                                               |
| POST   | `/create-jira` | Push a task to Jira and generate dev task file               |
| GET    | `/dev-task`    | Fetch dev task file (`?meeting=1&task=2` or `?jira=DEV-123`) |


### Code Knowledge


| Method | Endpoint              | Description                               |
| ------ | --------------------- | ----------------------------------------- |
| POST   | `/index-project`      | Start background indexing of a codebase    |
| GET    | `/index-jobs`         | List all indexing jobs                    |
| GET    | `/index-jobs/{id}`    | Get job status and progress               |
| GET    | `/projects`           | List indexed projects                     |
| GET    | `/services/{project}` | List services in a project                |
| POST   | `/code-knowledge`     | Manually add code context (fallback)      |


### Domain Entities


| Method | Endpoint                | Description                                |
| ------ | ----------------------- | ------------------------------------------ |
| GET    | `/domain-entities`      | List domain entities with linked services  |
| POST   | `/domain-entities`      | Create a domain entity                     |
| DELETE | `/domain-entities/{id}` | Delete a domain entity                     |
| POST   | `/domain-entities/link` | Link a domain entity to a service in Neo4j |


