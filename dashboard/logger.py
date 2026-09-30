
import logging
import logging.handlers
from .config import settings
"""日志配置：控制台 + 文件轮转 + 按资源类型分级"""


logger = logging.getLogger("stock2.logger")

# 幂等标记
_INITIALIZED = False


class _AsyncioConnectionResetFilter(logging.Filter):
    """过滤 Windows 上 asyncio 的 ConnectionResetError 噪音。"""
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "asyncio":
            msg = record.getMessage()
            if "ConnectionResetError" in msg and "10054" in msg:
                return False
        return True


def setup_logging() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = logging.handlers.RotatingFileHandler(
        settings.LOG_DIR / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 按资源类型分级
    for res_type, level in settings.RESOURCE_LOG_LEVELS.items():
        lg = logging.getLogger(f"stock2.{res_type}")
        lg.setLevel(level)
        logger.info("log level set | logger=stock2.%s | level=%s",
                    res_type, level)

    # 降噪
    for noisy in ("uvicorn.access", "panel", "bokeh", "tornado",
                  "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # 精确过滤 asyncio 的 ConnectionResetError
    logging.getLogger("asyncio").addFilter(_AsyncioConnectionResetFilter())

    # Bokeh 校验警告降级
    logging.getLogger("bokeh.core.validation.check").setLevel(logging.ERROR)

    logger.info("logging initialized | root_level=%s", settings.LOG_LEVEL)
    _INITIALIZED = True