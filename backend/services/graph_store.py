
import json
import os
from pathlib import Path

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def is_available() -> bool:
    try:
        driver = _get_driver()
        driver.verify_connectivity()
        return True
    except Exception:
        return False


def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None


# Build

def _infer_service_for_entity(
    file_path: str,
    file_services: dict[str, list[str]],
    dir_services: dict[str, list[str]],
) -> str | None:
    # Find the nearest service for an enum: same file first, then same directory (1 level)
    same_file = file_services.get(file_path)
    if same_file:
        return same_file[0]
    parent = str(Path(file_path).parent)
    same_dir = dir_services.get(parent)
    if same_dir:
        return same_dir[0]
    return None


def build_graph(project_name: str, entities: list[dict]) -> tuple[int, int]:
    driver = _get_driver()
    node_count = 0
    edge_count = 0

    # Lookup maps
    file_services: dict[str, list[str]] = {}   # file_path -> service names in that file
    dir_services: dict[str, list[str]] = {}    # directory -> service names in that dir
    for e in entities:
        if e["type"] in ("service", "controller", "entity", "module", "resolver"):
            fp = e["file_path"]
            file_services.setdefault(fp, []).append(e["name"])
            d = str(Path(fp).parent)
            dir_services.setdefault(d, []).append(e["name"])

    # Enum name set for USES edge detection
    enum_names = [e["name"] for e in entities if e["type"] == "enum"]

    with driver.session() as session:
        # Clear existing project data
        session.run("MATCH (n {project: $project}) DETACH DELETE n", project=project_name)

        # Project node
        session.run("MERGE (p:Project {name: $name})", name=project_name)
        node_count += 1

        # Create all entity nodes

        for e in entities:
            etype = e["type"]

            if etype in ("service", "controller", "entity", "module", "resolver"):
                session.run(
                    "MERGE (s:Service {name: $name, project: $project}) "
                    "SET s.file_path = $file_path, s.entity_type = $etype",
                    name=e["name"], project=project_name,
                    file_path=e["file_path"], etype=etype,
                )
                session.run(
                    "MATCH (s:Service {name: $name, project: $project}), (p:Project {name: $project}) "
                    "MERGE (s)-[:PART_OF]->(p)",
                    name=e["name"], project=project_name,
                )
                node_count += 1
                edge_count += 1

            elif etype == "enum":
                session.run(
                    "CREATE (en:Enum {name: $name, project: $project, "
                    "file_path: $file_path, values_json: $values_json})",
                    name=e["name"], project=project_name,
                    file_path=e["file_path"],
                    values_json=json.dumps(e.get("values", [])),
                )
                node_count += 1

            elif etype == "interface":
                session.run(
                    "MERGE (i:Interface {name: $name, project: $project}) "
                    "SET i.file_path = $file_path",
                    name=e["name"], project=project_name, file_path=e["file_path"],
                )
                node_count += 1

            elif etype == "endpoint":
                session.run(
                    "CREATE (ep:APIEndpoint {http_method: $method, route: $route, "
                    "handler: $handler, service: $service, "
                    "file_path: $file_path, project: $project})",
                    method=e.get("http_method", "GET"),
                    route=e.get("route", "/"),
                    handler=e.get("handler", ""),
                    service=e.get("service", ""),
                    file_path=e["file_path"],
                    project=project_name,
                )
                node_count += 1

            elif etype == "function":
                session.run(
                    "CREATE (f:Function {name: $name, project: $project, "
                    "file_path: $file_path, class_name: $class_name, "
                    "params: $params, return_type: $return_type})",
                    name=e["name"],
                    project=project_name,
                    file_path=e["file_path"],
                    class_name=e.get("class_name") or "",
                    params=e.get("params") or "",
                    return_type=e.get("return_type") or "",
                )
                node_count += 1

        # Relationships

        # Enum -> Service (BELONGS_TO) - fixed: same-file first, then same-dir
        for e in entities:
            if e["type"] == "enum":
                svc = _infer_service_for_entity(e["file_path"], file_services, dir_services)
                if svc:
                    session.run(
                        "MATCH (en:Enum {name: $enum_name, project: $project}), "
                        "(s:Service {name: $svc_name, project: $project}) "
                        "MERGE (en)-[:BELONGS_TO]->(s)",
                        enum_name=e["name"], svc_name=svc, project=project_name,
                    )
                    edge_count += 1

        # APIEndpoint -> Service (BELONGS_TO)
        for e in entities:
            if e["type"] == "endpoint" and e.get("service"):
                session.run(
                    "MATCH (ep:APIEndpoint {handler: $handler, project: $project}), "
                    "(s:Service {name: $svc_name, project: $project}) "
                    "MERGE (ep)-[:BELONGS_TO]->(s)",
                    handler=e.get("handler", ""), svc_name=e["service"], project=project_name,
                )
                edge_count += 1

        # Function -> Service (DEFINED_IN)
        for e in entities:
            if e["type"] == "function" and e.get("class_name"):
                session.run(
                    "MATCH (f:Function {name: $fn_name, project: $project, file_path: $file_path}), "
                    "(s:Service {name: $svc_name, project: $project}) "
                    "MERGE (f)-[:DEFINED_IN]->(s)",
                    fn_name=e["name"],
                    project=project_name,
                    file_path=e["file_path"],
                    svc_name=e["class_name"],
                )
                edge_count += 1

        # APIEndpoint -> Function (TRIGGERS) - endpoint handler name matches function name
        for e in entities:
            if e["type"] == "endpoint" and e.get("handler"):
                session.run(
                    "MATCH (ep:APIEndpoint {handler: $handler, project: $project}), "
                    "(f:Function {name: $handler, project: $project}) "
                    "MERGE (ep)-[:TRIGGERS]->(f)",
                    handler=e["handler"], project=project_name,
                )

        # Function -> Enum (USES) - enum name appears in function source_chunk
        for e in entities:
            if e["type"] == "function" and e.get("source_chunk") and enum_names:
                fn_source = e["source_chunk"]
                for enum_name in enum_names:
                    if enum_name in fn_source:
                        session.run(
                            "MATCH (f:Function {name: $fn_name, project: $project, file_path: $file_path}), "
                            "(en:Enum {name: $enum_name, project: $project}) "
                            "MERGE (f)-[:USES]->(en)",
                            fn_name=e["name"],
                            project=project_name,
                            file_path=e["file_path"],
                            enum_name=enum_name,
                        )
                        edge_count += 1

    return node_count, edge_count


