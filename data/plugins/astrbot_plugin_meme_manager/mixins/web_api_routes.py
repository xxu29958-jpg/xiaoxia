import time

from quart import make_response, request

from astrbot.api import logger

from .web_api_support import PLUGIN_NAME, WEBUI_LOG_PREFIX
from .web_routes import WEB_ROUTES


class WebAPIMixinRoutes:
    """Routes; shared runtime resources belong to the facade."""

    def _get_github_accelerator_url(self) -> str:
        value = self._read_config_value(
            ("community", "github_accelerator_url"),
            default="https://ghfast.top/",
        )
        return str(value or "").strip()

    def _register_web_apis(self):
        for route, handler_name, methods, description in WEB_ROUTES:
            self._register_webui_api(
                route, getattr(self, handler_name), list(methods), description
            )

    def _register_webui_api(self, route, handler, methods, desc):
        route_path = f"/{PLUGIN_NAME}/{route.strip('/')}"

        async def logged_handler(*args, **kwargs):
            started_at = time.monotonic()
            logger.info(f"{WEBUI_LOG_PREFIX} {request.method} {route_path} 开始")
            try:
                response = await handler(*args, **kwargs)
                if route in {
                    "meme_image_data",
                    "preview/manifest",
                    "auto-collect/inbox/image_data",
                }:
                    response = await make_response(response)
                    if response.status_code >= 400:
                        response.headers["Cache-Control"] = "no-store"
            except Exception:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                logger.error(
                    f"{WEBUI_LOG_PREFIX} {request.method} {route_path} 失败 耗时={elapsed_ms}ms",
                    exc_info=True,
                )
                raise
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            status_code = self._get_webui_response_status(response)
            logger.info(
                f"{WEBUI_LOG_PREFIX} {request.method} {route_path} 完成 状态={status_code} 耗时={elapsed_ms}ms"
            )
            return response

        logged_handler.__name__ = f"webui_{handler.__name__}"
        self.context.register_web_api(route_path, logged_handler, methods, desc)
