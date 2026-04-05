import json
import re
from backend.models.llm import generate

TASK_EXTRACTION_PROMPT = """Extract actionable tasks from this meeting transcript.

Each task must include:
- title: short summary of the task
- description: detailed description of what needs to be done
- assignee: person responsible (if mentioned, else null)

Return ONLY a JSON array. No markdown, no explanation, no code fences.

Example:
[{"title": "Set up CI pipeline", "description": "Configure GitHub Actions for automated testing", "assignee": "Speaker 1"}]

Transcript:
"""


def parse_tasks_response(response_text: str) -> list[dict]:
    text = response_text.strip()

    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        tasks = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            tasks = json.loads(match.group())
        else:
            return []

    if not isinstance(tasks, list):
        return []

    normalized = []
    for t in tasks:
        if isinstance(t, dict) and "title" in t:
            normalized.append({
                "title": t.get("title", ""),
                "description": t.get("description", ""),
                "assignee": t.get("assignee"),
            })
    return normalized


def extract_tasks(transcript: str) -> list[dict]:
    response_text = generate(TASK_EXTRACTION_PROMPT + transcript)
    return parse_tasks_response(response_text)
