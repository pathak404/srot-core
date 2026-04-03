
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


_SERVICE_KINDS = frozenset({"service", "controller", "entity", "module", "resolver"})
_GRAPHQL_TYPE_KINDS = frozenset({"graphql_type", "graphql_input", "graphql_interface", "graphql_args_type"})


def _infer_service_for_entity(
    file_path: str,
    file_services: dict[str, list[str]],
    dir_services: dict[str, list[str]],
) -> str | None:
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

    file_services: dict[str, list[str]] = {}
    dir_services: dict[str, list[str]] = {}
    all_service_names: set[str] = set()
    all_graphql_type_names: set[str] = set()
    all_interface_names: set[str] = set()
    all_entity_names: set[str] = set()

    for e in entities:
        etype = e.get("type", "")
        name = e.get("name", "")
        if etype in _SERVICE_KINDS:
            fp = e["file_path"]
            file_services.setdefault(fp, []).append(name)
            dir_services.setdefault(str(Path(fp).parent), []).append(name)
            all_service_names.add(name)
            if etype == "entity":
                all_entity_names.add(name)
        elif etype in _GRAPHQL_TYPE_KINDS:
            all_graphql_type_names.add(name)
        elif etype == "interface":
            all_interface_names.add(name)

    enum_names = [e["name"] for e in entities if e.get("type") == "enum"]

    with driver.session() as session:
        # Clear existing
        session.run("MATCH (n {project: $project}) DETACH DELETE n", project=project_name)

        # Project node
        session.run("MERGE (p:Project {name: $name})", name=project_name)
        node_count += 1

        # Node creation 
        for e in entities:
            etype = e.get("type", "")

            # Service-kind nodes: Injectable / Controller / Resolver / Module / Entity
            if etype in _SERVICE_KINDS:
                session.run(
                    "MERGE (s:Service {name: $name, project: $project}) "
                    "SET s.file_path = $file_path, s.entity_type = $etype, "
                    "s.route_prefix = $route_prefix, s.resolver_type = $resolver_type, "
                    "s.guards = $guards, "
                    "s.module_imports = $mod_imports, "
                    "s.module_providers = $mod_providers, "
                    "s.module_exports = $mod_exports",
                    name=e["name"], project=project_name,
                    file_path=e["file_path"], etype=etype,
                    route_prefix=e.get("route_prefix") or "",
                    resolver_type=e.get("resolver_type") or "",
                    guards=json.dumps(e.get("guards") or []),
                    mod_imports=json.dumps(e.get("imports") or []),
                    mod_providers=json.dumps(e.get("providers") or []),
                    mod_exports=json.dumps(e.get("exports") or []),
                )
                session.run(
                    "MATCH (s:Service {name: $name, project: $project}), (p:Project {name: $project}) "
                    "MERGE (s)-[:PART_OF]->(p)",
                    name=e["name"], project=project_name,
                )
                node_count += 1
                edge_count += 1

            # GraphQL type nodes: ObjectType / InputType / InterfaceType / ArgsType
            elif etype in _GRAPHQL_TYPE_KINDS:
                session.run(
                    "MERGE (g:GraphQLType {name: $name, project: $project}) "
                    "SET g.file_path = $file_path, g.gql_kind = $gql_kind, "
                    "g.guards = $guards",
                    name=e["name"], project=project_name,
                    file_path=e["file_path"],
                    gql_kind=etype,
                    guards=json.dumps(e.get("guards") or []),
                )
                session.run(
                    "MATCH (g:GraphQLType {name: $name, project: $project}), (p:Project {name: $project}) "
                    "MERGE (g)-[:PART_OF]->(p)",
                    name=e["name"], project=project_name,
                )
                node_count += 1
                edge_count += 1

            # GraphQL field nodes
            elif etype == "graphql_field":
                session.run(
                    "MERGE (gf:GraphQLField {name: $name, class_name: $class_name, project: $project}) "
                    "SET gf.file_path = $file_path, gf.graphql_type = $graphql_type",
                    name=e["name"],
                    class_name=e.get("class_name") or "",
                    project=project_name,
                    file_path=e["file_path"],
                    graphql_type=e.get("graphql_type") or "",
                )
                node_count += 1

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
                    "file_path: $file_path, project: $project, "
                    # endpoint guards, GraphQL return type
                    "guards: $guards, return_gql_type: $return_gql_type})",
                    method=e.get("http_method", "GET"),
                    route=e.get("route", "/"),
                    handler=e.get("handler", ""),
                    service=e.get("service", ""),
                    file_path=e["file_path"],
                    project=project_name,
                    guards=json.dumps(e.get("guards") or []),
                    return_gql_type=e.get("return_gql_type") or "",
                )
                node_count += 1

            elif etype == "function":
                session.run(
                    "CREATE (f:Function {name: $name, project: $project, "
                    "file_path: $file_path, class_name: $class_name, "
                    "params: $params, return_type: $return_type, "
                    # method guards, graphql args
                    "guards: $guards, graphql_args: $graphql_args})",
                    name=e["name"],
                    project=project_name,
                    file_path=e["file_path"],
                    class_name=e.get("class_name") or "",
                    params=e.get("params") or "",
                    return_type=e.get("return_type") or "",
                    guards=json.dumps(e.get("guards") or []),
                    graphql_args=json.dumps(e.get("graphql_args") or []),
                )
                node_count += 1

            elif etype == "entity_column":
                session.run(
                    "MERGE (ec:EntityColumn {name: $name, class_name: $class_name, project: $project}) "
                    "SET ec.file_path = $file_path, ec.column_type = $column_type, "
                    "ec.is_primary = $is_primary, ec.nullable = $nullable, "
                    "ec.default_value = $default_value, "
                    "ec.relation_kind = $relation_kind, ec.relation_target = $relation_target",
                    name=e["name"],
                    class_name=e.get("class_name") or "",
                    project=project_name,
                    file_path=e["file_path"],
                    column_type=e.get("column_type") or "",
                    is_primary=e.get("is_primary") or False,
                    nullable=e.get("nullable") or False,
                    default_value=e.get("default_value") or "",
                    relation_kind=e.get("relation_kind") or "",
                    relation_target=e.get("relation_target") or "",
                )
                node_count += 1

        # Relationship creation

        # Enum -> Service (BELONGS_TO)
        for e in entities:
            if e.get("type") == "enum":
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
            if e.get("type") == "endpoint" and e.get("service"):
                session.run(
                    "MATCH (ep:APIEndpoint {handler: $handler, project: $project}), "
                    "(s:Service {name: $svc_name, project: $project}) "
                    "MERGE (ep)-[:BELONGS_TO]->(s)",
                    handler=e.get("handler", ""), svc_name=e["service"], project=project_name,
                )
                edge_count += 1

        # Function -> Service (DEFINED_IN)
        for e in entities:
            if e.get("type") == "function" and e.get("class_name"):
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

        # APIEndpoint -> Function (TRIGGERS)
        for e in entities:
            if e.get("type") == "endpoint" and e.get("handler"):
                session.run(
                    "MATCH (ep:APIEndpoint {handler: $handler, project: $project}), "
                    "(f:Function {name: $handler, project: $project}) "
                    "MERGE (ep)-[:TRIGGERS]->(f)",
                    handler=e["handler"], project=project_name,
                )

        # Function -> Enum (USES)
        for e in entities:
            if e.get("type") == "function" and e.get("source_chunk") and enum_names:
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

        # GraphQLType -> GraphQLField (HAS_FIELD)
        for e in entities:
            if e.get("type") == "graphql_field" and e.get("class_name"):
                session.run(
                    "MATCH (g:GraphQLType {name: $type_name, project: $project}), "
                    "(gf:GraphQLField {name: $field_name, class_name: $class_name, project: $project}) "
                    "MERGE (g)-[:HAS_FIELD]->(gf)",
                    type_name=e["class_name"],
                    field_name=e["name"],
                    class_name=e["class_name"],
                    project=project_name,
                )
                edge_count += 1

        # Entity (Service) -> EntityColumn (HAS_COLUMN)
        for e in entities:
            if e.get("type") == "entity_column" and e.get("class_name"):
                session.run(
                    "MATCH (s:Service {name: $class_name, project: $project, entity_type: 'entity'}), "
                    "(ec:EntityColumn {name: $col_name, class_name: $class_name, project: $project}) "
                    "MERGE (s)-[:HAS_COLUMN]->(ec)",
                    class_name=e["class_name"],
                    col_name=e["name"],
                    project=project_name,
                )
                edge_count += 1

        # EntityColumn -> Entity (REFERENCES) - for @ManyToOne, @OneToMany
        for e in entities:
            if e.get("type") == "entity_column" and e.get("relation_target"):
                if e["relation_target"] in all_entity_names:
                    session.run(
                        "MATCH (ec:EntityColumn {name: $col_name, class_name: $class_name, project: $project}), "
                        "(s:Service {name: $target, project: $project, entity_type: 'entity'}) "
                        "MERGE (ec)-[:REFERENCES]->(s)",
                        col_name=e["name"],
                        class_name=e["class_name"],
                        target=e["relation_target"],
                        project=project_name,
                    )
                    edge_count += 1

        # EXTENDS edges: Service/GraphQLType -> superclass
        for e in entities:
            extends_name = e.get("extends")
            if not extends_name:
                continue
            etype = e.get("type", "")
            if etype in _SERVICE_KINDS:
                src_label = "Service"
            elif etype in _GRAPHQL_TYPE_KINDS:
                src_label = "GraphQLType"
            else:
                continue

            if extends_name in all_service_names:
                session.run(
                    f"MATCH (a:{src_label} {{name: $src, project: $project}}), "
                    f"(b:Service {{name: $target, project: $project}}) "
                    f"MERGE (a)-[:EXTENDS]->(b)",
                    src=e["name"], target=extends_name, project=project_name,
                )
                edge_count += 1
            elif extends_name in all_graphql_type_names:
                session.run(
                    f"MATCH (a:{src_label} {{name: $src, project: $project}}), "
                    f"(b:GraphQLType {{name: $target, project: $project}}) "
                    f"MERGE (a)-[:EXTENDS]->(b)",
                    src=e["name"], target=extends_name, project=project_name,
                )
                edge_count += 1

        # IMPLEMENTS edges: Service/GraphQLType -> Interface
        for e in entities:
            implements_list = e.get("implements") or []
            if not implements_list:
                continue
            etype = e.get("type", "")
            if etype in _SERVICE_KINDS:
                src_label = "Service"
            elif etype in _GRAPHQL_TYPE_KINDS:
                src_label = "GraphQLType"
            else:
                continue

            for iface_name in implements_list:
                if iface_name in all_interface_names:
                    session.run(
                        f"MATCH (a:{src_label} {{name: $src, project: $project}}), "
                        f"(b:Interface {{name: $target, project: $project}}) "
                        f"MERGE (a)-[:IMPLEMENTS]->(b)",
                        src=e["name"], target=iface_name, project=project_name,
                    )
                    edge_count += 1

        # Module PROVIDES -> Service and MODULE_IMPORTS -> Module
        for e in entities:
            if e.get("type") != "module":
                continue
            mod_name = e["name"]
            for provider_name in e.get("providers") or []:
                if provider_name in all_service_names:
                    session.run(
                        "MATCH (m:Service {name: $mod, project: $project}), "
                        "(s:Service {name: $provider, project: $project}) "
                        "MERGE (m)-[:PROVIDES]->(s)",
                        mod=mod_name, provider=provider_name, project=project_name,
                    )
                    edge_count += 1
            for import_entry in e.get("imports") or []:
                # Strip call-expression suffix: TypeOrmModule.forFeature -> TypeOrmModule
                clean_name = import_entry.split(".")[0] if "." in import_entry else import_entry
                if clean_name in all_service_names:
                    session.run(
                        "MATCH (m:Service {name: $mod, project: $project}), "
                        "(s:Service {name: $imported, project: $project}) "
                        "MERGE (m)-[:MODULE_IMPORTS]->(s)",
                        mod=mod_name, imported=clean_name, project=project_name,
                    )
                    edge_count += 1

        # IMPORTS edges: Service -> Service/GraphQLType (from import statements)
        for e in entities:
            if e.get("type") != "import":
                continue
            importer_file = e["file_path"]
            importer_svcs = file_services.get(importer_file) or dir_services.get(
                str(Path(importer_file).parent), []
            )
            for importer_svc in importer_svcs:
                for imported_name in e.get("names") or []:
                    if imported_name == importer_svc:
                        continue
                    if imported_name in all_service_names:
                        session.run(
                            "MATCH (a:Service {name: $src, project: $project}), "
                            "(b:Service {name: $target, project: $project}) "
                            "MERGE (a)-[:IMPORTS]->(b)",
                            src=importer_svc, target=imported_name, project=project_name,
                        )
                        edge_count += 1
                    elif imported_name in all_graphql_type_names:
                        session.run(
                            "MATCH (a:Service {name: $src, project: $project}), "
                            "(b:GraphQLType {name: $target, project: $project}) "
                            "MERGE (a)-[:IMPORTS]->(b)",
                            src=importer_svc, target=imported_name, project=project_name,
                        )
                        edge_count += 1

        # DEPENDS_ON edges: constructor injection (Service -> Service)
        for e in entities:
            if e.get("type") not in _SERVICE_KINDS:
                continue
            for dep_name in e.get("constructor_deps") or []:
                if dep_name in all_service_names and dep_name != e["name"]:
                    session.run(
                        "MATCH (a:Service {name: $src, project: $project}), "
                        "(b:Service {name: $target, project: $project}) "
                        "MERGE (a)-[:DEPENDS_ON]->(b)",
                        src=e["name"], target=dep_name, project=project_name,
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
    gql_filter = "AND g.project = $project" if project_name else ""
    col_filter = "AND ec.project = $project" if project_name else ""

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
            RETURN s.name AS name, s.file_path AS file_path,
                   s.entity_type AS entity_type, s.project AS project,
                   s.route_prefix AS route_prefix, s.resolver_type AS resolver_type,
                   s.guards AS guards
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
                "route_prefix": record["route_prefix"],
                "resolver_type": record["resolver_type"],
                "guards": record["guards"],
            })

        # Functions
        q3 = f"""
            MATCH (f:Function)
            WHERE any(term IN $terms WHERE toLower(f.name) CONTAINS toLower(term))
            {fn_filter}
            OPTIONAL MATCH (f)-[:DEFINED_IN]->(s:Service)
            RETURN f.name AS name, f.params AS params, f.return_type AS return_type,
                   f.file_path AS file_path, f.project AS project, s.name AS service,
                   f.guards AS guards, f.graphql_args AS graphql_args
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
                "guards": record["guards"],
                "graphql_args": record["graphql_args"],
            })

        # GraphQL types (ObjectType / InputType / InterfaceType)
        q4 = f"""
            MATCH (g:GraphQLType)
            WHERE any(term IN $terms WHERE toLower(g.name) CONTAINS toLower(term))
            {gql_filter}
            OPTIONAL MATCH (g)-[:HAS_FIELD]->(gf:GraphQLField)
            RETURN g.name AS name, g.gql_kind AS gql_kind,
                   g.file_path AS file_path, g.project AS project,
                   collect(gf.name + ': ' + gf.graphql_type)[0..10] AS fields
            LIMIT 5
        """
        for record in session.run(q4, terms=terms, project=project_name):
            results.append({
                "entity_type": "graphql_type",
                "name": record["name"],
                "project": record["project"],
                "gql_kind": record["gql_kind"],
                "fields": [f for f in (record["fields"] or []) if f],
                "file_path": record["file_path"],
                "values_json": None,
                "service": None,
            })

        # EntityColumn
        q5 = f"""
            MATCH (ec:EntityColumn)
            WHERE any(term IN $terms WHERE
                toLower(ec.name) CONTAINS toLower(term)
                OR toLower(ec.class_name) CONTAINS toLower(term))
            {col_filter}
            OPTIONAL MATCH (s:Service {{entity_type: 'entity', project: ec.project}})-[:HAS_COLUMN]->(ec)
            OPTIONAL MATCH (ec)-[:REFERENCES]->(target:Service)
            RETURN ec.name AS name, ec.class_name AS class_name,
                   ec.column_type AS column_type, ec.is_primary AS is_primary,
                   ec.nullable AS nullable, ec.default_value AS default_value,
                   ec.relation_kind AS relation_kind, ec.relation_target AS relation_target,
                   ec.file_path AS file_path, ec.project AS project,
                   s.name AS entity_name, target.name AS resolved_target
            LIMIT 15
        """
        for record in session.run(q5, terms=terms, project=project_name):
            results.append({
                "entity_type": "entity_column",
                "name": record["name"],
                "class_name": record["class_name"],
                "project": record["project"],
                "column_type": record["column_type"],
                "is_primary": record["is_primary"],
                "nullable": record["nullable"],
                "default_value": record["default_value"],
                "relation_kind": record["relation_kind"],
                "relation_target": record["relation_target"],
                "entity_name": record["entity_name"],
                "resolved_target": record["resolved_target"],
                "file_path": record["file_path"],
                "values_json": None,
                "service": record["entity_name"],
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
