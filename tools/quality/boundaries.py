"""Static Python dependency contracts and newly introduced cycle paths."""

import ast
from collections import deque
from pathlib import PurePosixPath


def module_name(path: str) -> str:
    name = path.removesuffix(".py").removesuffix(".pyi").replace("/", ".")
    return name.removesuffix(".__init__")


def imported_modules(path: str, text: str) -> set[str]:
    package = module_name(path).split(".")
    if PurePosixPath(path).stem != "__init__":
        package.pop()
    result = set()
    for node in ast.walk(ast.parse(text, filename=path)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else []
            base = ".".join([*prefix, *([node.module] if node.module else [])])
            result.add(base)
            result.update(
                f"{base}.{alias.name}" for alias in node.names if alias.name != "*"
            )
    return result


def within(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def graph(sources: dict[str, bytes]) -> dict[str, set[str]]:
    known = {module_name(path) for path in sources}
    # Plugins are loaded with data/plugins on sys.path in the real bot runtime.
    # Keep the canonical repository name in reports and resolve that actual alias.
    aliases = {
        name.removeprefix("data.plugins."): name
        for name in known
        if name.startswith("data.plugins.")
    }
    result = {}
    for path, data in sources.items():
        source = module_name(path)
        imports = imported_modules(path, data.decode("utf-8-sig"))
        edges = set()
        for item in imports:
            while item and item not in known and item not in aliases:
                item = item.rpartition(".")[0]
            item = aliases.get(item, item)
            if item and item != source:
                edges.add(item)
        result[source] = edges
    return result


def route(edges: dict[str, set[str]], start: str, target: str) -> list[str] | None:
    queue = deque([(start, [start])])
    visited = {start}
    while queue:
        current, path = queue.popleft()
        if current == target:
            return path
        for destination in sorted(edges.get(current, set()) - visited):
            visited.add(destination)
            queue.append((destination, [*path, destination]))
    return None


def violations(sources: dict[str, bytes], contracts: list[dict]) -> list[dict]:
    result = []
    for path, data in sources.items():
        source = module_name(path)
        imports = imported_modules(path, data.decode("utf-8-sig"))
        for contract in contracts:
            if not within(source, contract["source"]):
                continue
            for target in contract["forbidden"]:
                if any(within(item, target) for item in imports):
                    result.append(
                        {
                            "rule": contract["id"],
                            "path": path,
                            "source": source,
                            "target": target,
                        }
                    )
    return result


def new_cycles(
    before: dict[str, set[str]], after: dict[str, set[str]]
) -> list[list[str]]:
    cycles = []
    for source, targets in sorted(after.items()):
        for target in sorted(targets - before.get(source, set())):
            found = route(after, target, source)
            if found:
                cycles.append([source, *found])
    return cycles
