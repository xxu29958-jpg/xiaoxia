from .web_api_categories import WebAPIMixinCategories
from .web_api_collection import WebAPIMixinCollection
from .web_api_community import WebAPIMixinCommunity
from .web_api_guards import WebAPIMixinGuards
from .web_api_image_semantics import WebAPIMixinImageSemantics
from .web_api_images import WebAPIMixinImages
from .web_api_pack_view import WebAPIMixinPackView
from .web_api_packs import WebAPIMixinPacks
from .web_api_preview import WebAPIMixinPreview
from .web_api_routes import WebAPIMixinRoutes
from .web_api_settings import WebAPIMixinSettings
from .web_api_support import (
    COMMUNITY_INSTALL_JOB_TTL_SECONDS as COMMUNITY_INSTALL_JOB_TTL_SECONDS,
)
from .web_api_support import (
    IMG_HOST_STATUS_CACHE_TTL_SECONDS as IMG_HOST_STATUS_CACHE_TTL_SECONDS,
)
from .web_api_support import MAX_ORIGINAL_IMAGE_BYTES as MAX_ORIGINAL_IMAGE_BYTES
from .web_api_support import MAX_PACK_ARCHIVE_BYTES as MAX_PACK_ARCHIVE_BYTES
from .web_api_support import (
    MAX_PACK_UPLOAD_REQUEST_BYTES as MAX_PACK_UPLOAD_REQUEST_BYTES,
)
from .web_api_support import MAX_PREVIEW_IMAGE_BYTES as MAX_PREVIEW_IMAGE_BYTES
from .web_api_support import (
    PACK_IMPORT_SESSION_TTL_SECONDS as PACK_IMPORT_SESSION_TTL_SECONDS,
)
from .web_api_support import PLUGIN_NAME as PLUGIN_NAME
from .web_api_support import PREVIEW_IMAGE_MAX_DIMENSION as PREVIEW_IMAGE_MAX_DIMENSION
from .web_api_support import WEBUI_LOG_PREFIX as WEBUI_LOG_PREFIX
from .web_api_sync import WebAPIMixinSync
from .web_api_tasks import WebAPIMixinTasks
from .web_api_transfer import WebAPIMixinTransfer
from .web_api_updates import WebAPIMixinUpdates
from .web_api_upload import WebAPIMixinUpload


class WebAPIMixin(
    WebAPIMixinRoutes,
    WebAPIMixinGuards,
    WebAPIMixinUpload,
    WebAPIMixinPackView,
    WebAPIMixinImages,
    WebAPIMixinCategories,
    WebAPIMixinSync,
    WebAPIMixinPreview,
    WebAPIMixinImageSemantics,
    WebAPIMixinPacks,
    WebAPIMixinCollection,
    WebAPIMixinTasks,
    WebAPIMixinTransfer,
    WebAPIMixinUpdates,
    WebAPIMixinCommunity,
    WebAPIMixinSettings,
):
    """包含所有 WebUI 仪表盘 API 的注册与处理逻辑"""
