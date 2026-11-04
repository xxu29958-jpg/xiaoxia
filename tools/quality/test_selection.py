"""Explicit consumer contracts; an unmatched code change remains a failed gate."""

from fnmatch import fnmatchcase
from pathlib import Path

from .ownership import SOURCE

CONTRACTS = {
    "registration_contracts": (
        "astrbot/core/provider/register.py",
        "astrbot/core/provider/manager.py",
        "astrbot/core/provider/provider.py",
        "astrbot/core/provider/entities.py",
        "astrbot/core/platform/register.py",
        "astrbot/core/platform/manager.py",
        "astrbot/core/platform/platform.py",
        "astrbot/core/platform/platform_metadata.py",
        "tests/registration/*",
        "data/plugins/astrbot_plugin_codex_oauth_plug/tests/test_astrbot_registration.py",
        "tools/quality/registration_checks.py",
    ),
    "frontend_contracts": ("dashboard/*", "tools/quality/frontend*"),
    "reading_contracts": (
        "data/plugins/astrbot_plugin_ada_companion/reading_*.py",
        "data/plugins/astrbot_plugin_ada_companion/web_notebook.py",
        "data/plugins/astrbot_plugin_ada_companion/web_tools.py",
        "data/plugins/astrbot_plugin_ada_companion/browser_document.py",
        "data/plugins/astrbot_plugin_ada_companion/interest_discovery.py",
        "data/plugins/astrbot_plugin_ada_companion/bili_discovery.py",
        "data/plugins/astrbot_plugin_ada_companion/initiative_materials.py",
        "data/plugins/astrbot_plugin_ada_companion/tool_policy.py",
        "data/plugins/astrbot_plugin_ada_companion/prompting.py",
        "data/plugins/astrbot_plugin_ada_companion/main.py",
        "maintenance/tests/test_reading_*.py",
        "maintenance/tests/reading_crash_support.py",
        "maintenance/tests/test_interest_discovery.py",
        "maintenance/tests/test_initiative_materials.py",
        "maintenance/tests/test_ada_web_backpack.py",
        "maintenance/tests/test_bili_*.py",
        "maintenance/tests/test_browser_*.py",
        "maintenance/tests/test_sticker_request_routing.py",
    ),
    "cognition_contracts": (
        "tools/quality/cognition_checks.py",
        "tools/quality/pytest_progress.py",
        "data/plugins/astrbot_plugin_memory_companion/core/appraisal_*.py",
        "data/plugins/astrbot_plugin_memory_companion/core/sql/appraisal_state.sql",
        "data/plugins/astrbot_plugin_ada_companion/appraisals.py",
        "data/plugins/astrbot_plugin_memory_companion/core/core_state*.py",
        "data/plugins/astrbot_plugin_memory_companion/core/working_state*.py",
        "data/plugins/astrbot_plugin_memory_companion/core/sensitive_data.py",
        "data/plugins/astrbot_plugin_memory_companion/core/store_schema.py",
        "data/plugins/astrbot_plugin_memory_companion/core/store_scope_erasure.py",
        "data/plugins/astrbot_plugin_memory_companion/core/store_erasure_domains.py",
        "data/plugins/astrbot_plugin_memory_companion/core/sql/working_state.sql",
        "data/plugins/astrbot_plugin_memory_companion/core/store.py",
        "data/plugins/astrbot_plugin_memory_companion/core/store_record_insert.py",
        "data/plugins/astrbot_plugin_memory_companion/core/sql/memory_schema.sql",
        "data/plugins/astrbot_plugin_memory_companion/core/injection_memory_lines.py",
        "data/plugins/astrbot_plugin_ada_companion/shared_core.py",
        "data/plugins/astrbot_plugin_ada_companion/memory_tools.py",
        "data/plugins/astrbot_plugin_ada_companion/owner_memory.py",
        "data/plugins/astrbot_plugin_ada_companion/main.py",
        "data/plugins/astrbot_plugin_ada_companion/dialogue.py",
        "data/plugins/astrbot_plugin_ada_companion/chat_replyer.py",
        "data/plugins/astrbot_plugin_ada_companion/thread_review.py",
        "data/plugins/astrbot_plugin_ada_companion/prompting.py",
        "data/plugins/astrbot_plugin_ada_companion/initiative.py",
        "data/plugins/astrbot_plugin_ada_companion/management_api.py",
        "data/plugins/astrbot_plugin_ada_companion/admin_api.py",
        "data/plugins/astrbot_plugin_ada_companion/web_routes.py",
        "data/plugins/astrbot_plugin_ada_companion/pages/manage/conversation.js",
        "maintenance/tests/conftest.py",
        "maintenance/tests/working_state_support.py",
        "maintenance/tests/test_working_management_page.py",
        "maintenance/tests/test_ada_dialogue.py",
        "maintenance/tests/test_ada_companion.py",
        "maintenance/tests/test_ada_cloud_voice.py",
        "maintenance/tests/test_chat_replyer.py",
        "maintenance/tests/test_thread_review.py",
        "maintenance/tests/test_contact_channels.py",
        "maintenance/tests/test_conversation_management.py",
        "maintenance/tests/test_ada_owner_memory.py",
        "maintenance/tests/test_companion_runtime.py",
        "tests/cognition_state/*",
    ),
    "quality_contracts": (
        ".github/workflows/*",
        ".gitignore",
        "tools/quality/*",
        "quality/*",
        "requirements/quality.txt",
        "pyrightconfig.json",
        "pyproject.toml",
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
        "models.manifest.json",
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
        "requirements/ci-signal.txt",
        "services/voice/cover_pipeline.py",
        "tests/media_pipeline/*",
    ),
    "performance_contracts": (
        "data/plugins/astrbot_plugin_ada_companion/response_media.py",
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
