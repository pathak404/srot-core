
import json
from pathlib import Path

_parser = None
_ts_language = None


def _get_parser():
    global _parser, _ts_language
    if _parser is None:
        import tree_sitter_typescript as tsts
        from tree_sitter import Language, Parser
        _ts_language = Language(tsts.language_typescript())
        _parser = Parser(_ts_language)
    return _parser


_DECORATOR_TYPE_MAP = {
    "Injectable": "service",
    "Controller": "controller",
    "Entity": "entity",
    "Module": "module",
    "Resolver": "resolver",
    "ObjectType": "graphql_type",
    "InputType": "graphql_input",
    "InterfaceType": "graphql_interface",
    "ArgsType": "graphql_args_type",
}

_GRAPHQL_TYPE_KINDS = frozenset({"graphql_type", "graphql_input", "graphql_interface", "graphql_args_type"})
_SERVICE_KINDS = frozenset({"service", "controller", "entity", "module", "resolver"})


_TYPEORM_COLUMN_DECORATORS = frozenset({
    "Column", "PrimaryColumn", "PrimaryGeneratedColumn",
    "CreateDateColumn", "UpdateDateColumn", "DeleteDateColumn", "VersionColumn",
})
_TYPEORM_RELATION_DECORATORS = frozenset({
    "ManyToOne", "OneToMany", "OneToOne", "ManyToMany",
})
_TYPEORM_COLUMN_ALL = _TYPEORM_COLUMN_DECORATORS | _TYPEORM_RELATION_DECORATORS

_HTTP_DECORATORS = {"Get", "Post", "Put", "Delete", "Patch", "Options", "Head"}
_GRAPHQL_DECORATORS = {"Query", "Mutation", "Subscription", "ResolveField"}

_GUARD_DECORATORS = {"UseGuards", "UseInterceptors", "UseFilters", "UsePipes", "Roles", "SetMetadata"}

_GRAPHQL_PARAM_DECORATORS = {"Args", "Parent", "Context", "Info"}


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def _visit(node, on_enter, on_exit=None):
    on_enter(node)
    for child in node.children:
        _visit(child, on_enter, on_exit)
    if on_exit:
        on_exit(node)


def _extract_decorator_names(class_node, source: bytes) -> list[str]:
    names = []
    for child in class_node.children:
        if child.type == "decorator":
            for sub in child.children:
                if sub.type == "call_expression":
                    func = sub.child_by_field_name("function")
                    if func:
                        names.append(_node_text(func, source))
                elif sub.type == "identifier":
                    names.append(_node_text(sub, source))
    return names


def _extract_decorator_arg(call_node, source: bytes) -> str | None:
    args = call_node.child_by_field_name("arguments")
    if not args:
        return None
    for child in args.children:
        if child.type in ("string", "template_string"):
            return _node_text(child, source).strip("'\"` ")
        if child.type == "arrow_function":
            body = child.child_by_field_name("body")
            if body:
                return _node_text(body, source).strip()
    return None


def _extract_string_arg(call_node, source: bytes) -> str | None:
    args = call_node.child_by_field_name("arguments")
    if args:
        for child in args.children:
            if child.type in ("string", "template_string"):
                raw = _node_text(child, source)
                return raw.strip("'\"` ")
    return None


def _extract_column_options(call_node, source: bytes) -> dict:
    result = {}
    args = call_node.child_by_field_name("arguments")
    if not args:
        return result
    for child in args.children:
        if child.type != "object":
            continue
        for prop in child.children:
            if prop.type != "pair":
                continue
            key_node = prop.child_by_field_name("key")
            val_node = prop.child_by_field_name("value")
            if not key_node or not val_node:
                continue
            key = _node_text(key_node, source).strip("'\"")
            if key not in ("type", "default", "nullable", "unique", "length"):
                continue
            raw_val = _node_text(val_node, source).strip()
            if val_node.type in ("string", "template_string"):
                raw_val = raw_val.strip("'\"` ")
            result[key] = raw_val
        break  # first object arg only
    return result


