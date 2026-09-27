"""集中配置，所有路径和凭据都从环境变量读取"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    def __init__(self):
        # ---- 服务器 ----
        self.HOST = os.getenv("APP_HOST", "127.0.0.1")
        self.PORT = int(os.getenv("APP_PORT", "8000"))
        
        # ---- 数据库 ----
        self.DB_NAME = os.getenv("APP_DB_NAME", "jhd_rd")
        # ---- 权限 ----
        # 未匹配任何规则时的默认策略：false=拒绝（推荐），true=允许
        self.DEFAULT_PERMISSION_ALLOW = (
            os.getenv("APP_DEFAULT_PERMISSION_ALLOW", "false").lower()
            in ("1", "true", "yes")
        )
        # 权限缓存时间（秒），修改权限后等待缓存过期生效
        self.PERMISSION_CACHE_TTL = int(os.getenv("APP_PERMISSION_CACHE_TTL", "60"))
        
        # ---- HTTPS ----
        self.USE_HTTPS = os.getenv("APP_USE_HTTPS", "false").lower() in ("1", "true", "yes")
        self.SSL_CERTFILE = os.getenv("APP_SSL_CERTFILE", str(BASE_DIR / "certs" / "cert.pem"))
        self.SSL_KEYFILE = os.getenv("APP_SSL_KEYFILE", str(BASE_DIR / "certs" / "key.pem"))

        # ---- 路径配置 ----
        self.REST_API_PREFIX = os.getenv("APP_REST_API_PREFIX", "/api")
        self.WS_API_SUBPATH = os.getenv("APP_WS_API_SUBPATH", "/ws")     # 相对 REST 前缀
        self.EXPLORER_PATH = os.getenv("APP_EXPLORER_PATH", "/explorer")
        self.HOME_PATH = "/"
        self.LOGIN_PATH = "/login"
        self.LOGOUT_PATH = "/logout"
        
        # ---- SSO 固定凭据 ----
        # self.USERNAME = os.getenv("APP_USERNAME", "kim")
        # self.PASSWORD = os.getenv("APP_PASSWORD", "kim168")
        self.SESSION_COOKIE = os.getenv("APP_SESSION_COOKIE", "stock2_session")
        self.SESSION_TTL_HOURS = int(os.getenv("APP_SESSION_TTL_HOURS", "8"))

        # ---- 日志 ----
        self.LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "INFO")
        self.LOG_DIR = Path(os.getenv("APP_LOG_DIR", str(BASE_DIR / "logs")))

        self.DEBUG = os.getenv("APP_DEBUG", "false").lower() in ("1", "true", "yes")
        self.DEBUG_SCOPE = os.getenv("APP_DEBUG_SCOPE", "false").lower() in ("1", "true", "yes")
        
        self.RESOURCE_LOG_LEVELS = {
            "api": os.getenv("APP_LOG_LEVEL_API", "WARNING"),
            "ws":  os.getenv("APP_LOG_LEVEL_WS",  "DEBUG"),
            "web": os.getenv("APP_LOG_LEVEL_WEB", "INFO"),
        }


settings = Settings()

# 派生：完整的 WebSocket 路径
settings.WS_API_PATH = f"{settings.REST_API_PREFIX}{settings.WS_API_SUBPATH}"