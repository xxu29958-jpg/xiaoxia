"""Exclusive physical/code/comment/blank counts using pinned built-in lexers."""

from pathlib import PurePosixPath

from pygments import lex
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.token import Comment, Literal
from pygments.util import ClassNotFound

from .ownership import CONFIG, SOURCE

LANGUAGES = {
    ".cpp": "C++",
    ".h": "C/C++ header",
    ".cu": "CUDA",
    ".pyx": "Cython",
    ".py": "Python",
    ".pyi": "Python",
    ".vue": "Vue",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".ts": "TypeScript",
    ".tsx": "TSX",
    ".js": "JavaScript",
    ".json": "JSON",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
}


def decode(data: bytes) -> tuple[str | None, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16-bom"
        except UnicodeDecodeError:
            return None, "invalid-utf-16"
    if b"\0" in data:
        return None, "binary"
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return None, "non-utf8-or-binary"


def role(path: str) -> str:
    item = PurePosixPath(path)
    if any(
        part in {"tests", "fixtures", "__tests__", "cli_tests"} for part in item.parts
    ) or item.name.startswith("test_"):
        return "test" if item.suffix in SOURCE else "test_fixture"
    if (
        item.suffix == ".sql"
        or "migrations" in item.parts
        or ("schema" in item.stem and item.suffix in CONFIG)
    ):
        return (
            "schema_migration"
            if item.suffix in SOURCE | CONFIG | {".sql"}
            else "document"
        )
    if item.name.endswith(".lock") or item.name in {"package-lock.json", "uv.lock"}:
        return "generated_manifest"
    if (
        item.suffix in CONFIG
        or item.name
        in {
            ".gitignore",
            ".gitattributes",
            ".python-version",
            ".editorconfig",
            "_redirects",
            "MANIFEST.in",
        }
        or item.parts[0] == "requirements"
    ):
        return "configuration"
    if item.suffix in SOURCE:
        return (
            "tool_script_build"
            if item.parts[0] in {"tools", "scripts", "maintenance"}
            or item.suffix in {".ps1", ".sh", ".bat", ".cmd"}
            else "production"
        )
    if item.suffix in {".html", ".htm"}:
        return "production"
    if item.suffix in {".svg", ".tiktoken", ".jsonl"}:
        return "text_asset"
    return "document"


def measure(path: str, text: str, purpose: str) -> dict:
    suffix = PurePosixPath(path).suffix.lower()
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    result = {
        "physical_lines": len(lines),
        "code_lines": 0,
        "comment_lines": 0,
        "blank_lines": sum(not line.strip() for line in lines),
        "content_lines": 0,
        "language": LANGUAGES.get(suffix, suffix.lstrip(".") or "extensionless"),
    }
    if purpose in {"document", "test_fixture", "text_asset"}:
        result["content_lines"] = len(lines) - result["blank_lines"]
        return result
    if PurePosixPath(path).name in {
        ".gitignore",
        ".gitattributes",
        ".python-version",
        "_redirects",
        "MANIFEST.in",
    } or (
        PurePosixPath(path).parts[0] == "requirements"
        and suffix in {".txt", ".in", ".lock"}
    ):
        result["comment_lines"] = sum(line.startswith("#") for line in lines)
        result["code_lines"] = (
            len(lines) - result["blank_lines"] - result["comment_lines"]
        )
        result["language"] = (
            "Requirements"
            if PurePosixPath(path).parts[0] == "requirements"
            else "Git/build configuration"
        )
        return result
    try:
        override = {".vue": "vue", ".service": "ini"}.get(suffix)
        override = "toml" if PurePosixPath(path).name == "uv.lock" else override
        override = "ini" if PurePosixPath(path).name == ".editorconfig" else override
        lexer = (
            get_lexer_by_name(override, stripnl=False, ensurenl=False)
            if override
            else get_lexer_for_filename(path, stripnl=False, ensurenl=False)
        )
    except ClassNotFound:
        result["line_count_gap"] = "No pinned lexer for this code/configuration type"
        result["content_lines"] = len(lines) - result["blank_lines"]
        return result
    marks: dict[int, set[str]] = {}
    row = 0
    for kind, value in lex("\n".join(lines), lexer):
        category = (
            "comment" if kind in Comment or kind in Literal.String.Doc else "code"
        )
        for index, part in enumerate(value.split("\n")):
            if index:
                row += 1
            if part.strip() and row < len(lines):
                marks.setdefault(row, set()).add(category)
    for number, line in enumerate(lines):
        if line.strip():
            result[
                "code_lines" if "code" in marks.get(number, set()) else "comment_lines"
            ] += 1
    return result


def totals(records: list[dict]) -> dict:
    keys = (
        "bytes",
        "physical_lines",
        "code_lines",
        "comment_lines",
        "blank_lines",
        "content_lines",
    )
    return {
        "files": len(records),
        **{key: sum(r.get(key, 0) for r in records) for key in keys},
    }


def grouped(records: list[dict], axis: str) -> dict:
    return {
        name: totals([r for r in records if r[axis] == name])
        for name in sorted({r[axis] for r in records})
    }
