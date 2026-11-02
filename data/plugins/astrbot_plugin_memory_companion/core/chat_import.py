from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .chat_import_detail_enrichment import HistoricalChatImporterDetailEnrichment
from .chat_import_finalization import HistoricalChatImporterFinalization
from .chat_import_identity import HistoricalChatImporterIdentity
from .chat_import_identity_repair import HistoricalChatImporterIdentityRepair
from .chat_import_indexing import HistoricalChatImporterIndexing
from .chat_import_lifecycle import HistoricalChatImporterLifecycle
from .chat_import_qq_export import HistoricalChatImporterQqExport
from .chat_import_rebind import HistoricalChatImporterRebind
from .chat_import_reconciliation import HistoricalChatImporterReconciliation
from .chat_import_staging import HistoricalChatImporterStaging
from .chat_import_start import HistoricalChatImporterStart
from .chat_import_summary_records import HistoricalChatImporterSummaryRecords
from .chat_import_support import CLOSING_MARKERS as CLOSING_MARKERS
from .chat_import_support import FIELD_CONTENT_LABELS as FIELD_CONTENT_LABELS
from .chat_import_support import FIELD_LINE as FIELD_LINE
from .chat_import_support import FIELD_METADATA_LABELS as FIELD_METADATA_LABELS
from .chat_import_support import FIELD_SPEAKER_LABELS as FIELD_SPEAKER_LABELS
from .chat_import_support import FIELD_TIME_LABELS as FIELD_TIME_LABELS
from .chat_import_support import FULL_HEADER as FULL_HEADER
from .chat_import_support import HEADER_LIKE as HEADER_LIKE
from .chat_import_support import LOCAL_TZ as LOCAL_TZ
from .chat_import_support import OPENING_MARKERS as OPENING_MARKERS
from .chat_import_support import SHORT_HEADER as SHORT_HEADER
from .chat_import_support import HistoricalChatParser as HistoricalChatParser
from .chat_import_support import HistoricalChatSegmenter as HistoricalChatSegmenter
from .chat_import_support import ParsedChatMessage as ParsedChatMessage
from .chat_import_upload import HistoricalChatImporterUpload
from .chat_import_worker import HistoricalChatImporterWorker


class HistoricalChatImporter(
    HistoricalChatImporterStaging,
    HistoricalChatImporterQqExport,
    HistoricalChatImporterUpload,
    HistoricalChatImporterIdentity,
    HistoricalChatImporterStart,
    HistoricalChatImporterLifecycle,
    HistoricalChatImporterRebind,
    HistoricalChatImporterWorker,
    HistoricalChatImporterSummaryRecords,
    HistoricalChatImporterIdentityRepair,
    HistoricalChatImporterFinalization,
    HistoricalChatImporterReconciliation,
    HistoricalChatImporterDetailEnrichment,
    HistoricalChatImporterIndexing,
):
    MAX_UPLOAD_BYTES = 8 * 1024 * 1024

    IDENTITY_LINKS_VERSION = 2

    SUMMARY_PERSPECTIVE_VERSION = 1

    DETAIL_SCHEMA_VERSION = 1

    STAGED_DATA_ERROR = "历史导入暂存数据损坏，请重新上传或清理该预览"

    def __init__(self, service: Any) -> None:
        self.service = service
        self.store = service.store
        self.root = Path(service.data_dir) / "historical_chat_imports"
        self.upload_root = self.root / "uploads"
        self.batch_root = self.root / "batches"
        self.upload_root.mkdir(parents=True, exist_ok=True)
        self.batch_root.mkdir(parents=True, exist_ok=True)
        self.parser = HistoricalChatParser()
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def _config_int(self, key: str, default: int) -> int:
        config = getattr(self.service, "config", None)
        getter = getattr(config, "int", None)
        if callable(getter):
            try:
                return int(getter(f"historical_chat_import.{key}", default))
            except Exception:
                pass
        return int(default)

    def _resolved_options(self, raw: dict[str, Any] | None = None) -> dict[str, int]:
        supplied = raw if isinstance(raw, dict) else {}

        def value(name: str, default: int, minimum: int, maximum: int) -> int:
            configured = self._config_int(name, default)
            try:
                selected = int(supplied[name]) if name in supplied else configured
            except Exception:
                selected = configured
            return max(minimum, min(maximum, selected))

        return {
            "merge_seconds": value("merge_seconds", 120, 0, 1800),
            "hard_gap_minutes": value("hard_gap_minutes", 120, 5, 1440),
            "soft_gap_minutes": value("soft_gap_minutes", 30, 1, 720),
            "max_turns": value("max_turns", 30, 4, 120),
            "max_segment_chars": value("max_segment_chars", 4200, 1000, 12000),
            "package_chars": value("package_chars", 4600, 2000, 12000),
        }

    @staticmethod
    def _segmenter_from_options(options: dict[str, int]) -> HistoricalChatSegmenter:
        return HistoricalChatSegmenter(
            merge_seconds=options["merge_seconds"],
            hard_gap_minutes=options["hard_gap_minutes"],
            soft_gap_minutes=options["soft_gap_minutes"],
            max_turns=options["max_turns"],
            max_chars=options["max_segment_chars"],
        )
