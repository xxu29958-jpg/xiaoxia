"""Provenance and maintenance responsibility are separate from file paths."""

from pathlib import PurePosixPath

SOURCE = {
    ".py",
    ".pyi",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".mjs",
    ".cjs",
    ".vue",
    ".css",
    ".scss",
    ".ps1",
    ".sh",
}
CONFIG = {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".xml"}
DOCUMENT = {".md", ".rst", ".txt", ".csv", ".mmd"}
TEXT = SOURCE | CONFIG | DOCUMENT | {".html", ".lock", ".svg"}


def file_kind(name: str) -> str:
    path = PurePosixPath(name)
    if any(p in {"tests", "fixtures", "__tests__", "cli_tests"} for p in path.parts):
        return "test" if path.suffix in SOURCE else "fixture"
    if path.suffix in SOURCE:
        return "production"
    if path.suffix in CONFIG:
        return "configuration"
    if path.suffix in DOCUMENT:
        return "document"
    return "asset"


def responsibility(name: str, oid: str, policy: dict, upstream: dict) -> dict:
    """Only an exact upstream blob match earns an unmodified exemption."""
    if name in upstream:
        unchanged = oid == upstream[name]
        return {
            "origin": "upstream",
            "maintenance": "upstream_unmodified" if unchanged else "self_maintained",
            "source_blob": upstream[name],
            "size_exempt": unchanged,
        }
    for scope in policy["origins"]:
        if name.startswith(scope["prefix"]):
            return {
                "origin": scope["origin"],
                "maintenance": "self_maintained",
                "source": scope["source"],
                "verification": "local_or_unverified_delta",
                "size_exempt": False,
            }
    return {"origin": "project", "maintenance": "self_maintained", "size_exempt": False}


def findings(record: dict, limits: dict) -> list[dict]:
    from .metrics import threshold

    result = []
    kind = record["kind"]
    checks = {
        "production": "production_file_lines",
        "test": "test_file_lines",
        "configuration": "manual_configuration_lines",
        "document": "manual_document_lines",
    }
    metric = checks.get(kind)
    if metric and "lines" in record:
        level = threshold(record["lines"], limits[metric])
        if level:
            result.append({"metric": metric, "value": record["lines"], "level": level})
    if kind == "production":
        for symbol in record.get("symbols", []):
            for metric, value in (
                (symbol["kind"] + "_lines", symbol["lines"]),
                ("control_nesting", symbol["control_nesting"]),
            ):
                level = threshold(value, limits[metric])
                if level:
                    result.append(
                        {
                            "metric": metric,
                            "symbol": symbol["name"],
                            "line": symbol["start"],
                            "value": value,
                            "level": level,
                        }
                    )
    return result
