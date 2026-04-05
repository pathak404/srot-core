
import json
from pathlib import Path

from backend.models.llm import generate
from backend.services.knowledge_retriever import get_jira_context, get_projects_for_tasks

_DEV_TASKS_DIR = Path("dev_tasks")

_PROMPT_TEMPLATE = """You are a senior backend engineer creating an execution-ready task file for another AI coding agent.

TERMINOLOGY RULES (STRICT):
- Project = backend system / microservice
- Service = NestJS service class (e.g. payout.service.ts)
- NEVER mix these

INPUT:

Projects involved: {project_names}

Jira Ticket:
{jira_ticket_json}

Code Context:
{retrieved_code_context}

TASK:
Generate a developer-ready markdown task file.
This file will be the SOLE source of truth for the AI agent. It must contain ALL necessary details from the code context.

OUTPUT FORMAT (STRICT MARKDOWN):

# Task: <clear title>

## 🏷 Project
<project name>

## 🧠 Context
<short explanation of the problem>

## 🎯 Objective
<what needs to be achieved>

## 🧩 Domain
- Domain Entity: <name>
- Type: <enum / api / logic / bug / feature>

## 🏗 Affected Components
- Service: <service names with .ts extension>
- Files: <list short relative paths like project-name/src/.../file.ts>
- Other: <enums / APIs if applicable>

## 🔍 Existing State
<Detail the current implementation from Code Context. Include relevant snippets, symbols, or logic rules found.>

## ➕ Required Changes
<step-by-step actions, concrete. Reference specific files and symbols.>

## ⚠️ Constraints
<what must NOT break>

## ❓ Unknowns / Clarifications Needed
<list gaps if any>

## 🧪 Suggested Validation
<how to verify correctness>

## 🚀 Execution Hint (for AI agent)
<how to start: where to look, what to search, which files to modify first>

RULES:
- USE short relative paths (e.g., project-name/src/...) provided in the context.
- ALWAYS use full file names with extensions (e.g., payout.service.ts, not just payout.service).
- Do NOT hallucinate missing code details.
- If unsure, explicitly say "Unknown".
- Keep it precise, but include all grounded technical details from the input context."""


def _build_prompt(task: dict, code_context: str, projects: list[str]) -> str:
    jira_json = json.dumps(
        {
            "title": task.get("title", ""),
            "description": task.get("description", ""),
            "assignee": task.get("assignee"),
            "jira_ticket_id": task.get("jira_ticket_id"),
        },
        indent=2,
    )
    project_names = ", ".join(projects) if projects else "Unknown"
    return _PROMPT_TEMPLATE.format(
        project_names=project_names,
        jira_ticket_json=jira_json,
        retrieved_code_context=code_context or "No code context available.",
    )


def _task_seq(task: dict) -> int:
    return task.get("task_seq") or task["id"]


def save_dev_task_file(meeting_id: int, task: dict, content: str) -> str:
    _DEV_TASKS_DIR.mkdir(exist_ok=True)
    path = _DEV_TASKS_DIR / f"m{meeting_id}-t{_task_seq(task)}.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


def read_dev_task_file(meeting_id: int, task_seq: int) -> str | None:
    path = _DEV_TASKS_DIR / f"m{meeting_id}-t{task_seq}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def generate_and_save(task: dict, meeting_id: int) -> str:
    projects = get_projects_for_tasks([task])
    code_context = get_jira_context([task])
    prompt = _build_prompt(task, code_context, projects)
    content = generate(prompt, temperature=0.0)
    return save_dev_task_file(meeting_id, task, content)
