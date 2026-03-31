# Srot Core

Srot-core is an AI-powered engineering intelligence system that turns meetings into code-aware, execution-ready developer tasks. It bridges the gap between what is discussed in a meeting and what an engineer (or AI agent) needs to actually implement it.

---

## What It Does

Engineering teams lose significant time translating meeting discussions into actionable tickets, and those tickets rarely contain enough context for an engineer to start work immediately. srot-core solves this end to end:

1. **Transcribes meetings**: Upload a recording or use live audio capture. srot-core transcribes it accurately, handling mixed-language (Hinglish/English) speech.
2. **Extracts tasks**: An LLM reads the transcript and identifies actionable engineering tasks, grouped by domain module.
3. **Generates Jira tickets**: Tasks are evaluated and shaped into well-formed Jira tickets with titles, descriptions, and assignees.
4. **Enriches tickets with code context**: Before generating anything, srot-core queries its code knowledge graph to find the exact services, functions, enums, and APIs relevant to each task.
5. **Produces dev-ready task files**: For every task, a structured markdown file (`m{meeting}-t{task}.md`) is generated for use by engineers or AI coding agents. It contains the objective, affected components, required changes, constraints, and execution hints grounded in actual code.

---

## How It Works

### Meeting Pipeline

```
Audio File / Live Mic
        │
        ▼
  Audio Pre-processing
  (16kHz mono, noise gate, 10-min chunks)
        │
        ▼
  Gemini 2.5 Flash Transcription
  (glossary injection, multi-pass refinement)
        │
        ▼
  Task Extraction LLM
  (groups by module, drops trivial items)
        │
        ▼
  Jira Suggestion LLM
  (shapes tasks into Jira-ready tickets with code context)
        │
        ▼
  Dev Task File Generation
  (execution-ready markdown per task)
```

### Code Knowledge Graph

srot-core indexes your TypeScript/NestJS codebase into a hybrid knowledge store:

- **Neo4j (graph)**: Nodes for Services, Functions, Enums, API Endpoints, GraphQL Resolvers, and Domain Entities with typed relationships (`DEFINED_IN`, `TRIGGERS`, `USES`, `HANDLES`, `EXPOSES`).
- **Qdrant (vector)**: Each function, class, and enum is embedded using Gemini Embedding and stored for semantic search. One Qdrant collection per project (`code_{project_name}`).

At task generation time, both stores are queried: graph lookups find exact structural matches (service names, enum values, function signatures), while vector search finds semantically relevant code chunks. The combined context feeds the LLM.

### Dev Task Files

Each task produces a file like `dev_tasks/m1-t2.md` containing:

- Project name (resolved from graph, supports multi-project tasks)
- Domain entity and task type
- Affected services and APIs
- Existing state (from code context)
- Step-by-step required changes
- Constraints and unknowns
- Validation approach
- Execution hint for AI agents

These files are readable via API (`GET /dev-task?meeting=1&task=2` or `GET /dev-task?jira=DEV-123`).

---

## Tech Stack


| Layer               | Technology                            |
| ------------------- | ------------------------------------- |
| Backend             | FastAPI                               |
| Frontend            | Streamlit                             |
| LLM                 | Gemini 2.5 Flash                      |
| Live transcription  | Gemini Live API (WebSocket)           |
| Embeddings          | Gemini Embedding 001 (via LlamaIndex) |
| Graph database      | Neo4j                                 |
| Vector database     | Qdrant                                |
| Relational database | MySQL                                 |
| Code parsing        | Tree-sitter (TypeScript)              |
| Audio processing    | pydub + ffmpeg                        |
| Jira integration    | Jira REST API v3                      |


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
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_gemini_api_key

# MySQL
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=your_mysql_user
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=meeting_assistant_mvp

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_neo4j_password

# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=                      # optional, for cloud deployments

# Jira
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=your_email
JIRA_API_TOKEN=your_jira_api_token
JIRA_PROJECT_KEY=your_project_key

# Transcription tuning
TRANSCRIPTION_EXTRA_PROMPT="Describe your meeting context here (A generic context, optional)."
TRANSCRIPTION_GLOSSARY=Term1, Term2, Term3
```

### 3. Create the database

```sql
CREATE DATABASE IF NOT EXISTS meeting_assistant_mvp;
```

Tables are created automatically on first startup.

---

## Running

**Terminal 1 — Backend:**

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload
```

API available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

**Terminal 2 — Frontend:**

```bash
source .venv/bin/activate
streamlit run frontend/app.py
```

UI available at `http://localhost:8501`.

---

## Usage

### Recorded Meetings

1. Go to the **Upload** page
2. Upload an audio file (mp3, wav, m4a, ogg, flac, webm) up to ~3 hours
3. Add optional context (agenda, speaker names, technical glossary)
4. Click **Process Meeting**
5. Review and edit the transcript on the meeting detail page
6. Click **Save & Extract Tasks**, and Jira suggestions are generated with code context
7. Edit task titles, descriptions, and assignees as needed
8. Click **Create Jira Ticket** to push to Jira

### Live Meetings

1. Go to the **Live** page
2. Start a session (audio streams in real time via the Gemini Live API)
3. After the meeting, click **Finalize** to refine the transcript and extract tasks

### Code Indexing

1. Go to the **Code Knowledge** tab
2. Enter the project name and root path of your TypeScript/NestJS codebase
3. Click **Index Project**. The background job parses all files, builds the Neo4j graph, and creates Qdrant embeddings automatically
4. Optionally define **Domain Entities** (business concepts like `Payout`, `Partner`) and link them to services

---

## API Reference

### Meetings


| Method | Endpoint                   | Description                             |
| ------ | -------------------------- | --------------------------------------- |
| POST   | `/process-meeting`         | Upload and process meeting audio        |
| GET    | `/meetings`                | List all meetings                       |
| GET    | `/meeting/{id}`            | Get transcript and tasks                |
| PUT    | `/meeting/{id}/transcript` | Update transcript and re-extract tasks  |
| POST   | `/start-live-meeting`      | Start a live meeting session            |
| POST   | `/finalize-meeting/{id}`   | Finalize live meeting and extract tasks |


### Tasks


| Method | Endpoint       | Description                                                  |
| ------ | -------------- | ------------------------------------------------------------ |
| PUT    | `/task/{id}`   | Update task title, description, or assignee                  |
| DELETE | `/task/{id}`   | Dismiss a task                                               |
| POST   | `/create-jira` | Push a task to Jira                                          |
| GET    | `/dev-task`    | Fetch dev task file (`?meeting=1&task=2` or `?jira=DEV-123`) |


### Code Knowledge


| Method | Endpoint              | Description                             |
| ------ | --------------------- | --------------------------------------- |
| POST   | `/index-project`      | Start background indexing of a codebase |
| GET    | `/index-jobs`         | List all indexing jobs                  |
| GET    | `/index-jobs/{id}`    | Get job status and progress             |
| GET    | `/projects`           | List indexed projects                   |
| GET    | `/services/{project}` | List services in a project              |


### Domain Entities


| Method | Endpoint                | Description                                |
| ------ | ----------------------- | ------------------------------------------ |
| GET    | `/domain-entities`      | List domain entities with linked services  |
| POST   | `/domain-entities`      | Create a domain entity                     |
| DELETE | `/domain-entities/{id}` | Delete a domain entity                     |
| POST   | `/domain-entities/link` | Link a domain entity to a service in Neo4j |


