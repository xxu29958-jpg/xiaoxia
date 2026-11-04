"""EQ1: collect a versioned inventory of a committed tree, without imports/I/O side effects."""

import argparse
import copy
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath

from . import architecture_report, complexity_report, file_map, line_counts, trends
from . import upstream as upstream_sources
from .frontend import EXTENSIONS
from .git_snapshot import BlobReader, Entry, entries, git, revision
from .metrics import physical_lines, python_metrics
from .ownership import file_kind, findings, responsibility

VERSION = 2


def provenance(policy: dict) -> dict:
    scanner = hashlib.sha256()
    for source in sorted(
        p for p in Path(__file__).parent.iterdir() if p.suffix in {".py", ".cjs"}
    ):
        scanner.update(source.name.encode())
        scanner.update(source.read_bytes().replace(b"\r\n", b"\n"))
    return {
        "schema_version": VERSION,
        "python": platform.python_version(),
        "node": subprocess.check_output(
            ["node", "--version"], text=True, timeout=10
        ).strip(),
        "git": subprocess.check_output(
            ["git", "--version"], text=True, timeout=10
        ).strip(),
        "ruff": importlib.metadata.version("ruff"),
        "pygments": importlib.metadata.version("pygments"),
        "typescript": "5.1.6",
        "vue_compiler_sfc": "3.3.4",
        "scanner_sha256": scanner.hexdigest(),
        "rules_sha256": hashlib.sha256(
            json.dumps(policy, sort_keys=True).encode()
        ).hexdigest(),
        "rules_version": policy.get("measurement_version", "legacy"),
    }


def file_record(
    entry: Entry, data: bytes | None, policy: dict, upstream: dict
) -> tuple[dict, bytes | None]:
    record = {
        "path": entry.path,
        "blob": entry.oid,
        "bytes": entry.size,
        "object_kind": entry.kind,
        "kind": file_kind(entry.path),
        **responsibility(entry.path, entry.oid, policy, upstream),
        "role": line_counts.role(entry.path),
        "language": "binary",
    }
    source = None
    if data is not None:
        text, encoding = line_counts.decode(data)
        record["encoding"] = encoding
        if text is None:
            record["role"] = "binary_asset"
        else:
            suffix = PurePosixPath(entry.path).suffix
            measure_text(record, text.encode("utf-8"), suffix)
            record.update(line_counts.measure(entry.path, text, record["role"]))
            if suffix in {".py", ".pyi"} and "parse_error" not in record:
                source = text.encode("utf-8")
            elif suffix in EXTENSIONS:
                source = text.encode("utf-8")
    record["category"] = category(record)
    record["first_party_production"] = (
        record["role"] == "production"
        and record["maintenance"] == "self_maintained"
        and (
            record["origin"] != "vendor"
            or "source_blob" in record
            or record.get("verification") == "not_present_in_pinned_source"
        )
    )
    record["findings"] = findings(record, policy["thresholds"])
    return record, source


def collect(
    repo: Path, ref: str, policy: dict, upstream: dict, previous: dict | None = None
) -> dict:
    commit = revision(repo, ref)
    records = []
    sources = {}
    measurement = provenance(policy)
    cached = (
        {r["path"]: r for r in previous["files"]}
        if previous and previous.get("measurement") == measurement
        else {}
    )
    remeasured = set()
    declared_policy = policy
    with BlobReader(repo) as reader:
        for entry in entries(repo, commit):
            data = reader.read(entry.oid) if entry.kind == "blob" else None
            if entry.path == "quality/policy.json" and data is not None:
                declared_policy = json.loads(data)
            record, source = reuse_or_measure(
                entry, data, policy, upstream, cached, remeasured
            )
            if source is not None:
                sources[entry.path] = source
            records.append(record)
    values = complexity_report.measure(
        {
            path: data.decode("utf-8")
            for path, data in sources.items()
            if path in remeasured and path.endswith((".py", ".pyi"))
        }
    )
    for record in records:
        for symbol in record.get("symbols", []):
            if symbol["definition_line"] in values.get(record["path"], {}):
                symbol["complexity"] = values[record["path"]][symbol["definition_line"]]
    as_of = git(repo, "show", "-s", "--format=%cI", commit).decode().strip()[:10]
    influence = file_map.attach(
        records,
        sources,
        {**policy, "exceptions": declared_policy.get("exceptions", [])},
        as_of,
    )
    maintained = [r for r in records if r["first_party_production"]]
    return {
        "schema_version": VERSION,
        "commit": commit,
        "tree": git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip(),
        "measurement_python": platform.python_version(),
        "measurement": measurement,
        "declared_policy_sha256": hashlib.sha256(
            json.dumps(declared_policy, sort_keys=True).encode()
        ).hexdigest(),
        "incremental": {
            "reused_file_metrics": len(records) - len(remeasured),
            "remeasured_file_metrics": len(remeasured),
        },
        "scope": "committed Git tree only; ignored runtime assets measured separately",
        "upstream_comparison_available": bool(upstream),
        "files": records,
        "summary": summarize(records),
        "scale": {
            "Repository Total": line_counts.totals(records),
            "First-party Production Total": line_counts.totals(maintained),
            "categories": line_counts.grouped(records, "category"),
            "roles": line_counts.grouped(records, "role"),
            "languages": line_counts.grouped(records, "language"),
            "production_domains": line_counts.grouped(maintained, "domain"),
        },
        "complexity": {
            "repository": complexity_report.summarize(records),
            "first_party_production": complexity_report.summarize(maintained),
        },
        "influence": influence,
        "architecture": architecture_report.summarize(records, policy),
        "history": trends.history(repo, commit, records),
        "method": {
            "lines": "physical = code + comment-only + blank + non-code content; mixed code/comment lines count as code; Python docstrings count as comments",
            "production": "self-maintained executable source including verified vendor modifications; excludes tools/tests/config/schema and unchanged upstream; maintenance is not authorship",
            "upstream_limit": "Unpinned external plugin origins retain explicit unverified provenance; no size exemption",
            "symbol_totals": "Overlapping function/class spans are never summed into repository LOC",
            "complexity": "Ruff 0.15.22 C901 with report threshold zero; enforcement retains policy thresholds",
        },
    }


