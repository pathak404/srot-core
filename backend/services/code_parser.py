
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
}


_HTTP_DECORATORS = {"Get", "Post", "Put", "Delete", "Patch", "Options", "Head"}

_GRAPHQL_DECORATORS = {"Query", "Mutation", "Subscription"}


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


def _extract_string_arg(call_node, source: bytes) -> str | None:
    args = call_node.child_by_field_name("arguments")
    if args:
        for child in args.children:
            if child.type in ("string", "template_string"):
                raw = _node_text(child, source)
                return raw.strip("'\"` ")
    return None


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


def _parse_file(file_path: str) -> list[dict]:
    parser = _get_parser()
    source = Path(file_path).read_bytes()
    tree = parser.parse(source)
    entities: list[dict] = []

    # To track nested class contexts
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

        # Class (service / controller / entity / plain class)
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
            source_chunk = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:1500]
            entity = {
                "type": entity_type,
                "name": name,
                "decorators": decorators,
                "file_path": file_path,
                "source_chunk": source_chunk,
            }
            entities.append(entity)
            class_stack.append(entity)

        # Interface
        elif node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                entities.append({
                    "type": "interface",
                    "name": _node_text(name_node, source),
                    "file_path": file_path,
                })

        # Method definition (inside a class) -> function + optional endpoint
        elif node.type == "method_definition" and class_stack:
            current_class = class_stack[-1]
            key_node = node.child_by_field_name("name")
            if not key_node:
                return
            fn_name = _node_text(key_node, source)
            if fn_name == "constructor":
                return

            params_node = node.child_by_field_name("parameters")
            params = _extract_params(params_node, source) if params_node else ""
            return_type = _extract_return_type(node, source)
            source_chunk = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:1500]
            entities.append({
                "type": "function",
                "name": fn_name,
                "class_name": current_class["name"],
                "params": params,
                "return_type": return_type,
                "source_chunk": source_chunk,
                "file_path": file_path,
            })

            cls_type = current_class.get("type")

            # REST endpoint
            if cls_type == "controller":
                method_decorators: list[str] = []
                route_paths: list[str] = []
                for child in node.children:
                    if child.type == "decorator":
                        for sub in child.children:
                            if sub.type == "call_expression":
                                func = sub.child_by_field_name("function")
                                if func:
                                    dec_name = _node_text(func, source)
                                    method_decorators.append(dec_name)
                                    if dec_name in _HTTP_DECORATORS:
                                        path = _extract_string_arg(sub, source)
                                        route_paths.append(path or "/")
                for i, http_dec in enumerate(method_decorators):
                    if http_dec in _HTTP_DECORATORS:
                        entities.append({
                            "type": "endpoint",
                            "http_method": http_dec.upper(),
                            "route": route_paths[i] if i < len(route_paths) else "/",
                            "handler": fn_name,
                            "service": current_class["name"],
                            "file_path": file_path,
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
                                        entities.append({
                                            "type": "endpoint",
                                            "http_method": dec_name.upper(),  # QUERY / MUTATION / SUBSCRIPTION
                                            "route": fn_name,                 # operation name = method name
                                            "handler": fn_name,
                                            "service": current_class["name"],
                                            "file_path": file_path,
                                        })

        # Standalone function declaration
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
