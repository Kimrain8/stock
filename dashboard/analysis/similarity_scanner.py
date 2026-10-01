"""相似度扫描 — 用样本画像找出当前市场里相似的股票。

匹配策略（方案 C）：
    1. 硬过滤：当前值必须落在样本分位区间内（可外扩）
    2. 软排序：离中位数的标准化距离越小得分越高
"""
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("stock2.similarity")

# 硬过滤的外扩系数（0.2 = 允许超出区间 20% 范围）
EXPAND_RATIO = 0.20

# 特征最小有效样本数（低于这个数则跳过该特征）
MIN_VALID_FOR_FEATURE = 3


def _expand_range(q25: float, q75: float, ratio: float = EXPAND_RATIO):
    """外扩分位区间。"""
    if not np.isfinite(q25) or not np.isfinite(q75):
        return None
    span = q75 - q25
    if span <= 0:
        span = abs(q25) * 0.1 if q25 != 0 else 1.0
    return q25 - span * ratio, q75 + span * ratio


def scan_similar(
    universe: pd.DataFrame,
    profile: dict,
    top_n: int = 50,
    weight_map: dict | None = None,
) -> pd.DataFrame:
    """在当前市场里扫描与画像相似的股票。

    参数：
        universe: 当前市场特征库，列 = [ts_code, name] + FEATURE_NAMES
        profile: build_profile 的输出
        top_n: 返回前 N 个
        weight_map: 特征权重 dict（默认用 FEATURE_WEIGHTS）

    返回：
        DataFrame [ts_code, name, score, matched_features, ...]
    """
    if universe is None or universe.empty:
        return pd.DataFrame()

    if weight_map is None:
        from .feature_extractor import FEATURE_WEIGHTS
        weight_map = FEATURE_WEIGHTS

    # ---- 样本中出现的特征 ----
    feat_names = [
        f for f in weight_map.keys()
        if f in profile["features"]
        and profile["features"][f]["missing_ratio"] < 0.5
    ]

    if not feat_names:
        logger.warning("no valid features in profile")
        return pd.DataFrame()

    # ---- 计算每行的过滤和得分 ----
    rows = []
    for _, r in universe.iterrows():
        ts_code = r.get("ts_code", "")
        name = r.get("name", "")

        total_score = 0.0
        total_weight = 0.0
        matched = []
        failed = []

        for feat in feat_names:
            v = r.get(feat)
            if pd.isna(v):
                failed.append(feat)
                continue

            f = profile["features"][feat]
            q25, med, q75 = f["q25"], f["median"], f["q75"]

            rng = _expand_range(q25, q75)
            if rng is None:
                continue

            lo, hi = rng
            w = weight_map.get(feat, 0.0)

            # 硬过滤
            if not (lo <= v <= hi):
                failed.append(feat)
                continue

            # 软得分：离中位数越近得分越高
            std = f["std"] if np.isfinite(f["std"]) and f["std"] > 0 else 1.0
            dist = abs(v - med) / std
            feat_score = 1.0 / (1.0 + dist)

            total_score += feat_score * w
            total_weight += w
            matched.append(feat)

        if total_weight > 0:
            final_score = total_score / total_weight
        else:
            final_score = 0.0

        rows.append({
            "ts_code": ts_code,
            "name": name,
            "score": round(final_score, 4),
            "matched_count": len(matched),
            "failed_count": len(failed),
            "matched_features": ",".join(matched),
            **{f: r.get(f) for f in feat_names},
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # 排序：先按匹配数降序，再按得分降序
    df = df.sort_values(
        ["matched_count", "score"], ascending=[False, False],
    ).head(top_n).reset_index(drop=True)

    logger.info("scan done | universe=%d | matched=%d | top_n=%d",
                len(universe), len(df), top_n)
    return df