"""Authenticated, shared-owner controls for the companion's management page."""

import asyncio
import base64
import io
import json
import sqlite3
import time

from PIL import Image
from quart import request

from .model_controls import MODELS


class ManagementApi:
    def __init__(self, plugin):
        self.plugin = plugin
        self.lock = asyncio.Lock()

    def library(self):
        library = self.plugin.services.plugin("astrbot_plugin_meme_manager")
        if library is None:
            raise ValueError("表情包服务未加载，请先检查插件状态。")
        return library

    def gallery(self):
        library = self.library()
        pack = library._resolve_runtime_pack_context()
        storage = self.plugin.online_memes.native(library, "semantic.storage")
        metadata = storage.load_metadata(pack["pack_dir"])
        state = library.meme_usage.status(self.plugin.owner_memory.shared_scope)
        recent = {x["digest"]: x for x in state["recent"]}
        images = [
            {
                "digest": x["content_sha256"],
                "caption": x.get("caption", ""),
                "category": x.get("category", ""),
                "tags": x.get("tags", []),
                "indexed": x.get("embedding_status") == "done",
                "blocked": x["content_sha256"] in state["blocked"],
                "recent": recent.get(x["content_sha256"]),
            }
            for x in metadata.get("images", {}).values()
            if x.get("content_sha256")
        ]
        return {
            "pack_id": pack["pack_id"],
            "total": len(images),
            "indexed": sum(x["indexed"] for x in images),
            "enabled": state["enabled"],
            "images": images,
        }

    async def overview(self):
        p = self.plugin
        data = (await p.admin.status())["data"]
        data.pop("deliveries", None)
        data["channels_status"] = []
        for channel in ("qq", "wechat"):
            try:
                resolved = p.contacts.resolve(channel)
                adapter = p.context.get_platform_inst(
                    resolved["origin"].split(":", 1)[0]
                )
                connected = resolved["online"]
                client = getattr(adapter, "bot", None)
                if (
                    channel == "qq"
                    and client is not None
                    and hasattr(client, "get_status")
                ):
                    try:
                        status = await asyncio.wait_for(client.get_status(), 3)
                        connected = bool(
                            status.get("online") and status.get("good", True)
                        )
                    except Exception:
                        connected = False
                data["channels_status"].append(
                    {"channel": channel, "online": connected}
                )
            except ValueError as exc:
                data["channels_status"].append(
                    {"channel": channel, "online": False, "error": str(exc)}
                )
        data["models"] = [
            {"id": pid, "name": name, "available": p.models.available(pid)}
            for pid, name in MODELS.values()
        ]
        try:
            data["selected_model"] = p.models.selected()
        except (OSError, ValueError):
            data["selected_model"] = ""
            data["model_error"] = "模型偏好无法读取，请重新选择并保存。"
        data["public_browser"] = p.public_browser.status(p.owner_memory.shared_scope)
        data["desktop"] = p.desktop.status()
        try:
            data["gallery"] = self.gallery()
        except (ValueError, OSError, sqlite3.Error) as exc:
            data["gallery"] = {"error": str(exc), "images": []}
        data["bookmarks"] = p.public_browser.store.bookmarks(
            p.owner_memory.shared_scope, "list"
        ).get("bookmarks", [])
        try:
            materials = p.initiative.materials.candidates()
            materials_error = ""
        except (ValueError, OSError, sqlite3.Error):
            materials, materials_error = [], "浏览笔记暂时无法读取。"
        data["conversation"] = {
            "topics": p.dialogue.visible(p.target),
            "topic_review": p.prompt.review.last,
            "materials": materials,
            "materials_error": materials_error,
            "last_check": p.initiative.last_check,
        }
        return {"ok": True, "data": data}

    async def image(self):
        digest = request.args.get("digest", "")
        library = self.library()
        pack = library._resolve_runtime_pack_context()
        storage = self.plugin.online_memes.native(library, "semantic.storage")
        for item in storage.load_metadata(pack["pack_dir"]).get("images", {}).values():
            if digest != item.get("content_sha256"):
                continue
            path = storage.safe_relative_path(pack["pack_dir"], item["relative_path"])
            if not path or not path.is_file():
                break

            def thumbnail():
                with Image.open(path) as img:
                    img.thumbnail((240, 200))
                    buf = io.BytesIO()
                    img.convert("RGB").save(buf, "JPEG", quality=82)
                    return base64.b64encode(buf.getvalue()).decode("ascii")

            return {
                "ok": True,
                "src": "data:image/jpeg;base64," + await asyncio.to_thread(thumbnail),
            }
        return {"ok": False, "error": "这张图片已经移除，请刷新图库。"}, 404

    async def action(self):
        data = await request.get_json() or {}
        if not isinstance(data, dict):
            return {"ok": False, "error": "Invalid request"}, 400
        try:
            # Stop controls must not wait behind an indexing or settings write.
            if data.get("action") in {"desktop_stop", "browser_stop"}:
                return {"ok": True, "message": await self.apply(data)}
            async with self.lock:
                message = await self.apply(data)
            return {"ok": True, "message": message}
        except (ValueError, RuntimeError, OSError) as exc:
            return {"ok": False, "error": str(exc)[:240]}, 400

    async def memory(self):
        p = self.plugin
        bridge = p.services.memory()
        payload = await request.get_json() if request.method == "POST" else {}
        payload = payload or {}
        if request.method == "POST" and (
            not isinstance(payload, dict)
            or payload.get("action") != "set"
            or not isinstance(payload.get("expected"), str)
            or not isinstance(payload.get("content"), str)
            or len(payload["content"]) > 4000
        ):
            return {"ok": False, "error": "请先读取原内容，再保存这条偏好。"}, 400
        return await p.memory.core.execute(
            p.target,
            getattr(bridge, "_plugin", None),
            p.target,
            action=payload.get("action", "list"),
            label=payload.get("label", ""),
            content=payload.get("content", ""),
            kind=payload.get("kind") or "preference",
            expected=payload.get("expected"),
        )

    async def apply(self, data):
        p, action = self.plugin, data.get("action")
        scope = p.owner_memory.shared_scope
        if action == "topic_close":
            topic = next(
                (x for x in p.dialogue.visible(p.target) if x["id"] == data.get("id")),
                None,
            )
            if topic is None or topic != data.get("expected"):
                raise ValueError("这个话题已经更新，请刷新后再操作。")
            result = p.dialogue.update(
                p.target,
                "在管理页选择不再追问这个话题",
                action="resolve",
                item_id=topic["id"],
                evidence_quote="在管理页选择不再追问这个话题",
            )
            if not result.get("ok"):
                raise ValueError("话题未关闭，请检查状态后重试。")
            return "已结束这个话题，QQ 和微信共同生效。"
        if action == "material_dismiss":
            p.initiative.materials.dismiss(str(data.get("key", "")))
            return "未来 30 天不主动分享这个网址，收藏和笔记保留。"
        if action == "desktop_stop":
            await p.desktop.stop()
            return "桌面操作已停止。"
        if action == "desktop_switch":
            if type(data.get("enabled")) is not bool:
                raise ValueError("Missing switch value")
            p.desktop.store.enable(data["enabled"])
            if not data["enabled"]:
                await p.desktop.stop()
            return "桌面操作开关已保存。"
        if action == "model":
            pid = data.get("provider_id")
            if pid not in {x[0] for x in MODELS.values()} or not p.models.available(
                pid
            ):
                raise ValueError("这个模型尚未配置可用，未切换。")
            async with p.models.lock:
                temp = p.models.path.with_suffix(".tmp")
                temp.write_text(json.dumps({"provider_id": pid}), encoding="utf-8")
                temp.replace(p.models.path)
            return "模型已切换，QQ 和微信共同生效。"
        if action in {"memes", "block", "unblock"}:
            usage = self.library().meme_usage
            if action == "memes":
                if type(data.get("enabled")) is not bool:
                    raise ValueError("Missing switch value")
                usage.set_enabled(scope, data["enabled"])
            else:
                usage.block(scope, str(data.get("digest", "")), action == "block")
            return "表情偏好已保存，QQ 和微信共同生效。"
        if action == "proactive":
            enabled = data.get("enabled")
            if type(enabled) is not bool or not p.policy.healthy:
                raise ValueError("主动消息状态无法保存。")
            if enabled and not p.config.get("proactive_enabled"):
                raise ValueError("主动消息总开关未开启。")
            p.policy.data.update(enabled=enabled, next_due=time.time() + 9000)
            p.policy.save()
            return "主动消息已恢复。" if enabled else "主动消息已暂停。"
        if action == "browser_stop":
            await p.public_browser.close(scope)
            return "浏览器已停止，收藏和登录状态保留。"
        if action == "browser_switch":
            if type(data.get("enabled")) is not bool:
                raise ValueError("Missing switch value")
            p.public_browser.store.set_enabled(data["enabled"])
            if not data["enabled"]:
                await p.public_browser.close()
            return "浏览器开关已保存。"
        if action == "reindex":
            library = self.library()
            pack = library._resolve_runtime_pack_context()
            await library.semantic_task_manager.rebuild_index(
                pack["pack_id"], force=False
            )
            if not library.semantic_task_manager.status(pack["pack_id"]).get(
                "index_ready"
            ):
                raise ValueError("索引尚未就绪，请在图库管理查看错误详情。")
            return "索引已校验并建立。"
        raise ValueError("Unknown management action")