def _extract_params(params_node, source: bytes) -> str:
    if not params_node:
        return ""
    param_texts = []
    for child in params_node.children:
        if child.type in ("required_parameter", "optional_parameter", "rest_parameter"):
            text = _node_text(child, source)
            if len(text) > 60:
                text = text[:57] + "..."
            param_texts.append(text)
    return ", ".join(param_texts)


def _extract_return_type(node, source: bytes) -> str:
    for child in node.children:
        if child.type == "type_annotation":
            return _node_text(child, source).lstrip(":").strip()
    return ""


def _extract_class_heritage(class_node, source: bytes) -> tuple[str | None, list[str]]:
    extends_class = None
    implements_list = []
    for child in class_node.children:
        if child.type == "class_heritage":
            for h in child.children:
                if h.type == "extends_clause":
                    for id_node in h.children:
                        if id_node.type in ("identifier", "type_identifier"):
                            extends_class = _node_text(id_node, source)
                            break
                        elif id_node.type == "generic_type":
                            for gn in id_node.children:
                                if gn.type in ("identifier", "type_identifier"):
                                    extends_class = _node_text(gn, source)
                                    break
                            break
                elif h.type == "implements_clause":
                    for id_node in h.children:
                        if id_node.type in ("identifier", "type_identifier"):
                            name = _node_text(id_node, source)
                            if name not in (",",):
                                implements_list.append(name)
                        elif id_node.type == "generic_type":
                            for gn in id_node.children:
                                if gn.type in ("identifier", "type_identifier"):
                                    implements_list.append(_node_text(gn, source))
                                    break
    return extends_class, implements_list


def _extract_module_metadata(class_node, source: bytes) -> dict:
    meta: dict = {"imports": [], "providers": [], "exports": []}
    for child in class_node.children:
        if child.type == "decorator":
            for sub in child.children:
                if sub.type == "call_expression":
                    fn = sub.child_by_field_name("function")
                    if fn and _node_text(fn, source) == "Module":
                        args_node = sub.child_by_field_name("arguments")
                        if args_node:
                            for arg in args_node.children:
                                if arg.type == "object":
                                    _collect_module_arrays(arg, source, meta)
    return meta


def _collect_module_arrays(obj_node, source: bytes, target: dict) -> None:
    for prop in obj_node.children:
        if prop.type == "pair":
            key_node = prop.child_by_field_name("key")
            val_node = prop.child_by_field_name("value")
            if not key_node or not val_node:
                continue
            key = _node_text(key_node, source).strip("'\"")
            if key in target and val_node.type == "array":
                for item in val_node.children:
                    if item.type in ("identifier", "type_identifier"):
                        name = _node_text(item, source)
                        if name not in (",", "[", "]"):
                            target[key].append(name)
                    elif item.type == "call_expression":
                        fn = item.child_by_field_name("function")
                        if fn:
                            target[key].append(_node_text(fn, source))


def _extract_graphql_fields(class_node, source: bytes, class_name: str, file_path: str) -> list[dict]:
    fields = []
    body = class_node.child_by_field_name("body")
    if not body:
        return fields

    pending_field_decorator = False
    pending_gql_type = ""
    for member in body.children:
        if member.type == "decorator":
            for sub in member.children:
                if sub.type == "call_expression":
                    fn = sub.child_by_field_name("function")
                    if fn and _node_text(fn, source) == "Field":
                        pending_field_decorator = True
                        arg = _extract_decorator_arg(sub, source)
                        pending_gql_type = arg or ""
                elif sub.type == "identifier" and _node_text(sub, source) == "Field":
                    pending_field_decorator = True
                    pending_gql_type = ""
            continue

        if member.type in ("public_field_definition", "field_definition", "property_signature"):
            has_field = pending_field_decorator
            field_gql_type = pending_gql_type
            pending_field_decorator = False
            pending_gql_type = ""

            for child in member.children:
                if child.type == "decorator":
                    for sub in child.children:
                        if sub.type == "call_expression":
                            fn = sub.child_by_field_name("function")
                            if fn and _node_text(fn, source) == "Field":
                                has_field = True
                                arg = _extract_decorator_arg(sub, source)
                                if arg:
                                    field_gql_type = arg
                        elif sub.type == "identifier" and _node_text(sub, source) == "Field":
                            has_field = True

            if has_field:
                name_node = member.child_by_field_name("name")
                if not field_gql_type:
                    field_gql_type = _extract_return_type(member, source)
                if name_node:
                    fields.append({
                        "type": "graphql_field",
                        "name": _node_text(name_node, source),
                        "class_name": class_name,
                        "graphql_type": field_gql_type,
                        "file_path": file_path,
                    })
        else:
            pending_field_decorator = False
            pending_gql_type = ""

    return fields


