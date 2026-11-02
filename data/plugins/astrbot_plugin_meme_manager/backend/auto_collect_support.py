from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .semantic.caption import (
    CAPTION_ANALYSIS_PROMPT,
)

AUTO_COLLECT_SCHEMA_VERSION = 1


AUTO_COLLECT_PROMPT_VERSION = "auto-collect-v2-review"


MAX_IMAGE_BYTES = 20 * 1024 * 1024


MAX_IMAGE_PIXELS = 40_000_000


MAX_DECISION_CACHE_ITEMS = 2000


MAX_OBSERVATIONS = 2000


MAX_OBSERVATION_MESSAGE_IDS = 16


OBSERVATION_TTL_SECONDS = 7 * 24 * 3600


QUEUE_SIZE = 20


FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "GIF": ".gif",
    "WEBP": ".webp",
}


AUTO_COLLECT_SYSTEM_PROMPT = (
    """你是聊天表情包审核与分类器。请判断图片是否适合保存为聊天表情包，并从给定分类中选择最合适的一项。
普通照片、文档截图、聊天记录截图、商品图、二维码、收付款码、广告和没有明确聊天反应用途的图片默认不是表情包。
表情包可以是静态图或动图，也可以包含文字。不得创造分类名，只能逐字返回给定分类之一；无法可靠分类时 category 留空。
只返回 JSON，不要输出分析过程。"""
    + CAPTION_ANALYSIS_PROMPT
    + """
请根据图片实际内容选择分类，而非将已有分类当作前提。caption 必须描述核心含义及聊天使用场景；tags 返回 6-10 个细粒度标签；visible_text 必须忠实记录图片文字。仍然仅返回上述审核 JSON 格式。"""
)


@dataclass(slots=True)
class AutoCollectJob:
    """保存单张图片自动收集请求的快照。

    Args:
        snapshot_path: 在消息事件结束前创建的插件自有图片快照路径。
        target_pack_id: 最终接收图片的表情包 ID。
        categories: 用于识别分类的运行时分类描述。
        source_kind: 来源类型，值为 ``group`` 或 ``user``。
        source_id: 原始群聊或个人用户 ID。
    """

    snapshot_path: Path
    target_pack_id: str
    categories: dict[str, str]
    source_kind: str
    source_id: str
    digest: str = ""


@dataclass(frozen=True)
class CollectionPaths:
    """Physical collection storage owned by one plugin runtime."""

    packs: Path
    inbox: Path
    images: Path
    metadata: Path
    state: Path
    temporary: Path

    @classmethod
    def for_root(cls, root: Path):
        root = Path(root)
        inbox = root / "auto_collect_inbox"
        return cls(
            root / "packs",
            inbox,
            inbox / "images",
            inbox / "metadata.json",
            root / "auto_collect_state.json",
            root / "temp" / "auto_collect",
        )
