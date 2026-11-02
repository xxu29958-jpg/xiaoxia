from __future__ import annotations

import asyncio

from .index import (
    build_index,
)
from .models import (
    REVIEW_CATEGORY,
    semantic_caption_is_complete,
    utc_now,
)
from .storage import (
    apply_conflict_reclassifications,
    load_category_descriptions,
    load_metadata,
    save_metadata,
)


class SemanticTaskManagerWorker:
    """Worker; shared runtime resources belong to the facade."""

    async def _run(self, pack_id: str, *, mode: str, force: bool) -> None:
        pack_dir = self._pack_dir(pack_id)
        try:
            metadata = await self._run_blocking(load_metadata, pack_dir)
            vision_provider = str(self.config.get("vision_provider_id") or "")
            embedding = self._embedding_adapter(pack_id)
            descriptions = await self._run_blocking(
                load_category_descriptions, pack_dir
            )
            memes_root = pack_dir / "memes"
            available_categories = await self._run_blocking(
                lambda: (
                    {
                        path.name: str(descriptions.get(path.name) or "")
                        for path in memes_root.iterdir()
                        if path.is_dir() and path.name != REVIEW_CATEGORY
                    }
                    if memes_root.is_dir()
                    else {}
                )
            )
            state = await self._run_blocking(self._load_state, pack_id)
            try:
                concurrency = max(1, min(16, int(state.get("concurrency") or 1)))
            except (TypeError, ValueError):
                concurrency = self._configured_concurrency()
            semaphore = asyncio.Semaphore(concurrency)
            caption_tasks = [
                asyncio.create_task(
                    self._process_caption_item(
                        pack_id,
                        pack_dir,
                        metadata,
                        str(digest),
                        raw_item,
                        vision_provider,
                        available_categories,
                        force,
                        semaphore,
                    ),
                    name=f"meme-semantic:{pack_id}:{str(digest)[:12]}",
                )
                for digest, raw_item in list(metadata.get("images", {}).items())
                if isinstance(raw_item, dict)
                and not (
                    raw_item.get("manual_override")
                    or raw_item.get("provenance") in {"manual", "mixed"}
                )
                and (force or not semantic_caption_is_complete(raw_item))
            ]
            if caption_tasks:
                try:
                    await asyncio.gather(*caption_tasks)
                except BaseException:
                    # gather 遇到第一个异常会立即向上抛出，但不会自动等待其余
                    # 协程结束。必须主动取消并收拢，否则页面已显示失败后，旧
                    # 图片请求仍可能继续运行并覆盖下一轮任务的状态。
                    for caption_task in caption_tasks:
                        if not caption_task.done():
                            caption_task.cancel()
                    await asyncio.gather(*caption_tasks, return_exceptions=True)
                    raise
            pause_event = self._pause_events.setdefault(pack_id, asyncio.Event())
            while (await self._run_blocking(self._load_state, pack_id)).get(
                "task_status"
            ) == "paused":
                await pause_event.wait()
            reclassification_result = await self._run_blocking(
                apply_conflict_reclassifications, pack_dir, metadata
            )
            if reclassification_result.get("moved"):
                state = await self._run_blocking(self._load_state, pack_id)
                state["reclassified_items"] = int(
                    state.get("reclassified_items", 0) or 0
                ) + int(reclassification_result["moved"])
                state["updated_at"] = utc_now()
                await self._run_blocking(self._save_state, pack_id, state)
            has_caption = any(
                isinstance(item, dict) and semantic_caption_is_complete(item)
                for item in metadata.get("images", {}).values()
            )
            all_captions_done = bool(
                metadata.get("images")
                and all(
                    isinstance(item, dict) and semantic_caption_is_complete(item)
                    for item in metadata.get("images", {}).values()
                )
            )
            if mode == "caption_only":
                failed = any(
                    isinstance(item, dict) and not semantic_caption_is_complete(item)
                    for item in metadata.get("images", {}).values()
                )
            elif embedding.ready and has_caption and all_captions_done:
                # build_index 会从旧 FAISS 复用未变化向量，只补充新增或变化的图片。
                index_state = await self._run_blocking(self._load_state, pack_id)
                await self._run_blocking(
                    self._save_state,
                    pack_id,
                    {
                        **index_state,
                        "task_status": "running",
                        "task_phase": "indexing",
                        "current": "正在建立向量索引",
                        "updated_at": utc_now(),
                    },
                )
                await build_index(
                    pack_dir,
                    self.plugin_data_dir,
                    pack_id,
                    embedding,
                    force=force,
                )
            elif has_caption and not embedding.ready:
                for item in metadata.get("images", {}).values():
                    if not isinstance(item, dict) or not semantic_caption_is_complete(
                        item
                    ):
                        continue
                    item["embedding_status"] = "failed"
                    item["error"] = "未配置 AstrBot 核心向量模型"
                await self._run_blocking(save_metadata, pack_dir, metadata)
            latest = await self._run_blocking(load_metadata, pack_dir)
            if mode != "caption_only":
                failed = any(
                    isinstance(item, dict)
                    and (
                        not semantic_caption_is_complete(item)
                        or item.get("embedding_status") != "done"
                    )
                    for item in latest.get("images", {}).values()
                )
            final_state = await self._run_blocking(self._load_state, pack_id)
            final_state.update(
                {
                    "task_status": "completed_with_errors" if failed else "completed",
                    "task_phase": "finished",
                    "mode": mode,
                    "current": "",
                    "active_items": [],
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            await self._run_blocking(self._save_state, pack_id, final_state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed_state = await self._run_blocking(self._load_state, pack_id)
            failed_state.update(
                {
                    "task_status": "failed",
                    "task_phase": "failed",
                    "mode": mode,
                    "current": "",
                    "active_items": [],
                    "finished_at": utc_now(),
                    "last_error": self._safe_error(exc, pack_id),
                    "updated_at": utc_now(),
                }
            )
            await self._run_blocking(self._save_state, pack_id, failed_state)
