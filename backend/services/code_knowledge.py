
import json
from storage.db import get_all_code_entities


def get_manual_context(tasks: list[dict]) -> str:

    try:
        entities = get_all_code_entities()
    except Exception:
        return ""
    if not entities:
        return ""

    task_text = " ".join(
        f"{t.get('title', '')} {t.get('description', '')}" for t in tasks
    ).lower()

    lines = []
    for e in entities:
        if e["name"].lower() not in task_text:
            continue
        if e["type"] == "enum":
            values = json.loads(e["values_json"]) if e["values_json"] else []
            vals_str = ", ".join(str(v) for v in values) if values else "unknown"
            svc = f" [{e['service']}]" if e["service"] else ""
            lines.append(f"- {e['name']}{svc} is an enum with existing values: [{vals_str}]")
        elif e["type"] == "service":
            lines.append(f"- {e['name']} is a service")
        elif e["type"] == "field":
            svc = f" in {e['service']}" if e["service"] else ""
            desc = f": {e['description']}" if e["description"] else ""
            lines.append(f"- {e['name']}{svc} is a field{desc}")
        else:
            svc = f" [{e['service']}]" if e["service"] else ""
            desc = f": {e['description']}" if e["description"] else ""
            lines.append(f"- {e['name']}{svc}{desc}")

    if not lines:
        return ""
    return "\n".join(lines)