def _extract_entity_columns(class_node, source: bytes, class_name: str, file_path: str) -> list[dict]:
    columns = []
    body = class_node.child_by_field_name("body")
    if not body:
        return columns

    pending: dict = {}

    def _reset():
        pending.clear()

    def _process_dec(sub, dec_name: str) -> None:
        pending["found"] = True

        if dec_name in ("PrimaryGeneratedColumn", "PrimaryColumn"):
            pending["is_primary"] = True
            if sub.type == "call_expression":
                s = _extract_string_arg(sub, source)
                if s:
                    pending["column_type"] = s

        elif dec_name == "Column":
            if sub.type == "call_expression":
                s = _extract_string_arg(sub, source)
                if s:
                    pending["column_type"] = s
                else:
                    opts = _extract_column_options(sub, source)
                    if opts.get("type"):
                        pending["column_type"] = opts["type"]
                    if "nullable" in opts:
                        pending["nullable"] = opts["nullable"].lower() not in ("false", "0")
                    if "default" in opts:
                        pending["default_value"] = opts["default"]

        elif dec_name in _TYPEORM_RELATION_DECORATORS:
            pending["relation_kind"] = dec_name
            if sub.type == "call_expression":
                target = _extract_decorator_arg(sub, source)
                if target:
                    pending["relation_target"] = target

        elif dec_name in ("CreateDateColumn", "UpdateDateColumn", "DeleteDateColumn"):
            pending["column_type"] = "timestamp"
        elif dec_name == "VersionColumn":
            pending["column_type"] = "int"

    def _scan_decorator(dec_node) -> bool:
        found = False
        for sub in dec_node.children:
            if sub.type == "call_expression":
                fn = sub.child_by_field_name("function")
                if fn:
                    dec_name = _node_text(fn, source)
                    if dec_name in _TYPEORM_COLUMN_ALL:
                        _process_dec(sub, dec_name)
                        found = True
            elif sub.type == "identifier":
                dec_name = _node_text(sub, source)
                if dec_name in _TYPEORM_COLUMN_ALL:
                    _process_dec(sub, dec_name)
                    found = True
        return found

    for member in body.children:
        if member.type == "decorator":
            _scan_decorator(member)
            continue

        if member.type in ("public_field_definition", "field_definition", "property_signature"):
            has_col = bool(pending.get("found"))
            col_type = pending.get("column_type", "")
            is_primary = pending.get("is_primary", False)
            nullable = pending.get("nullable", False)
            default_val = pending.get("default_value", "")
            relation_kind = pending.get("relation_kind", "")
            relation_target = pending.get("relation_target", "")
            _reset()

            for child in member.children:
                if child.type == "decorator":
                    if _scan_decorator(child):
                        has_col = True
                        col_type = pending.get("column_type", col_type)
                        is_primary = pending.get("is_primary", is_primary)
                        nullable = pending.get("nullable", nullable)
                        default_val = pending.get("default_value", default_val)
                        relation_kind = pending.get("relation_kind", relation_kind)
                        relation_target = pending.get("relation_target", relation_target)
                        _reset()

            if has_col:
                name_node = member.child_by_field_name("name")
                if not col_type and not relation_kind:
                    col_type = _extract_return_type(member, source)
                if name_node:
                    columns.append({
                        "type": "entity_column",
                        "name": _node_text(name_node, source),
                        "class_name": class_name,
                        "file_path": file_path,
                        "column_type": col_type,
                        "is_primary": is_primary,
                        "nullable": nullable,
                        "default_value": default_val,
                        "relation_kind": relation_kind,
                        "relation_target": relation_target,
                    })
        else:
            _reset()

    return columns


