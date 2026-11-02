from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quart import jsonify, request

from .core.models import clean_text
from .page_api_support import (
    PLUGIN_NAME,
)


class PluginPageApiResponseContract:
    """Response contract; shared runtime resources belong to the facade."""

    @staticmethod
    def _ok(data: dict[str, Any] | None = None):
        """单一响应信封：业务负载整体放在 `data` 键下。

        前端 `pages/记忆面板/app.js` 按 `data.data !== undefined ? data.data : data`
        解包，因此扁平端点（业务字段平铺在顶层）与包裹端点共用同一条解包规则；
        `result` 键不属于信封，不得再引入。
        """
        body = {"success": True}
        if data:
            body.update(data)
        return jsonify(body)

    @staticmethod
    def _declared_payload(
        payload: dict[str, Any], fields: tuple[str, ...]
    ) -> dict[str, Any]:
        """按字段归属契约裁剪负载，契约外的字段不会到达页面。"""

        return {key: payload[key] for key in fields if key in payload}

    @staticmethod
    def _err(message: str, status: int = 500):
        response = jsonify({"success": False, "error": message})
        response.status_code = status
        return response

    async def _json(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        return payload if isinstance(payload, dict) else {}

    def _write_plugin_config(self, raw: dict[str, Any]) -> None:
        path = self._plugin_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        import os
        import tempfile

        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix=path.stem,
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _plugin_config_path(self) -> Path:
        data_dir = Path(getattr(self.plugin.service, "data_dir", ""))
        root = (
            data_dir.parent.parent
            if data_dir.parent.name == "plugin_data"
            else data_dir.parent
        )
        return root / "config" / f"{PLUGIN_NAME}_config.json"

    def _load_config_schema(self) -> dict[str, Any]:
        path = Path(__file__).with_name("_conf_schema.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def _schema_config_values(
        self, schema: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for module, module_schema in schema.items():
            if not isinstance(module_schema, dict):
                continue
            items = module_schema.get("items")
            if not isinstance(items, dict):
                continue
            result[module] = {}
            for key, item_schema in items.items():
                if not isinstance(item_schema, dict):
                    continue
                dotted = f"{module}.{key}"
                default = item_schema.get("default")
                value = self._config_value_with_reverse_alias(dotted, default)
                result[module][key] = value
        return result

    def _config_value_with_reverse_alias(self, dotted: str, default: Any = None) -> Any:
        config = self.plugin.service.config
        marker = object()
        exact = getattr(config, "_get_exact", None)
        aliases = getattr(config, "ALIASES", {}) or {}
        if callable(exact):
            value = exact(dotted, marker)
            if value is not marker:
                return value
            for canonical, alias_list in aliases.items():
                if dotted in alias_list:
                    value = exact(canonical, marker)
                    if value is not marker:
                        return value
        return config.get(dotted, default)

    def _coerce_config_value(self, value: Any, item_schema: dict[str, Any]) -> Any:
        value_type = clean_text(item_schema.get("type"), 40)
        if value_type == "bool":
            return self._bool(value, bool(item_schema.get("default", False)))
        if value_type == "int":
            return self._int(value, int(item_schema.get("default", 0) or 0))
        if value_type == "float":
            return self._float(value, float(item_schema.get("default", 0.0) or 0.0))
        text = clean_text(value, 2000)
        options = item_schema.get("options")
        if (
            isinstance(options, list)
            and options
            and text not in {str(option) for option in options}
        ):
            default = item_schema.get("default", "")
            return clean_text(default, 2000)
        return text