# DomainEntity operations

def create_domain_entity(project_name: str, name: str, description: str = "") -> None:
    driver = _get_driver()
    with driver.session() as session:
        session.run(
            "MERGE (d:DomainEntity {name: $name, project: $project}) "
            "SET d.description = $description",
            name=name, project=project_name, description=description or "",
        )


def delete_domain_entity_node(project_name: str, name: str) -> None:
    driver = _get_driver()
    with driver.session() as session:
        session.run(
            "MATCH (d:DomainEntity {name: $name, project: $project}) DETACH DELETE d",
            name=name, project=project_name,
        )


def link_domain_entity(
    project_name: str,
    domain_name: str,
    service_name: str,
    rel_type: str = "HANDLES",
) -> None:
    valid_types = {"HANDLES", "EXPOSES", "IMPLEMENTS"}
    if rel_type not in valid_types:
        rel_type = "HANDLES"
    driver = _get_driver()
    with driver.session() as session:
        session.run(
            f"MATCH (s:Service {{name: $svc, project: $project}}), "
            f"(d:DomainEntity {{name: $domain, project: $project}}) "
            f"MERGE (s)-[:{rel_type}]->(d)",
            svc=service_name, domain=domain_name, project=project_name,
        )


def get_domain_entities(project_name: str | None = None) -> list[dict]:
    try:
        driver = _get_driver()
        project_filter = "WHERE d.project = $project" if project_name else ""
        with driver.session() as session:
            q = f"""
                MATCH (d:DomainEntity)
                {project_filter}
                OPTIONAL MATCH (s:Service)-[]->(d)
                RETURN d.name AS name, d.project AS project, d.description AS description,
                       collect(s.name) AS linked_services
                LIMIT 100
            """
            return [
                {
                    "name": r["name"],
                    "project": r["project"],
                    "description": r["description"],
                    "linked_services": [s for s in r["linked_services"] if s],
                }
                for r in session.run(q, project=project_name)
            ]
    except Exception:
        return []


