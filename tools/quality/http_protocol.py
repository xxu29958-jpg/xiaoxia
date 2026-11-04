"""Source-only HTTP service consumers; defaults are candidates, not live traffic."""

import ast
from collections import defaultdict
from urllib.parse import urlsplit

from .shared_state import expression, import_bindings

SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
CLIENTS = {
    "requests",
    "requests.Session()",
    "aiohttp.ClientSession()",
    "httpx",
    "httpx.Client()",
    "httpx.AsyncClient()",
}
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "request"}


class Bindings:
    """Resolve single direct bindings; ambiguous writes and parameters stay unknown."""

    def __init__(self, path: str, tree: ast.Module):
        self.tree = tree
        self.parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        self.import_candidates = import_bindings(path, tree)
        self.values = defaultdict(list)
        self.writes = defaultdict(list)
        for node in ast.walk(tree):
            scope = self.scope(node)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports = import_bindings(
                    path, ast.Module(body=[node], type_ignores=[])
                )
                for name, target in imports.items():
                    self.writes[scope, name].append(node)
                    self.values[scope, name].append(target)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.writes[scope, node.name].append(node)
            if isinstance(node, ast.arg):
                self.writes[scope, node.arg].append(node)
            elif isinstance(node, (ast.Name, ast.Attribute)) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                self.writes[scope, expression(node)].append(node)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    self.values[scope, expression(target)].append(node.value)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        self.values[scope, expression(item.optional_vars)].append(
                            item.context_expr
                        )
        self.patches = {name for _, name in self.writes if "." in name}

    def scope(self, node: ast.AST) -> ast.AST:
        while node is not self.tree:
            node = self.parents[node]
            if isinstance(node, SCOPES):
                return node
        return self.tree

    def binding(
        self, name: str, scope: ast.AST
    ) -> tuple[ast.AST | str | None, ast.AST]:
        key = (scope, name)
        if key in self.writes:
            values = self.values.get(key, [])
            value = values[0] if len(values) == len(self.writes[key]) == 1 else None
            return value, scope
        if scope is not self.tree:
            parent = self.scope(scope)
            # Method bodies do not inherit the class namespace.
            if isinstance(parent, ast.ClassDef):
                parent = self.scope(parent)
            return self.binding(name, parent)
        return None, scope

    def resolve(self, node: ast.AST | None, scope: ast.AST, seen=frozenset()) -> str:
        if node is None:
            return ""
        name = expression(node)
        if name:
            first, dot, tail = name.partition(".")
            if first in seen or any(
                name == p or name.startswith(p + ".") for p in self.patches
            ):
                return ""
            value, owner = self.binding(first, scope)
            base = (
                value
                if isinstance(value, str)
                else self.resolve(value, owner, seen | {first})
                if value is not None
                else ""
            )
            return base + dot + tail if base else ""
        if isinstance(node, ast.Call):
            target = self.resolve(node.func, scope, seen)
            if target + "()" in CLIENTS | {"fastapi.FastAPI()"}:
                return target + "()"
        return ""

    def text(
        self, node: ast.AST | None, scope: ast.AST, seen=frozenset()
    ) -> tuple[str, bool]:
        if node is None:
            return "", False
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value, False
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, a = self.text(node.left, scope, seen)
            right, b = self.text(node.right, scope, seen)
            return (left + right, a or b) if left and right else ("", a or b)
        if isinstance(node, ast.Call):
            return self.call_text(node, scope, seen)
        name = expression(node)
        if name and name not in seen:
            value, owner = self.binding(name, scope)
            if isinstance(value, ast.AST):
                return self.text(value, owner, seen | {name})
        return "", False

    def call_text(
        self, node: ast.Call, scope: ast.AST, seen: frozenset
    ) -> tuple[str, bool]:
        if not isinstance(node.func, ast.Attribute):
            return "", False
        if node.func.attr == "get" and len(node.args) == 2:
            value, _ = self.text(node.args[1], scope, seen)
            return value, True  # A supplied default is not the runtime configuration.
        if node.func.attr == "rstrip" and len(node.args) == 1:
            if isinstance(node.args[0], ast.Constant) and node.args[0].value == "/":
                value, default = self.text(node.func.value, scope, seen)
                return value.rstrip("/"), default
        return "", False

    def possible_clients(self) -> set[str]:
        result = {
            name
            for name, target in self.import_candidates.items()
            if target in {"requests", "httpx"}
        }
        for (scope, name), values in self.values.items():
            if any(
                isinstance(v, ast.Call)
                and self.possible_name(v.func) + "()" in CLIENTS
                or isinstance(v, ast.AST)
                and self.resolve(v, scope) in CLIENTS
                for v in values
            ):
                result.add(name)
        return result

    def possible_name(self, node: ast.AST) -> str:
        first, dot, tail = expression(node).partition(".")
        imported = self.import_candidates.get(first)
        return imported + dot + tail if imported else ""


def origin(url: str) -> dict:
    try:
        value = urlsplit(url)
        if value.scheme not in {"http", "https"} or not value.hostname:
            return {}
        host = value.hostname
        return {
            "host": "loopback" if host in {"localhost", "127.0.0.1", "::1"} else host,
            "port": value.port
            if value.port is not None
            else (443 if value.scheme == "https" else 80),
            "scheme": value.scheme,
            "path": value.path or "/",
        }
    except ValueError:
        return {}


