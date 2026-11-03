"""EQ1: collect a versioned inventory of a committed tree, without imports/I/O side effects."""

import argparse
import hashlib
import json
import platform
from collections import Counter
from pathlib import Path, PurePosixPath

from .git_snapshot import BlobReader, entries, git, revision
from .metrics import physical_lines, python_metrics
from .ownership import TEXT, file_kind, findings, responsibility

VERSION = 1


def collect(repo: Path, ref: str, policy: dict, upstream: dict) -> dict:
    commit = revision(repo, ref)
    records = []
    with BlobReader(repo) as reader:
        for entry in entries(repo, commit):
            record = {
                "path": entry.path,
                "blob": entry.oid,
                "bytes": entry.size,
                "object_kind": entry.kind,
                "kind": file_kind(entry.path),
                **responsibility(entry.path, entry.oid, policy, upstream),
            }
            suffix = PurePosixPath(entry.path).suffix
            if entry.kind == "blob" and suffix in TEXT:
                measure_text(record, reader.read(entry.oid), suffix)
            record["findings"] = findings(record, policy["thresholds"])
            records.append(record)
    return {
        "schema_version": VERSION,
        "commit": commit,
        "tree": git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip(),
        "measurement_python": platform.python_version(),
        "scope": "committed Git tree only; ignored runtime assets measured separately",
        "upstream_comparison_available": bool(upstream),
        "files": records,
        "summary": summarize(records),
    }


def measure_text(record: dict, data: bytes, suffix: str) -> None:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        record["encoding_error"] = {"offset": error.start, "reason": error.reason}
        return
    record["lines"] = physical_lines(text)
    if suffix in {".py", ".pyi"}:
        record.update(python_metrics(text, record["path"]))


def summarize(records: list[dict]) -> dict:
    maintained = [r for r in records if not r["size_exempt"]]
    return {
        "file_count": len(records),
        "tracked_bytes": sum(r["bytes"] for r in records),
        "kinds": dict(sorted(Counter(r["kind"] for r in records).items())),
        "maintenance": dict(sorted(Counter(r["maintenance"] for r in records).items())),
        "origins": dict(sorted(Counter(r["origin"] for r in records).items())),
        "maintained_files_with_findings": sum(bool(r["findings"]) for r in maintained),
        "maintained_review_findings": sum(
            f["level"] == "review" for r in maintained for f in r["findings"]
        ),
        "parse_errors": [r["path"] for r in records if "parse_error" in r],
        "encoding_errors": [r["path"] for r in records if "encoding_error" in r],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--policy", type=Path, default=Path("quality/policy.json"))
    parser.add_argument("--upstream-repo", type=Path)
    args = parser.parse_args()
    policy_bytes = args.policy.read_bytes()
    policy = json.loads(policy_bytes)
    upstream = {}
    if args.upstream_repo:
        upstream = {
            e.path: e.oid
            for e in entries(args.upstream_repo, policy["astrbot_upstream"]["commit"])
        }
    report = collect(args.repo, args.revision, policy, upstream)
    report["policy_sha256"] = hashlib.sha256(policy_bytes).hexdigest()
    report["astrbot_upstream"] = policy["astrbot_upstream"]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
