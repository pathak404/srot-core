import json
import re
from backend.models.llm import generate
from backend.services.knowledge_retriever import get_jira_context

JIRA_SUGGESTION_PROMPT = """You are a project management assistant.

Given tasks from a meeting, generate Jira tickets.

Rules:
- Group related tasks into one ticket
- Title must be short, specific, and describe the work only
- Description must be bullet points of actual development work only
- Ignore non-dev tasks (QA, meetings, sharing info, etc.)
- Mention the affected service (NestJS service name, e.g. payout.service.ts, payout.resolver.ts) in description

Return ONLY a JSON array:
[
  {
    "title": "...",
    "description": "...",
    "assignee": null
  }
]

Tasks:
"""


def _parse_json_array(text: str) -> list:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return []


def suggest_jira_tickets(tasks: list[dict], project_name: str | None = None) -> list[dict]:
    if not tasks:
        return []

    code_context = get_jira_context(tasks, project_name)
    prompt = JIRA_SUGGESTION_PROMPT
    if code_context:
        prompt += (
            f"\n{code_context}\n\n"
            "Apply the code context above to:\n"
            "- NEVER mention graphql.ts or graphql.schema.ts file names.\n"
            "- NEVER mention secret keys, credentials, or any other sensitive information.\n\n"
        )

    tasks_text = json.dumps(tasks, indent=2)
    response_text = generate(prompt + tasks_text, temperature=0.0)

    suggestions = _parse_json_array(response_text)

    if not isinstance(suggestions, list) or len(suggestions) == 0:
        return [{
            "title": "Meeting action items",
            "description": "\n".join(f"- {t.get('title', '')}: {t.get('description', '')}" for t in tasks),
            "assignee": None,
            "module": "General",
        }]

    normalized = []
    for s in suggestions:
        if isinstance(s, dict) and "title" in s:
            desc = s.get("description", "")
            if isinstance(desc, list):
                desc = "\n".join(f"- {item}" if not str(item).startswith("- ") else str(item) for item in desc)
            normalized.append({
                "title": str(s.get("title", "")),
                "description": str(desc),
                "assignee": s.get("assignee"),
                "module": str(s.get("module", "General")),
                "jira_worthy": True,
                "jira_reason": "",
            })
    return normalized