def get_services(project_name: str) -> list[str]:
    try:
        driver = _get_driver()
        with driver.session() as session:
            records = session.run(
                "MATCH (s:Service {project: $project}) RETURN s.name AS name ORDER BY s.name",
                project=project_name,
            )
            return [r["name"] for r in records]
    except Exception:
        return []


# Query

def query_context(terms: list[str], project_name: str | None = None) -> list[dict]:
    if not terms:
        return []
    try:
        driver = _get_driver()
    except Exception:
        return []

    results = []
    project_filter = "AND en.project = $project" if project_name else ""
    svc_filter = "AND s.project = $project" if project_name else ""
    fn_filter = "AND f.project = $project" if project_name else ""

    with driver.session() as session:
        # Enums
        q = f"""
            MATCH (en:Enum)
            WHERE any(term IN $terms WHERE toLower(en.name) CONTAINS toLower(term))
            {project_filter}
            OPTIONAL MATCH (en)-[:BELONGS_TO]->(s:Service)
            RETURN en.name AS name, en.values_json AS values_json,
                   en.file_path AS file_path, en.project AS project, s.name AS service
            LIMIT 10
        """
        for record in session.run(q, terms=terms, project=project_name):
            results.append({
                "entity_type": "enum",
                "name": record["name"],
                "project": record["project"],
                "service": record["service"],
                "values_json": record["values_json"],
                "file_path": record["file_path"],
            })

        # Services
        q2 = f"""
            MATCH (s:Service)
            WHERE any(term IN $terms WHERE toLower(s.name) CONTAINS toLower(term))
            {svc_filter}
            RETURN s.name AS name, s.file_path AS file_path, s.entity_type AS entity_type, s.project AS project
            LIMIT 5
        """
        for record in session.run(q2, terms=terms, project=project_name):
            results.append({
                "entity_type": "service",
                "name": record["name"],
                "project": record["project"],
                "service": None,
                "values_json": None,
                "file_path": record["file_path"],
            })

        # Functions
        q3 = f"""
            MATCH (f:Function)
            WHERE any(term IN $terms WHERE toLower(f.name) CONTAINS toLower(term))
            {fn_filter}
            OPTIONAL MATCH (f)-[:DEFINED_IN]->(s:Service)
            RETURN f.name AS name, f.params AS params, f.return_type AS return_type,
                   f.file_path AS file_path, f.project AS project, s.name AS service
            LIMIT 10
        """
        for record in session.run(q3, terms=terms, project=project_name):
            results.append({
                "entity_type": "function",
                "name": record["name"],
                "params": record["params"],
                "return_type": record["return_type"],
                "project": record["project"],
                "service": record["service"],
                "values_json": None,
                "file_path": record["file_path"],
            })

    return results


def query_domain_context(terms: list[str], project_name: str | None = None) -> list[dict]:
    if not terms:
        return []
    try:
        driver = _get_driver()
    except Exception:
        return []

    project_filter = "AND d.project = $project" if project_name else ""
    results = []
    with driver.session() as session:
        q = f"""
            MATCH (d:DomainEntity)
            WHERE any(term IN $terms WHERE toLower(d.name) CONTAINS toLower(term))
            {project_filter}
            OPTIONAL MATCH (s:Service)-[]->(d)
            OPTIONAL MATCH (f:Function)-[:DEFINED_IN]->(s)
            RETURN d.name AS domain, s.name AS service,
                   collect(DISTINCT f.name)[0..5] AS functions,
                   d.description AS description
            LIMIT 5
        """
        for record in session.run(q, terms=terms, project=project_name):
            results.append({
                "entity_type": "domain",
                "name": record["domain"],
                "service": record["service"],
                "functions": [f for f in (record["functions"] or []) if f],
                "description": record["description"],
            })
    return results


def get_all_projects() -> list[dict]:
    try:
        driver = _get_driver()
        with driver.session() as session:
            records = session.run(
                "MATCH (p:Project) "
                "OPTIONAL MATCH (n {project: p.name}) "
                "RETURN p.name AS name, count(n) AS node_count"
            )
            return [{"name": r["name"], "node_count": r["node_count"]} for r in records]
    except Exception:
        return []
