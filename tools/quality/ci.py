"""Cloud CI entry: plan, execute, provision or deliver an exact source candidate."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

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
    if group in {
        "performance_contracts",
        "cognition_contracts",
        "reading_contracts",
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
    elif group == "media_pipeline_contracts":
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
    elif group in {"runtime_gateway", "runtime_singing"}:
        raise RuntimeError(
            "GPU runtime type checks require an explicitly provisioned matching worker environment"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "check", "install", "package"))
    parser.add_argument("--report", type=Path, default=Path("out/install.json"))
    parser.add_argument("--tier", choices=("g1", "g2"), default="g1")
    parser.add_argument("--group", default="")
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
            if output := os.environ.get("GITHUB_OUTPUT"):
                with Path(output).open("a", encoding="utf-8") as stream:
                    stream.write(
                        f"matrix={json.dumps(matrix(report))}\nhas_groups={str(bool(report['groups'])).lower()}\nbase={base}\n"
                    )
        elif args.action == "check":
            report = check(repo, base, args.tier, args.group)
        elif args.action == "package":
            report = package(repo, base, args.report.parent)
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
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], "report": str(args.report)}))
    raise SystemExit(1 if report["status"] in {"fail", "error"} else 0)


if __name__ == "__main__":
    main()
