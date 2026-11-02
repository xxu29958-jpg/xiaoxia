from __future__ import annotations

from quart import request

from .core.models import clean_text


class PluginPageApiOperations:
    """Operations; shared runtime resources belong to the facade."""

    async def maintenance(self):
        result = await self.plugin.service.sleep_maintenance(reason="page_maintenance")
        return self._ok({"data": result})

    async def sleep_maintenance(self):
        if request.method == "POST":
            result = await self.plugin.service.sleep_maintenance(reason="page_sleep")
        else:
            result = self.plugin.service.sleep_status()
        return self._ok({"data": result})

    async def audit_preview(self):
        payload = await self._json()
        try:
            result = await self.plugin.service.preview_memory_audit(
                limit=max(0, min(100, self._int(payload.get("limit"), 0)))
            )
        except (ValueError, RuntimeError) as exc:
            return self._err(clean_text(exc, 300), 400)
        return self._ok({"data": result})

    async def audit_status(self):
        try:
            result = await self.plugin.service.memory_audit_status(
                clean_text(request.args.get("batch_id"), 80)
            )
        except ValueError as exc:
            return self._err(clean_text(exc, 300), 400)
        return self._ok({"data": result})

    async def audit_apply(self):
        payload = await self._json()
        try:
            result = await self.plugin.service.apply_memory_audit(
                clean_text(payload.get("batch_id"), 80),
                clean_text(payload.get("confirm"), 20),
            )
        except ValueError as exc:
            return self._err(clean_text(exc, 300), 400)
        return self._ok({"data": result})

    async def audit_rollback(self):
        payload = await self._json()
        try:
            result = await self.plugin.service.rollback_memory_audit(
                clean_text(payload.get("batch_id"), 80),
                clean_text(payload.get("confirm"), 20),
            )
        except ValueError as exc:
            return self._err(clean_text(exc, 300), 400)
        return self._ok({"data": result})

    async def repair_livingmemory_content(self):
        payload = await self._json()
        result = await self.plugin.service.migrator.repair_imported_content(
            configured_path=clean_text(payload.get("path"), 1000)
        )
        return self._ok({"data": result})

    async def clear_all(self):
        payload = await self._json()
        if clean_text(payload.get("confirm"), 20) != "清空":
            return self._err("confirmation mismatch", 400)
        result = await self.plugin.service.clear_all_memory_data()
        return self._ok({"data": result})

    async def clear_scope(self):
        payload = await self._json()
        target_type = clean_text(payload.get("target_type") or payload.get("type"), 40)
        group_id = clean_text(payload.get("group_id"), 120)
        user_id = clean_text(payload.get("user_id"), 120)
        preview = self._bool(payload.get("preview"), False)
        try:
            if preview:
                result = await self.plugin.service.store.preview_scoped_memory_clear(
                    target_type=target_type,
                    group_id=group_id,
                    user_id=user_id,
                )
            else:
                if clean_text(payload.get("confirm"), 20) != "清空":
                    return self._err("confirmation mismatch", 400)
                result = await self.plugin.service.clear_scoped_memory(
                    target_type=target_type,
                    group_id=group_id,
                    user_id=user_id,
                )
        except ValueError as exc:
            return self._err(str(exc), 400)
        return self._ok({"data": result})

    async def release_summary_batch(self):
        """End the quarantine freeze of one summary batch after human review.

        Without this the events a quarantined batch owns stay excluded from the
        pending window forever, so those messages never become long-term memory
        and nothing tells the administrator that they will not.
        """
        payload = await self._json()
        batch_id = clean_text(payload.get("batch_id"), 160)
        mode = clean_text(payload.get("mode"), 20).lower() or "retry"
        if not batch_id:
            return self._err("batch_id is required", 400)
        result = await self.plugin.service.store.release_summary_batch(
            batch_id, mode=mode
        )
        if not result.get("ok"):
            return self._err(
                clean_text(result.get("error"), 120) or "release failed", 400
            )
        return self._ok({"data": result})

    async def import_preview(self):
        configured = clean_text(request.args.get("path", ""), 1000)
        report = self.plugin.service.migrator.preview(configured)
        return self._ok({"report": report})

    async def import_run(self):
        payload = await self._json()
        result = await self.plugin.service.import_livingmemory(
            configured_path=clean_text(payload.get("path"), 1000)
        )
        return self._ok({"data": result})
