from pathlib import Path

from astrbot.api import logger

from .image_host.img_sync import ImageSync


class MemeSenderImageHost:
    """Image host; shared runtime resources belong to the facade."""

    def _get_image_host_type(self) -> str:
        image_host = self._read_config_value(
            ("storage", "provider"),
            default="stardots",
        )
        return str(image_host or "stardots").strip().lower()

    def _read_path(self, path: tuple[str, ...], missing=None):
        current = self.config
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return missing
            current = current[key]
        return current

    def _read_config_value(
        self,
        primary_path: tuple[str, ...],
        default=None,
    ):
        value = self._read_path(primary_path)
        return default if value is None else value

    def _get_provider_config(self, provider_key: str) -> dict:
        provider_config = self._read_path(("storage", "providers", provider_key), {})
        return dict(provider_config) if isinstance(provider_config, dict) else {}

    def _resolve_sync_pack_target(self, preferred_pack_id: str | None = None):
        from .backend.packs.resolver import get_pack_paths, resolve_pack_id

        pack_id = str(preferred_pack_id or "").strip()
        if pack_id:
            paths = get_pack_paths(pack_id)
            pack_dir = paths["pack_dir"]
            if pack_dir.is_dir():
                memes_dir = paths["memes_dir"]
                memes_dir.mkdir(parents=True, exist_ok=True)
                return pack_id, memes_dir

        resolved_pack_id = resolve_pack_id()
        paths = get_pack_paths(resolved_pack_id)
        memes_dir = paths["memes_dir"]
        memes_dir.mkdir(parents=True, exist_ok=True)
        return resolved_pack_id, memes_dir

    def _ensure_img_sync_for_pack(self, preferred_pack_id: str | None = None):
        if not (self.img_sync_config and self.img_sync_provider_type):
            return None

        target_pack_id, target_memes_dir = self._resolve_sync_pack_target(
            preferred_pack_id
        )
        running_process = (
            getattr(self.img_sync, "sync_process", None) if self.img_sync else None
        )
        if running_process and running_process.is_alive():
            if target_memes_dir.resolve() != Path(self.img_sync.local_dir).resolve():
                raise RuntimeError("已有同步任务正在运行，请等待完成后再切换同步表情包")
            return self.img_sync

        current_dir = None
        if self.img_sync and getattr(self.img_sync, "local_dir", None):
            try:
                current_dir = Path(self.img_sync.local_dir).resolve()
            except Exception:
                current_dir = None

        if current_dir != target_memes_dir.resolve():
            # 构建适配器时仅验证配置，不发起网络探测。
            try:
                previous_sync = self.img_sync
                self.img_sync = ImageSync(
                    config=self.img_sync_config,
                    local_dir=target_memes_dir,
                    provider_type=self.img_sync_provider_type,
                )
                if previous_sync:
                    previous_sync.close()
            except Exception as exc:
                logger.error(
                    "图床 %s 初始化失败，无法同步：%s",
                    self.img_sync_provider_type,
                    exc,
                )
                self.img_sync = None
                return None

        self._img_sync_pack_id = target_pack_id
        return self.img_sync
