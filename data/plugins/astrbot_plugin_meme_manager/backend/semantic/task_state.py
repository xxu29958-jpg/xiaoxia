from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


class SemanticTaskManagerState:
    """State; shared runtime resources belong to the facade."""

    @staticmethod
    def _validate_pack_id(pack_id: str) -> str:
        value = str(pack_id or "").strip()
        if not value or not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", value):
            raise ValueError("pack_id 无效")
        return value

    def _state_path(self, pack_id: str) -> Path:
        return (
            self.plugin_data_dir / "semantic_indexes" / str(pack_id) / "task_state.json"
        )

    def _selection_path(self, pack_id: str) -> Path:
        return (
            self.plugin_data_dir
            / "semantic_indexes"
            / str(pack_id)
            / "provider_selection.json"
        )

    def _load_selection(self, pack_id: str) -> dict[str, Any]:
        path = self._selection_path(pack_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_json_atomic(
        self, path: Path, payload: dict[str, Any], prefix: str
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
                json.dump(payload, file_obj, ensure_ascii=False, indent=2)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _load_state(self, pack_id: str) -> dict[str, Any]:
        path = self._state_path(pack_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, pack_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(pack_id)
        self._save_json_atomic(path, state, ".task_state.")

    def _pack_dir(self, pack_id: str) -> Path:
        return self.plugin_data_dir / "packs" / str(pack_id)

    def _safe_error(self, error: Any, pack_id: str = "") -> str:
        message = str(error or "未知错误")
        for secret_path in (str(self.plugin_data_dir), str(self._pack_dir(pack_id))):
            if secret_path:
                message = message.replace(secret_path, "<本地资源>")
        return message[:500]
