"""Prepare pinned Git tree objects separately from read-only measurement."""

import json
import re
import subprocess
from pathlib import Path

from .git_snapshot import git, revision


def sources(repo: Path, ref: str, policy: dict) -> list[dict]:
    result = [{"id": "astrbot", "prefix": "", **policy["astrbot_upstream"]}]
    result.extend(
        {
            "id": item["prefix"].strip("/").split("/")[-1],
            "prefix": item["prefix"],
            "repository": item["source"],
            "commit": item["commit"],
            "basis": item["basis"],
        }
        for item in policy["origins"]
        if "commit" in item
    )
    vendors = json.loads(git(repo, "show", f"{ref}:vendor/sources.json"))
    tracked = git(repo, "ls-tree", "-r", "--name-only", "-z", ref).decode().split("\0")
    for name, item in vendors.items():
        prefix = (
            "vendor/cosyvoice/third_party/Matcha-TTS/"
            if name == "cosyvoice_matcha"
            else f"vendor/{name}/"
        )
        if "revision" in item and any(path.startswith(prefix) for path in tracked):
            result.append(
                {
                    "id": name,
                    "prefix": prefix,
                    "repository": item["repository"],
                    "commit": item["revision"],
                    "basis": "vendor/sources.json",
                }
            )
    return result


def cache_path(cache: Path, source: dict) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", source["id"]) or not re.fullmatch(
        r"[0-9a-f]{40}", source["commit"]
    ):
        raise ValueError(
            "Upstream comparisons require a safe identifier and full commit"
        )
    return cache / source["id"]


def prepare(cache: Path, comparisons: list[dict]) -> dict:
    for source in comparisons:
        target = cache_path(cache, source)
        target.mkdir(parents=True, exist_ok=True)
        if not (target / "HEAD").exists():
            git(target, "init", "--bare", "-q")
        try:
            present = revision(target, source["commit"]) == source["commit"]
        except subprocess.CalledProcessError:
            present = False
        if not present:
            git(
                target,
                "fetch",
                "--depth=1",
                "--filter=blob:none",
                source["repository"],
                source["commit"],
            )
        if revision(target, source["commit"]) != source["commit"]:
            raise ValueError(f"Wrong comparison commit for {source['id']}")
    return {
        "status": "pass",
        "comparisons": comparisons,
        "scope": "Pinned Git tree objects only; no source imported or installed",
    }


def index(cache: Path, comparisons: list[dict]) -> dict[str, str]:
    result = {}
    # More specific nested vendor roots override the parent repository's gitlink.
    for source in sorted(comparisons, key=lambda item: len(item["prefix"])):
        target = cache_path(cache, source)
        revision(target, source["commit"])
        rows = git(target, "ls-tree", "-r", "-z", source["commit"])
        for row in rows.split(b"\0"):
            if not row:
                continue
            header, path = row.split(b"\t", 1)
            _, kind, oid = header.split()
            if kind == b"blob":
                result[source["prefix"] + path.decode()] = oid.decode()
    return result
