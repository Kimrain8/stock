"""日志配置：控制台 + 文件轮转"""
import logging
import logging.handlers
from .config import settings


def setup_logging() -> None:
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    # 清除已有 handler，避免热重载时重复输出
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # 文件（10MB × 5 份）
    fh = logging.handlers.RotatingFileHandler(
        settings.LOG_DIR / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 降噪：这些库在 DEBUG 下会刷屏
    for noisy in ("uvicorn.access", "panel", "bokeh", "tornado", "watchfiles", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)