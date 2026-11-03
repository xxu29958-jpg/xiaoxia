"""Explicit consumer contracts; an unmatched code change remains a failed gate."""

from fnmatch import fnmatchcase
from pathlib import Path

from .ownership import SOURCE

CONTRACTS = {
    "quality_contracts": (
        "tools/quality/*",
        "quality/*",
        "requirements/quality.txt",
        "pyrightconfig.json",
    ),
    "installation_contracts": (
        "setup.ps1",
        "services/runtime.py",
        "tools/download_models.py",
        "vendor/sources.json",
        "tools/installation/*",
        "tests/installation/*",
        "requirements/fish*",
        "models-manifests/*",
    ),
    "media_job_contracts": (
        "services/jobs/*",
        "tests/media_jobs/*",
        "tests/media_pipeline/*",
        "services/voice/job_api.py",
        "services/voice/job_execution.py",
        "services/voice/job_renderer.py",
        "services/voice/cover_source.py",
        "services/voice/server_voice.py",
        "services/voice/server_singing.py",
        "services/voice/cover_pipeline.py",
    ),
    "media_pipeline_contracts": (
        "services/voice/cover_pipeline.py",
        "tests/media_pipeline/*",
    ),
    "performance_contracts": (
        "data/plugins/astrbot_plugin_ada_companion/performance*.py",
        "data/plugins/astrbot_plugin_ada_companion/main.py",
        "data/plugins/astrbot_plugin_ada_companion/initiative.py",
        "data/plugins/astrbot_plugin_ada_companion/thread_review.py",
        "data/plugins/astrbot_plugin_ada_companion/prompting.py",
        "astrbot/core/star/register/star_handler.py",
        "tests/performance_delivery/*",
        "maintenance/tests/test_ada_local_voice.py",
    ),
}


def required_tests(changes: list) -> dict:
    paths = [change.path for change in changes]
    covered = set()
    result = {}
    for flag, patterns in CONTRACTS.items():
        selected = {
            path
            for path in paths
            if any(fnmatchcase(path, pattern) for pattern in patterns)
        }
        result[flag] = bool(selected)
        covered.update(selected)
    result["unmapped_code_changes"] = [
        path for path in paths if Path(path).suffix in SOURCE and path not in covered
    ]
    result["frontend_checks_required"] = [
        path
        for path in paths
        if path.startswith("dashboard/")
        and Path(path).suffix not in {".md", ".png", ".svg"}
    ]
    return result
