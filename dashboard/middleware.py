"""纯 ASGI 中间件：认证 + 权限"""
import logging
from starlette.responses import JSONResponse, RedirectResponse, PlainTextResponse

from .auth import sessions
from .config import settings
from .permissions import engine, detect_resource_type

logger = logging.getLogger("stock2.middleware")

PUBLIC_PATHS = {
    settings.LOGIN_PATH,
    settings.LOGOUT_PATH,
    "/favicon.ico",
    f"{settings.REST_API_PREFIX}/health",
}


PUBLIC_PATHS = {
    settings.LOGIN_PATH,
    settings.LOGOUT_PATH,
    "/favicon.ico",
    f"{settings.REST_API_PREFIX}/health",
}

STATIC_PREFIXES = (
    "/static/",
    f"{settings.EXPLORER_PATH}/static/",
)


def _is_public(path: str) -> bool:
    # 静态资源放行
    for prefix in STATIC_PREFIXES:
        if path.startswith(prefix):
            return True
    # 应用级白名单
    for p in PUBLIC_PATHS:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _parse_cookie(scope) -> dict:
    cookies = {}
    for name, value in scope.get("headers", []):
        if name == b"cookie":
            for chunk in value.decode().split(";"):
                if "=" in chunk:
                    k, v = chunk.strip().split("=", 1)
                    cookies[k] = v
            break
    return cookies


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")

        if _is_public(path):
            return await self.app(scope, receive, send)

        # ---------- 1. 认证 ----------
        cookies = _parse_cookie(scope)
        token = cookies.get(settings.SESSION_COOKIE)
        user_info = sessions.verify(token)

        if not user_info:
            logger.warning("unauthorized | type=%s | path=%s",
                           scope["type"], path)
            return await self._reject(scope, receive, send, path)

        # ---------- 2. 授权 ----------
        username = user_info["username"]
        role = user_info.get("role") or "user"
        dept = user_info.get("dept")
        resource_type = detect_resource_type(scope["type"], path)
        allowed, reason = engine.check(username, role, dept, path, resource_type)

        if not allowed:
            logger.warning("forbidden | user=%s | role=%s | dept=%s | type=%s | path=%s | reason=%s",
                           username, role, dept, resource_type, path, reason)
            return await self._reject(scope, receive, send, path, forbidden=True)

        # ---------- 3. 注入 state ----------
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["user"] = username
        scope["state"]["role"] = role
        scope["state"]["dept"] = dept
        return await self.app(scope, receive, send)

    async def _reject(self, scope, receive, send, path: str,
                      forbidden: bool = False) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        status = 403 if forbidden else 401
        if path.startswith(settings.REST_API_PREFIX):
            resp = JSONResponse(
                {"error": "forbidden" if forbidden else "unauthorized"},
                status_code=status,
            )
        elif forbidden:
            resp = PlainTextResponse(
                "403 无权限访问该资源，请联系管理员。",
                status_code=403,
            )
        else:
            resp = RedirectResponse(f"{settings.LOGIN_PATH}?next={path}")
        await resp(scope, receive, send)