def reuse_or_measure(
    entry: Entry,
    data: bytes | None,
    policy: dict,
    upstream: dict,
    cached: dict,
    remeasured: set,
) -> tuple[dict, bytes | None]:
    saved = cached.get(entry.path)
    if (
        saved
        and saved["blob"] == entry.oid
        and saved.get("source_blob") == upstream.get(entry.path)
    ):
        record = copy.deepcopy(saved)
        source = None
        if (
            data is not None
            and (PurePosixPath(entry.path).suffix in EXTENSIONS | {".py", ".pyi"})
            and "parse_error" not in record
        ):
            text, _ = line_counts.decode(data)
            if text is not None:
                source = text.encode("utf-8")
        return record, source
    remeasured.add(entry.path)
    return file_record(entry, data, policy, upstream)


def category(record: dict) -> str:
    if record["origin"] == "vendor":
        origin = (
            "vendor_unmodified"
            if record["size_exempt"]
            else "vendor_modified"
            if "source_blob" in record
            else "vendor_added_or_unverified"
        )
    elif record["maintenance"] == "upstream_unmodified":
        origin = "upstream_unmodified"
    elif record["origin"] == "upstream":
        origin = (
            "upstream_unverified" if "verification" in record else "upstream_modified"
        )
    else:
        origin = "project"
    return f"{origin}/{record['role']}"


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
        "parse_errors": [
            r["path"] for r in records if "parse_error" in r or r.get("parse_errors")
        ],
        "encoding_errors": [r["path"] for r in records if "encoding_error" in r],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--policy", type=Path, default=Path("quality/policy.json"))
    parser.add_argument("--upstream-repo", type=Path)
    parser.add_argument("--upstream-cache", type=Path)
    parser.add_argument("--base")
    parser.add_argument("--consumers")
    parser.add_argument(
        "--previous-report",
        type=Path,
        help="Reuse same-blob measurements only when tool and rule fingerprints match",
    )
    parser.add_argument(
        "--query-report",
        type=Path,
        help="Query an existing machine-readable result without rescanning",
    )
    args = parser.parse_args()
    if args.query_report:
        if not args.consumers:
            parser.error("--query-report requires --consumers")
        print(
            json.dumps(
                file_map.query(
                    json.loads(args.query_report.read_text(encoding="utf-8")),
                    args.consumers,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    policy_bytes = git(args.repo, "show", f"{args.revision}:{args.policy.as_posix()}")
    policy = json.loads(policy_bytes)
    upstream = {}
    comparisons = upstream_sources.sources(args.repo, args.revision, policy)
    if args.upstream_cache:
        upstream = upstream_sources.index(args.upstream_cache, comparisons)
    if args.upstream_repo:
        upstream.update(
            {
                e.path: e.oid
                for e in entries(
                    args.upstream_repo, policy["astrbot_upstream"]["commit"]
                )
            }
        )
    cached = (
        json.loads(args.previous_report.read_text(encoding="utf-8"))
        if args.previous_report
        else None
    )
    before = (
        collect(args.repo, args.base, policy, upstream, previous=cached)
        if args.base
        else None
    )
    report = collect(
        args.repo, args.revision, policy, upstream, previous=before or cached
    )
    report["policy_sha256"] = hashlib.sha256(policy_bytes).hexdigest()
    report["astrbot_upstream"] = policy["astrbot_upstream"]
    report["source_comparisons"] = (
        comparisons
        if args.upstream_cache
        else [policy["astrbot_upstream"]]
        if args.upstream_repo
        else []
    )
    if before:
        report["delta"] = trends.delta(args.repo, before, report)
    if args.consumers:
        report = file_map.query(report, args.consumers)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
