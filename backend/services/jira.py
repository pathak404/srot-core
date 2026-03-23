import os
import requests
from dotenv import load_dotenv

load_dotenv()

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "ENG")


def create_jira_ticket(title: str, description: str, assignee: str | None = None) -> dict:
    """Create a Jira ticket using the REST API."""
    if not JIRA_BASE_URL or not JIRA_EMAIL or not JIRA_API_TOKEN:
        raise ValueError("Jira credentials not configured. Set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN in .env")

    url = f"{JIRA_BASE_URL.rstrip('/')}/rest/api/2/issue"

    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": title,
            "description": description,
            "issuetype": {"name": "Task"},
        }
    }

    response = requests.post(
        url,
        json=payload,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Content-Type": "application/json"},
    )
    response.raise_for_status()

    data = response.json()
    ticket_key = data["key"]

    return {
        "ticket_id": ticket_key,
        "url": f"{JIRA_BASE_URL.rstrip('/')}/browse/{ticket_key}",
    }
