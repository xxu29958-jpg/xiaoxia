"""Asset integrity and targeted secret checks. Findings never echo secret values."""

import hashlib
import json
import re
from pathlib import Path

SECRET_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(
        rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{70,})\b"
    ),
    "openai_style_key": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b"),
}


def secret_findings(name: str, data: bytes) -> list[dict]:
    result = []
    for rule, pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(data):
            result.append(
                {
                    "path": name,
                    "rule": rule,
                    "line": data[: match.start()].count(b"\n") + 1,
                }
            )
    if name.endswith((".key", ".pem")) or Path(name).name in {
        ".env",
        "xiaoxia.local.json",
    }:
        result.append({"path": name, "rule": "runtime_secret_file"})
    return result


def verify_reference_bundle(root: Path) -> list[dict]:
    base = root / "docs/reference/v1.3"
    manifest = base / "MANIFEST.sha256.json"
    if not manifest.is_file():
        return [
            {
                "path": str(manifest.relative_to(root)),
                "rule": "missing_reference_manifest",
            }
        ]
    records = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    issues = []
    expected = {record["path"] for record in records} | {"MANIFEST.sha256.json"}
    for file in base.rglob("*"):
        if file.is_file() and file.relative_to(base).as_posix() not in expected:
            issues.append(
                {
                    "path": file.relative_to(base).as_posix(),
                    "rule": "unexpected_reference_file",
                }
            )
    for record in records:
        target = base / record["path"]
        if not target.resolve().is_relative_to(base.resolve()):
            issues.append(
                {"path": record["path"], "rule": "reference_path_outside_bundle"}
            )
            continue
        if not target.is_file() or target.is_symlink():
            issues.append({"path": record["path"], "rule": "missing_reference_file"})
            continue
        data = target.read_bytes()
        if (
            len(data) != record["bytes"]
            or hashlib.sha256(data).hexdigest() != record["sha256"]
        ):
            issues.append({"path": record["path"], "rule": "reference_hash_mismatch"})
    return issues


def asset_findings(name: str, data: bytes, before: bytes | None) -> list[dict]:
    previous_size = len(before) if before else 0
    if len(data) > 10 * 1024 * 1024 and len(data) > previous_size:
        return [{"path": name, "rule": "asset_requires_review", "bytes": len(data)}]
    return []