def _extract_class_guards(class_node, source: bytes) -> list[str]:
    guards = []
    for child in class_node.children:
        if child.type == "decorator":
            for sub in child.children:
                if sub.type == "call_expression":
                    fn = sub.child_by_field_name("function")
                    if fn:
                        dec_name = _node_text(fn, source)
                        if dec_name in _GUARD_DECORATORS:
                            args = sub.child_by_field_name("arguments")
                            arg_names = []
                            if args:
                                for a in args.children:
                                    if a.type in ("identifier", "type_identifier"):
                                        arg_names.append(_node_text(a, source))
                            guards.append(
                                f"{dec_name}({', '.join(arg_names)})" if arg_names else dec_name
                            )
    return guards


def _extract_method_guards(method_node, source: bytes) -> list[str]:
    guards = []
    for child in method_node.children:
        if child.type == "decorator":
            for sub in child.children:
                if sub.type == "call_expression":
                    fn = sub.child_by_field_name("function")
                    if fn:
                        dec_name = _node_text(fn, source)
                        if dec_name in _GUARD_DECORATORS:
                            args = sub.child_by_field_name("arguments")
                            arg_names = []
                            if args:
                                for a in args.children:
                                    if a.type in ("identifier", "type_identifier"):
                                        arg_names.append(_node_text(a, source))
                            guards.append(
                                f"{dec_name}({', '.join(arg_names)})" if arg_names else dec_name
                            )
    return guards


def _extract_graphql_params(params_node, source: bytes) -> list[dict]:
    gql_params = []
    if not params_node:
        return gql_params
    for param in params_node.children:
        if param.type in ("required_parameter", "optional_parameter", "rest_parameter"):
            for child in param.children:
                if child.type == "decorator":
                    for sub in child.children:
                        if sub.type == "call_expression":
                            fn = sub.child_by_field_name("function")
                            if fn:
                                dec = _node_text(fn, source)
                                if dec in _GRAPHQL_PARAM_DECORATORS:
                                    arg_name = _extract_string_arg(sub, source)
                                    gql_params.append({"decorator": dec, "arg_name": arg_name})
                        elif sub.type == "identifier":
                            dec = _node_text(sub, source)
                            if dec in _GRAPHQL_PARAM_DECORATORS:
                                gql_params.append({"decorator": dec, "arg_name": None})
    return gql_params


def _extract_imports(root_node, source: bytes, file_path: str) -> list[dict]:
    imports = []
    for node in root_node.children:
        if node.type == "import_statement":
            from_path = None
            imported_names = []
            for child in node.children:
                if child.type in ("string", "template_string"):
                    raw = _node_text(child, source).strip("'\"` ")
                    if raw.startswith("."):  # only relative (project-internal) imports
                        from_path = raw
                elif child.type == "import_clause":
                    for sub in child.children:
                        if sub.type == "named_imports":
                            for item in sub.children:
                                if item.type == "import_specifier":
                                    name_node = item.child_by_field_name("name")
                                    if name_node:
                                        imported_names.append(_node_text(name_node, source))
                        elif sub.type in ("identifier", "type_identifier"):
                            imported_names.append(_node_text(sub, source))
            if from_path and imported_names:
                imports.append({
                    "type": "import",
                    "from_path": from_path,
                    "names": imported_names,
                    "file_path": file_path,
                })
    return imports


