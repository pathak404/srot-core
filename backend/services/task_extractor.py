import os
import json
import re
from google import genai
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

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
    """Parse the Gemini response into a list of task dicts."""
    text = response_text.strip()

    # Remove markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        tasks = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array in the text
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            tasks = json.loads(match.group())
        else:
            return []

    if not isinstance(tasks, list):
        return []

    # Normalize each task
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
    """Extract actionable tasks from a meeting transcript using Gemini."""
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=TASK_EXTRACTION_PROMPT + transcript,
    )
    return parse_tasks_response(response.text)
