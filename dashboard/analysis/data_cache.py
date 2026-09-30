import json
import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

"""数据缓存层 — 把抓取结果落地到本地，避免重复请求。
目录：
    cache/data/
    ├── 688508.SH.pkl
    ├── 002074.SZ.pkl
    └── _meta.json        记录各股票的最后更新时间
用法：
    from dashboard.analysis.data_cache import get_data, refresh_cache

    data = get_data("688508.SH")            # 24h 内有效
    data = get_data("688508.SH", max_age_hours=1)   # 1h 内有效
    data = refresh_cache("688508.SH")        # 强制刷新
"""

logger = logging.getLogger("stock2.data_cache")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = BASE_DIR / "cache" / "data"
META_FILE = CACHE_DIR / "_meta.json"


# ============================================================
# 元数据
# ============================================================
def _load_meta() -> dict:
    if not META_FILE.exists():
        return {}
    try:
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(meta: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _update_meta(ts_code: str, ts: datetime) -> None:
    meta = _load_meta()
    meta[ts_code] = {
        "updated_at": ts.isoformat(),
        "file": f"{ts_code}.pkl",
    }
    _save_meta(meta)


# ============================================================
# 路径
# ============================================================
def _pkl_path(ts_code: str) -> Path:
    return CACHE_DIR / f"{ts_code}.pkl"


def _sanitize_code(ts_code: str) -> str:
    """确保代码格式规范（大写、带后缀）。"""
    return str(ts_code).strip().upper()


# ============================================================
# 读写缓存
# ============================================================
def _read_cache(ts_code: str) -> Optional[dict]:
    path = _pkl_path(ts_code)
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            return pickle.load(f)
    except Exception as e:
        logger.warning("read cache failed | %s | %s", ts_code, e)
        return None


def _write_cache(ts_code: str, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _pkl_path(ts_code)
    try:
        with path.open("wb") as f:
            pickle.dump(data, f)
        _update_meta(ts_code, datetime.now())
        logger.info("cache saved | %s | %d bytes", ts_code, path.stat().st_size)
    except Exception as e:
        logger.warning("write cache failed | %s | %s", ts_code, e)


# ============================================================
# 过期判定
# ============================================================
def _is_expired(ts_code: str, max_age_hours: float) -> bool:
    meta = _load_meta()
    entry = meta.get(ts_code)
    if not entry:
        return True
    try:
        updated = datetime.fromisoformat(entry["updated_at"])
    except Exception:
        return True
    return datetime.now() - updated > timedelta(hours=max_age_hours)


# ============================================================
# 对外主接口
# ============================================================
def get_data(ts_code: str, max_age_hours: float = 24.0) -> dict:
    """获取数据。优先读缓存；缓存过期或不存在则抓取并保存。

    参数：
        ts_code: 股票代码，如 '688508.SH'
        max_age_hours: 缓存有效时长（小时），默认 24h

    返回：
        fetch_all() 的返回结构。
    """
    ts_code = _sanitize_code(ts_code)

    # 1. 缓存命中且未过期
    if not _is_expired(ts_code, max_age_hours):
        data = _read_cache(ts_code)
        if data is not None:
            logger.info("cache hit | %s | age < %.1fh", ts_code, max_age_hours)
            return data

    # 2. 抓取
    logger.info("cache miss | %s | fetching...", ts_code)
    from .data_fetcher import fetch_all
    data = fetch_all(ts_code)

    # 3. 写入缓存
    _write_cache(ts_code, data)
    return data


def refresh_cache(ts_code: str) -> dict:
    """强制刷新缓存。"""
    ts_code = _sanitize_code(ts_code)
    logger.info("refresh cache | %s", ts_code)
    from .data_fetcher import fetch_all
    data = fetch_all(ts_code)
    _write_cache(ts_code, data)
    return data


def clear_cache(ts_code: Optional[str] = None) -> int:
    """清理缓存。

    参数：
        ts_code: None 表示全部清理；否则只清指定股票

    返回：清理的文件数
    """
    if ts_code is None:
        count = 0
        for f in CACHE_DIR.glob("*.pkl"):
            f.unlink()
            count += 1
        if META_FILE.exists():
            META_FILE.unlink()
        logger.info("cache cleared | all | %d files", count)
        return count

    ts_code = _sanitize_code(ts_code)
    path = _pkl_path(ts_code)
    if path.exists():
        path.unlink()
    meta = _load_meta()
    meta.pop(ts_code, None)
    _save_meta(meta)
    logger.info("cache cleared | %s", ts_code)
    return 1


def list_cache() -> list[dict]:
    """列出所有缓存记录。"""
    meta = _load_meta()
    out = []
    for code, info in meta.items():
        path = _pkl_path(code)
        out.append({
            "ts_code": code,
            "updated_at": info.get("updated_at", ""),
            "size_kb": round(path.stat().st_size / 1024, 1) if path.exists() else 0,
        })
    return sorted(out, key=lambda x: x["updated_at"], reverse=True)