def request_site(node: ast.Call, bindings: Bindings, scope: ast.AST) -> dict:
    target = bindings.resolve(node.func, scope)
    owner, _, method = target.rpartition(".")
    if owner not in CLIENTS or method not in METHODS:
        return {}
    index = 1 if method == "request" else 0
    url_node = (
        node.args[index]
        if len(node.args) > index
        else next((k.value for k in node.keywords if k.arg == "url"), None)
    )
    url, default = (
        bindings.text(url_node, scope) if url_node is not None else ("", False)
    )
    method_node = (
        node.args[0]
        if node.args
        else next((k.value for k in node.keywords if k.arg == "method"), None)
    )
    method_text = (
        bindings.text(method_node, scope)[0] if method == "request" else method
    )
    return {
        "line": node.lineno,
        "method": method_text.upper() or "unknown",
        "configured_default": default,
        **origin(url),
    }


def listener_site(node: ast.Call, bindings: Bindings, scope: ast.AST) -> dict:
    if bindings.resolve(node.func, scope) != "uvicorn.run":
        return (
            {"line": node.lineno, "unresolved": "listener name is rebound or shadowed"}
            if bindings.possible_name(node.func) == "uvicorn.run"
            else {}
        )
    keywords = {k.arg: k.value for k in node.keywords}
    app = node.args[0] if node.args else keywords.get("app")
    host = keywords.get("host")
    port = keywords.get("port")
    if not isinstance(host, ast.Constant) or not isinstance(port, ast.Constant):
        return {"line": node.lineno, "unresolved": "nonliteral listener address"}
    if (
        not isinstance(host.value, str)
        or type(port.value) is not int
        or port.value == 0
    ):
        return {
            "line": node.lineno,
            "unresolved": "listener address is not a fixed host and port",
        }
    if (
        app is None
        or bindings.resolve(app, scope) != "fastapi.FastAPI()"
        or not isinstance(port.value, int)
    ):
        return {
            "line": node.lineno,
            "unresolved": "application binding is not a direct FastAPI instance",
        }
    if "ssl_keyfile" in keywords or "ssl_certfile" in keywords:
        return {
            "line": node.lineno,
            "unresolved": "TLS listener requires deployment configuration",
        }
    host_text = f"[{host.value}]" if ":" in str(host.value) else str(host.value)
    address = origin(f"http://{host_text}:{port.value}")
    if not address:
        return {"line": node.lineno, "unresolved": "listener address is invalid"}
    return {
        "line": node.lineno,
        "app": expression(app),
        **address,
    }


def facts(path: str, tree: ast.Module) -> dict:
    imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    imports += [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and not node.level
    ]
    if not any(
        name
        and name.split(".")[0] in {"requests", "aiohttp", "httpx", "fastapi", "uvicorn"}
        for name in imports
    ):
        return {}
    bindings = Bindings(path, tree)
    possible = bindings.possible_clients()
    clients, listeners, routes, gaps = [], [], [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        scope = bindings.scope(node)
        if client := request_site(node, bindings, scope):
            clients.append(client)
        elif isinstance(node.func, ast.Attribute) and node.func.attr in METHODS:
            if expression(node.func.value) in possible:
                gaps.append(
                    {
                        "kind": "http_binding",
                        "line": node.lineno,
                        "reason": "client name is rebound, shadowed or indirect",
                    }
                )
        if listener := listener_site(node, bindings, scope):
            listeners.append(listener)
        name = bindings.resolve(node.func, scope)
        owner, _, method = name.rpartition(".")
        if owner == "fastapi.FastAPI()" and method in METHODS and node.args:
            route, _ = bindings.text(node.args[0], scope)
            routes.append(
                {"line": node.lineno, "method": method.upper(), "path": route}
            )
    return {
        "http_clients": clients,
        "http_listeners": listeners,
        "http_routes": routes,
        "http_binding_gaps": gaps,
    }


def matches(client: dict, listener: dict) -> bool:
    if not client.get("host") or not listener.get("host"):
        return False
    host = listener["host"]
    return (client["scheme"], client["port"]) == (
        listener["scheme"],
        listener["port"],
    ) and (
        client["host"] == host
        or client["host"] == "loopback"
        and host in {"0.0.0.0", "::"}
    )


def extend_impact(graph: dict, records: dict) -> None:
    listeners = [
        (p, site) for p, r in records.items() for site in r.get("http_listeners", [])
    ]
    for path, record in records.items():
        relations, gaps = [], list(record.get("http_binding_gaps", []))
        gaps.extend(
            {"kind": "http_listener", **site}
            for site in record.get("http_listeners", [])
            if site.get("unresolved")
        )
        for client in record.get("http_clients", []):
            targets = [(p, s) for p, s in listeners if p != path and matches(client, s)]
            if not client.get("host"):
                gaps.append(
                    {
                        "kind": "http_target",
                        "line": client["line"],
                        "reason": "request target is not statically resolved",
                    }
                )
            if len(targets) > 1:
                gaps.append(
                    {
                        "kind": "http_target",
                        "line": client["line"],
                        "reason": "multiple source listeners share this address",
                        "candidates": sorted({p for p, _ in targets}),
                    }
                )
            for server, listener in targets:
                relations.append(
                    {"server": server, "request": client, "listener": listener}
                )
        record["protocol_dependencies"] = sorted({r["server"] for r in relations})
        record["protocol_relations"] = relations
        record.setdefault("dynamic_gaps", []).extend(gaps)
        graph.setdefault(path, set()).update(record["protocol_dependencies"])


def uncertainties(routes: dict, records: dict) -> dict:
    # Unknown clients can otherwise disappear when only their server changes.
    servers = any(records.get(p, {}).get("http_listeners") for p in routes)
    return {
        p: gaps
        for p, r in records.items()
        if (
            gaps := [
                g for g in r.get("dynamic_gaps", []) if g["kind"].startswith("http_")
            ]
        )
        and (p in routes or servers)
    }
