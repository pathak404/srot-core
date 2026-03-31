from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class JiraState:
    tickets: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    last_updated: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def _token_overlap(a: str, b: str) -> float:
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


class JiraStateBuilder:
    """
    Maintains the live Jira ticket list for a meeting.

    On each LLM output:
      - If type == "decision": appends to decisions list, not tickets.
      - If task is null: skipped.
      - If similar ticket exists (title overlap > 80%): merge (update non-null fields only).
      - Otherwise: create new ticket with status "open".
    """

    def __init__(self):
        self._state = JiraState()

    def update(self, llm_output: dict, context: dict):
        task_title = llm_output.get("task")
        if not task_title:
            return

        ticket_type = llm_output.get("type")
        if ticket_type == "decision":
            desc = llm_output.get("description") or ""
            self._state.decisions.append(f"{task_title}: {desc}".strip(": "))
            self._update_timestamp()
            return

        # Check for duplicate
        for existing in self._state.tickets:
            if _token_overlap(existing["title"], task_title) > 0.70:
                for field_name in ("description", "eta", "assignee", "type"):
                    new_val = llm_output.get(field_name)
                    if new_val is not None:
                        existing[field_name] = new_val
                self._update_timestamp()
                return

        # New ticket
        self._state.tickets.append({
            "title": task_title,
            "description": llm_output.get("description"),
            "eta": llm_output.get("eta"),
            "type": ticket_type,
            "assignee": llm_output.get("assignee"),
            "status": "open",
        })
        self._update_timestamp()

    def get_state(self) -> JiraState:
        return self._state

    def _update_timestamp(self):
        self._state.last_updated = datetime.now(timezone.utc).isoformat()
