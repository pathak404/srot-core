
import json
import re

from backend.services.code_knowledge import get_manual_context
from backend.services import graph_store, vector_index

_STOP_WORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "have",
    "will", "need", "should", "would", "could", "also", "then", "when",
    "where", "which", "their", "they", "them", "about", "after", "before",
    "update", "add", "new", "remove", "change", "implement", "create",
    "make", "fix", "task", "feature", "service", "module",
}


def _extract_terms(tasks: list[dict]) -> list[str]:
    text = " ".join(
        f"{t.get('title', '')} {t.get('description', '')}" for t in tasks
    )
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[^a-zA-Z0-9 ]", " ", text)
    words = text.split()
    seen: set[str] = set()
    terms: list[str] = []
    for w in words:
        low = w.lower()
        if len(low) >= 3 and low not in _STOP_WORDS and low not in seen:
            seen.add(low)
            terms.append(w)
    return terms[:25]


def _format_graph_results(graph_results: list[dict]) -> tuple[list[str], list[str], list[str]]:
    enum_lines = []
    service_lines = []
    function_lines = []
    seen_names: set[str] = set()

    for r in graph_results:
        name = r.get("name", "")
        # composite key for entity columns so id/name clashes across entities don't dedup
        dedup_key = f"{r.get('class_name', '')}.{name}" if r.get("entity_type") == "entity_column" else name
        if dedup_key in seen_names:
            continue
        seen_names.add(dedup_key)

        if r["entity_type"] == "enum":
            try:
                values = json.loads(r["values_json"] or "[]")
            except Exception:
                values = []
            svc = f" [{r['service']}]" if r.get("service") else ""
            if values:
                vals_str = ", ".join(
                    f"{v['name']}({v['value']})" if isinstance(v, dict) else str(v)
                    for v in values
                )
            else:
                vals_str = "no values indexed"
            enum_lines.append(f"- {name}{svc}: {vals_str}")

        elif r["entity_type"] == "service":
            service_lines.append(f"- {name}")

        elif r["entity_type"] == "function":
            svc = f" [{r['service']}]" if r.get("service") else ""
            params = r.get("params") or ""
            ret = r.get("return_type") or ""
            sig = f"{name}({params})"
            if ret:
                sig += f" → {ret}"
            guards = r.get("guards")
            if guards:
                try:
                    guard_list = json.loads(guards) if isinstance(guards, str) else guards
                    if guard_list:
                        sig += f" [guards: {', '.join(guard_list)}]"
                except Exception:
                    pass
            function_lines.append(f"- {sig}{svc}")

        elif r["entity_type"] == "entity_column":
            entity = r.get("entity_name") or r.get("class_name") or ""
            relation_kind = r.get("relation_kind") or ""
            if relation_kind:
                target = r.get("relation_target") or ""
                descriptor = f"@{relation_kind} → {target}" if target else f"@{relation_kind}"
            else:
                parts = []
                if r.get("column_type"):
                    parts.append(r["column_type"])
                if r.get("is_primary"):
                    parts.append("PK")
                if r.get("nullable"):
                    parts.append("nullable")
                if r.get("default_value"):
                    parts.append(f"default={r['default_value']}")
                descriptor = ", ".join(parts) or "column"
            prefix = f"[{entity}]" if entity else ""
            service_lines.append(f"- {prefix}.{name}: {descriptor}")

        elif r["entity_type"] == "graphql_type":
            kind_map = {
                "graphql_type": "ObjectType",
                "graphql_input": "InputType",
                "graphql_interface": "InterfaceType",
                "graphql_args_type": "ArgsType",
            }
            kind = kind_map.get(r.get("gql_kind", ""), "GraphQLType")
            fields = r.get("fields") or []
            fields_str = ", ".join(fields) if fields else "no fields indexed"
            service_lines.append(f"- {name} (@{kind}): {fields_str}")

    return enum_lines, service_lines, function_lines


def get_projects_for_tasks(tasks: list[dict]) -> list[str]:
    if not graph_store.is_available() or not tasks:
        return []
    terms = _extract_terms(tasks)
    try:
        results = graph_store.query_context(terms, project_name=None)
    except Exception:
        return []
    seen: set[str] = set()
    projects: list[str] = []
    for r in results:
        p = r.get("project")
        if p and p not in seen:
            seen.add(p)
            projects.append(p)
    return projects


def get_jira_context(tasks: list[dict], project_name: str | None = None) -> str:

    if not tasks:
        return ""

    terms = _extract_terms(tasks)
    task_text_full = " ".join(
        f"{t.get('title', '')} {t.get('description', '')}" for t in tasks
    )

    sections: list[str] = []

    # 1. Graph query (Neo4j) - enums, services, functions
    graph_results: list[dict] = []
    if graph_store.is_available():
        try:
            graph_results = graph_store.query_context(terms, project_name)
        except Exception:
            pass

    enum_lines, service_lines, function_lines = _format_graph_results(graph_results)
    if enum_lines:
        sections.append("Enums:\n" + "\n".join(enum_lines))
    if service_lines:
        sections.append("Services:\n" + "\n".join(service_lines))
    if function_lines:
        sections.append("Functions:\n" + "\n".join(function_lines))

    # 2. Domain entity context (Neo4j)
    if graph_store.is_available():
        try:
            domain_results = graph_store.query_domain_context(terms, project_name)
            if domain_results:
                domain_lines = []
                for dr in domain_results:
                    svc = dr.get("service") or ""
                    funcs = [f for f in (dr.get("functions") or []) if f]
                    line = f"- {dr['name']}"
                    if svc:
                        line += f" → {svc}"
                    if funcs:
                        line += f" (functions: {', '.join(funcs)})"
                    if dr.get("description"):
                        line += f" — {dr['description']}"
                    domain_lines.append(line)
                sections.append("Domain Entities:\n" + "\n".join(domain_lines))
        except Exception:
            pass

    # 3. Semantic search (Qdrant)
    vector_snippets: list[str] = []
    if vector_index.is_available():
        try:
            vector_snippets = vector_index.search(task_text_full, project_name, top_k=3)
        except Exception:
            pass

    if vector_snippets:
        trimmed = [s[:400] for s in vector_snippets]
        sections.append("Relevant code:\n" + "\n---\n".join(trimmed))

    # 4. Manual entities (MySQL fallback)
    graph_names = {r["name"].lower() for r in graph_results}
    manual_raw = get_manual_context(tasks)
    if manual_raw:
        filtered_manual = [
            line for line in manual_raw.splitlines()
            if line and not any(name in line.lower() for name in graph_names)
        ]
        if filtered_manual:
            sections.append("Additional context:\n" + "\n".join(filtered_manual))

    if not sections:
        return ""

    return "Code Knowledge:\n\n" + "\n\n".join(sections)
