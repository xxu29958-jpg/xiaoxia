"""Cloud CI entry: plan, execute, provision or deliver an exact source candidate."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from . import upstream
from .ci_checks import check
from .git_snapshot import git, revision
from .impact import matrix, plan


def resolve_base(repo: Path) -> str:
    value = os.environ.get("BASE_SHA", "")
    if not value or set(value) == {"0"}:
        value = "HEAD^"
    return revision(repo, value)


def package(repo: Path, base: str, output: Path) -> dict:
    head = revision(repo, "HEAD")
    archive = output / f"xiaoxia-{head}.zip"
    subprocess.run(
        ["git", "archive", "--format=zip", "--output", str(archive.resolve()), head],
        cwd=repo,
        check=True,
    )
    return {
        "status": "pass",
        "head": head,
        "base": base,
        "tree": git(repo, "rev-parse", f"{head}^{{tree}}").decode().strip(),
        "archive": archive.name,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "kind": "source_candidate",
        "production_deployed": False,
        "g3": "not_executed",
    }


def install(repo: Path, group: str) -> None:
    if group == "frontend_contracts":
        npx = "npx.cmd" if os.name == "nt" else "npx"
        subprocess.run(
            [
                npx,
                "--yes",
                "pnpm@10.15.1",
                "--dir",
                "dashboard",
                "install",
                "--frozen-lockfile",
                "--ignore-scripts",
            ],
            cwd=repo,
            check=True,
        )
        return
    if group in {
        "performance_contracts",
        "cognition_contracts",
        "reading_contracts",
        "registration_contracts",
        "runtime_bot",
    }:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements/bot.txt"],
            cwd=repo,
            check=True,
        )
        if group in {"reading_contracts", "cognition_contracts"}:
            npm = "npm.cmd" if os.name == "nt" else "npm"
            subprocess.run(
                [
                    npm,
                    "ci",
                    "--prefix",
                    "maintenance/browser",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                ],
                cwd=repo,
                check=True,
            )
    elif group in {"media_pipeline_contracts", "runtime_singing"}:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                "requirements/ci-signal.txt",
            ],
            cwd=repo,
            check=True,
        )
    elif group == "runtime_gateway":
        raise RuntimeError(
            "GPU runtime type checks require an explicitly provisioned matching worker environment"
        )


def publish_plan(report: dict, payload: bytes) -> None:
    """Bind the job matrix and downstream artifact to the bytes actually written."""
    output = os.environ.get("GITHUB_OUTPUT")
    if report["status"] != "planned" or not output:
        return
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(
            f"matrix={json.dumps(matrix(report))}\n"
            f"has_groups={str(bool(report['groups'])).lower()}\n"
            f"base={report['base']}\n"
            f"plan_sha256={hashlib.sha256(payload).hexdigest()}\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("plan", "check", "install", "package", "sources")
    )
    parser.add_argument("--report", type=Path, default=Path("out/install.json"))
    parser.add_argument("--tier", choices=("g1", "g2"), default="g1")
    parser.add_argument("--group", default="")
    parser.add_argument("--cache", type=Path, default=Path("out/upstreams"))
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256", default="")
    args = parser.parse_args()
    repo = Path.cwd()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        head = revision(repo, "HEAD")
        if os.environ.get("HEAD_SHA", head) != head:
            raise ValueError("Checkout does not match the requested candidate")
        base = resolve_base(repo)
        if args.action == "plan":
            report = plan(repo, base, full=os.environ.get("FULL_REGRESSION") == "true")
            report["status"] = "planned"
        elif args.action == "check":
            report = check(
                repo,
                base,
                args.tier,
                args.group,
                plan_path=args.plan,
                plan_sha256=args.plan_sha256,
            )
        elif args.action == "package":
            report = package(repo, base, args.report.parent)
        elif args.action == "sources":
            policy = json.loads(git(repo, "show", "HEAD:quality/policy.json"))
            report = upstream.prepare(args.cache, upstream.sources(repo, head, policy))
        else:
            install(repo, args.group)
            report = {
                "status": "pass",
                "group": args.group,
                "scope": "dependencies installed; tests not executed",
            }
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report = {
            "status": "error",
            "action": args.action,
            "error": str(error),
            "g3": "not_executed",
        }
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    args.report.write_bytes(payload)
    publish_plan(report, payload)
    print(json.dumps({"status": report["status"], "report": str(args.report)}))
    raise SystemExit(1 if report["status"] in {"fail", "error"} else 0)


if __name__ == "__main__":
    main()
