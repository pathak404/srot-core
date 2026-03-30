import json
import re
from backend.models.llm import generate

JIRA_SUGGESTION_PROMPT = """You are a project management assistant. Given a list of tasks extracted from a meeting transcript, group them by module/area and suggest Jira tickets.

Rules:
- Group related tasks into one Jira ticket per module
- Each Jira ticket title should be short and clear, describing the work only — do NOT prefix the title with the module name (e.g. use "Implement payout status flow" not "Payments: Implement payout status flow")
- List all work items in the description as bullet points
- Skip any task that does not require writing code: manual testing requests, QA/UAT tasks, sending or forwarding something to a person, sharing credentials or links, scheduling meetings, verbal confirmations, documentation reviews
- Only include tasks that require actual software development work (coding, API changes, bug fixes, feature implementation, configuration, deployment scripts, database changes, etc.)
- If a module has only one significant task, it still gets its own ticket

Return a JSON array of suggested Jira tickets:
- "title": short Jira ticket title
- "description": bullet list of all work items for this module
- "assignee": primary assignee if clear, else null
- "module": module/area name

Return ONLY a JSON array. No markdown, no explanation, no code fences.

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


def suggest_jira_tickets(tasks: list[dict]) -> list[dict]:
    if not tasks:
        return []

    tasks_text = json.dumps(tasks, indent=2)
    response_text = generate(JIRA_SUGGESTION_PROMPT + tasks_text, temperature=0.0)

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