def _parse_file(file_path: str) -> list[dict]:
    parser = _get_parser()
    source = Path(file_path).read_bytes()
    tree = parser.parse(source)
    entities: list[dict] = []

    entities.extend(_extract_imports(tree.root_node, source, file_path))

    class_stack: list[dict] = []

    def handle_node(node):
        # Enum
        if node.type == "enum_declaration":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            name = _node_text(name_node, source)
            values: list[dict] = []
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "enum_assignment":
                        k = child.child_by_field_name("name")
                        v = child.child_by_field_name("value")
                        if k:
                            values.append({
                                "name": _node_text(k, source),
                                "value": _node_text(v, source) if v else str(len(values)),
                            })
                    elif child.type in ("property_identifier", "identifier"):
                        text = _node_text(child, source)
                        if text not in (",", "{", "}"):
                            values.append({"name": text, "value": str(len(values))})
            source_chunk = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:1500]
            entities.append({
                "type": "enum",
                "name": name,
                "values": values,
                "file_path": file_path,
                "source_chunk": source_chunk,
            })

        # Class
        elif node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            name = _node_text(name_node, source)
            decorators = _extract_decorator_names(node, source)
            entity_type = "class"
            for dec in decorators:
                dec_base = dec.split("(")[0]
                if dec_base in _DECORATOR_TYPE_MAP:
                    entity_type = _DECORATOR_TYPE_MAP[dec_base]
                    break

            route_prefix: str | None = None
            resolver_type: str | None = None
            for child in node.children:
                if child.type == "decorator":
                    for sub in child.children:
                        if sub.type == "call_expression":
                            fn = sub.child_by_field_name("function")
                            if fn:
                                dec_name = _node_text(fn, source)
                                if dec_name == "Controller" and route_prefix is None:
                                    route_prefix = _extract_decorator_arg(sub, source)
                                elif dec_name == "Resolver" and resolver_type is None:
                                    resolver_type = _extract_decorator_arg(sub, source)

            class_guards = _extract_class_guards(node, source)

            module_meta: dict = {}
            if entity_type == "module":
                module_meta = _extract_module_metadata(node, source)

            extends_class, implements_list = _extract_class_heritage(node, source)

            source_chunk = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:1500]
            entity: dict = {
                "type": entity_type,
                "name": name,
                "decorators": decorators,
                "file_path": file_path,
                "source_chunk": source_chunk,
                "route_prefix": route_prefix,
                "resolver_type": resolver_type,
                "guards": class_guards,
                "extends": extends_class,
                "implements": implements_list,
                **module_meta,
            }
            entities.append(entity)
            class_stack.append(entity)

            # Extract @Field properties for GraphQL type classes
            if entity_type in _GRAPHQL_TYPE_KINDS:
                entities.extend(_extract_graphql_fields(node, source, name, file_path))

            # Extract @Column / @PrimaryGeneratedColumn / @ManyToOne for TypeORM entities
            if entity_type == "entity":
                entities.extend(_extract_entity_columns(node, source, name, file_path))

        # Interface
        elif node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                entities.append({
                    "type": "interface",
                    "name": _node_text(name_node, source),
                    "file_path": file_path,
                })

        # Method definition
        elif node.type == "method_definition" and class_stack:
            current_class = class_stack[-1]
            key_node = node.child_by_field_name("name")
            if not key_node:
                return
            fn_name = _node_text(key_node, source)

            # Constructor injection
            if fn_name == "constructor":
                params_node = node.child_by_field_name("parameters")
                if params_node:
                    deps: list[str] = []
                    for param in params_node.children:
                        if param.type in ("required_parameter", "optional_parameter"):
                            type_ann = _extract_return_type(param, source)
                            if type_ann:
                                # Strip generic params: Repository<User> -> Repository
                                base = type_ann.split("<")[0].strip()
                                if base and " " not in base and "." not in base and "[" not in base:
                                    deps.append(base)
                    if deps:
                        current_class.setdefault("constructor_deps", []).extend(deps)
                return

            params_node = node.child_by_field_name("parameters")
            params = _extract_params(params_node, source) if params_node else ""
            return_type = _extract_return_type(node, source)
            source_chunk = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:1500]

            method_guards = _extract_method_guards(node, source)

            gql_params = _extract_graphql_params(params_node, source) if params_node else []

            entities.append({
                "type": "function",
                "name": fn_name,
                "class_name": current_class["name"],
                "params": params,
                "return_type": return_type,
                "source_chunk": source_chunk,
                "file_path": file_path,
                "guards": method_guards,
                "graphql_args": gql_params,
            })

            cls_type = current_class.get("type")

            if cls_type == "controller":
                method_decs: list[str] = []
                route_paths: list[str] = []
                endpoint_guards: list[str] = list(method_guards)
                for child in node.children:
                    if child.type == "decorator":
                        for sub in child.children:
                            if sub.type == "call_expression":
                                func = sub.child_by_field_name("function")
                                if func:
                                    dec_name = _node_text(func, source)
                                    method_decs.append(dec_name)
                                    if dec_name in _HTTP_DECORATORS:
                                        # Combine controller prefix + method path
                                        path = _extract_string_arg(sub, source) or "/"
                                        prefix = current_class.get("route_prefix") or ""
                                        full = f"/{prefix.strip('/')}/{path.strip('/')}".rstrip("/") or "/"
                                        route_paths.append(full)
                for i, http_dec in enumerate(method_decs):
                    if http_dec in _HTTP_DECORATORS:
                        entities.append({
                            "type": "endpoint",
                            "http_method": http_dec.upper(),
                            "route": route_paths[i] if i < len(route_paths) else "/",
                            "handler": fn_name,
                            "service": current_class["name"],
                            "file_path": file_path,
                            "guards": endpoint_guards,
                        })

            # GraphQL endpoint 
            elif cls_type == "resolver":
                for child in node.children:
                    if child.type == "decorator":
                        for sub in child.children:
                            if sub.type == "call_expression":
                                func = sub.child_by_field_name("function")
                                if func:
                                    dec_name = _node_text(func, source)
                                    if dec_name in _GRAPHQL_DECORATORS:
                                        return_gql_type = _extract_decorator_arg(sub, source)
                                        entities.append({
                                            "type": "endpoint",
                                            "http_method": dec_name.upper(),
                                            "route": fn_name,
                                            "handler": fn_name,
                                            "service": current_class["name"],
                                            "file_path": file_path,
                                            "guards": list(method_guards),
                                            "return_gql_type": return_gql_type,
                                        })

        # Function declaration 
        elif node.type == "function_declaration" and not class_stack:
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            fn_name = _node_text(name_node, source)
            params_node = node.child_by_field_name("parameters")
            params = _extract_params(params_node, source) if params_node else ""
            return_type = _extract_return_type(node, source)
            source_chunk = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:1500]
            entities.append({
                "type": "function",
                "name": fn_name,
                "class_name": None,
                "params": params,
                "return_type": return_type,
                "source_chunk": source_chunk,
                "file_path": file_path,
                "guards": [],
                "graphql_args": [],
            })

    def handle_exit(node):
        if node.type == "class_declaration" and class_stack:
            class_stack.pop()

    _visit(tree.root_node, handle_node, handle_exit)
    return entities


def parse_project(root_path: str) -> list[dict]:
    root = Path(root_path)
    SKIP_DIRS = {"node_modules", "dist", "build", ".git", ".next", "coverage", "__pycache__"}
    entities: list[dict] = []

    for ts_file in root.rglob("*.ts"):
        if any(skip in ts_file.parts for skip in SKIP_DIRS):
            continue
        try:
            entities.extend(_parse_file(str(ts_file)))
        except Exception:
            pass

    for tsx_file in root.rglob("*.tsx"):
        if any(skip in tsx_file.parts for skip in SKIP_DIRS):
            continue
        try:
            entities.extend(_parse_file(str(tsx_file)))
        except Exception:
            pass

    return entities
