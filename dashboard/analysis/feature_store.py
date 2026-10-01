"""特征库 — 离线预计算全市场特征，在线快速读取。

缓存路径：
    cache/features/{YYYYMMDD}.pkl
    cache/features/_meta.json
"""
import logging
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("stock2.feature_store")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
FEATURES_DIR = BASE_DIR / "cache" / "features"


# ============================================================
# 读写
# ============================================================
def save_features(df: pd.DataFrame, tag: str | None = None) -> Path:
    """保存特征库。

    参数：
        df: 列 = [ts_code, name] + FEATURE_NAMES
        tag: 文件名后缀，默认用日期 YYYYMMDD
    """
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    tag = tag or datetime.now().strftime("%Y%m%d")
    path = FEATURES_DIR / f"{tag}.pkl"
    with path.open("wb") as f:
        pickle.dump(df, f)
    logger.info("features saved | %s | rows=%d", path.name, len(df))
    return path


def load_features(tag: str | None = None) -> pd.DataFrame | None:
    """加载特征库。tag=None 时加载最新一份。"""
    if not FEATURES_DIR.exists():
        return None

    if tag:
        path = FEATURES_DIR / f"{tag}.pkl"
        if not path.exists():
            return None
    else:
        files = sorted(
            FEATURES_DIR.glob("*.pkl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return None
        path = files[0]

    try:
        with path.open("rb") as f:
            df = pickle.load(f)
        logger.info("features loaded | %s | rows=%d", path.name, len(df))
        return df
    except Exception as e:
        logger.warning("features load failed | %s", e)
        return None


def list_feature_snapshots() -> list[dict]:
    """列出所有特征快照。"""
    if not FEATURES_DIR.exists():
        return []
    out = []
    for p in sorted(FEATURES_DIR.glob("*.pkl"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        out.append({
            "tag": p.stem,
            "file": p.name,
            "size_kb": round(p.stat().st_size / 1024, 1),
            "updated_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
        })
    return out


# ============================================================
# 从样本列表提取"特征画像"
# ============================================================
def build_profile(samples: pd.DataFrame) -> dict:
    """从样本特征生成画像。

    参数：
        samples: 行 = 样本，列 = FEATURE_NAMES

    返回：
        {
            "count": N,
            "features": {
                特征名: {
                    "q25": ..., "median": ..., "q75": ...,
                    "mean": ..., "std": ...,
                    "min": ..., "max": ...,
                    "missing_ratio": ...,
                }
            }
        }
    """
    profile = {"count": len(samples), "features": {}}

    for name in samples.columns:
        s = pd.to_numeric(samples[name], errors="coerce")
        valid = s.dropna()

        if len(valid) == 0:
            profile["features"][name] = {
                "q25": np.nan, "median": np.nan, "q75": np.nan,
                "mean": np.nan, "std": np.nan,
                "min": np.nan, "max": np.nan,
                "missing_ratio": 1.0,
            }
            continue

        profile["features"][name] = {
            "q25": float(valid.quantile(0.25)),
            "median": float(valid.median()),
            "q75": float(valid.quantile(0.75)),
            "mean": float(valid.mean()),
            "std": float(valid.std()),
            "min": float(valid.min()),
            "max": float(valid.max()),
            "missing_ratio": float(s.isna().mean()),
        }

    return profile