"""特征提取器 — 从原始数据里提取 9 个特征。

用于：
    1. 从历史样本（如 20 日涨幅 >40% 的股票）提取"起涨前"特征
    2. 从当前市场股票提取"最新"特征
"""
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger("stock2.features")

FEATURE_NAMES = [
    "price_level",
    "price_percentile_250d",
    "volatility_60d",
    "turnover_20d",
    "volume_ratio_20_60",
    "amplitude_20d",
    "holder_num_change",
    "holder_num_trend_4q",
    "market_cap",
]

# 权重（用于相似度排序）
FEATURE_WEIGHTS = {
    # 价格低位 35%
    "price_level": 0.15,
    "price_percentile_250d": 0.15,
    "volatility_60d": 0.05,
    # 量能温和 30%
    "turnover_20d": 0.12,
    "volume_ratio_20_60": 0.10,
    "amplitude_20d": 0.08,
    # 筹码集中 25%
    "holder_num_change": 0.15,
    "holder_num_trend_4q": 0.10,
    # 基础属性 10%
    "market_cap": 0.10,
}


# ============================================================
# 单只股票、指定日期的特征提取
# ============================================================
def extract_features(
    ts_code: str,
    quotes: pd.DataFrame,
    holder: pd.DataFrame | None,
    as_of_date: datetime,
) -> dict:
    """提取 as_of_date 之前的特征。

    参数：
        ts_code: 股票代码
        quotes: 行情 DataFrame，含 date, open, high, low, close, volume
        holder: 股东户数 DataFrame（可 None）
        as_of_date: 特征计算日（只使用 <= 该日期的数据）

    返回：
        {特征名: 值}，缺失值用 NaN
    """
    features = {name: np.nan for name in FEATURE_NAMES}

    if quotes is None or quotes.empty:
        return features

    df = quotes.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] <= pd.Timestamp(as_of_date)].sort_values("date")

    if len(df) < 60:
        return features

    # ---------- 1. price_level ----------
    features["price_level"] = float(df["close"].iloc[-1])

    # ---------- 2. price_percentile_250d ----------
    c250 = df["close"].tail(250)
    if len(c250) >= 20:
        # 当前价在 250 日价格序列中的分位
        features["price_percentile_250d"] = float(
            (c250 <= c250.iloc[-1]).mean() * 100
        )

    # ---------- 3. volatility_60d ----------
    ret = df["close"].pct_change().tail(60)
    if len(ret) >= 20 and ret.std() > 0:
        features["volatility_60d"] = float(ret.std() * np.sqrt(252) * 100)

    # ---------- 4. turnover_20d ----------
    # 换手率 = 成交量 / 流通股。若无流通股，用成交量相对基准近似
    if "turnover" in df.columns:
        features["turnover_20d"] = float(df["turnover"].tail(20).mean() * 100)
    elif "float_share" in df.columns:
        t = df["volume"].tail(20) / df["float_share"].tail(20).replace(0, np.nan)
        features["turnover_20d"] = float(t.mean() * 100)
    else:
        # 无数据时用 volume 均值占 250 日均量的比值近似
        v20 = df["volume"].tail(20).mean()
        v250 = df["volume"].tail(250).mean() if len(df) >= 250 else v20
        if v250 > 0:
            features["turnover_20d"] = float(v20 / v250 * 100)

    # ---------- 5. volume_ratio_20_60 ----------
    v20 = df["volume"].tail(20).mean()
    v60 = df["volume"].tail(60).mean()
    if v60 > 0:
        features["volume_ratio_20_60"] = float(v20 / v60)

    # ---------- 6. amplitude_20d ----------
    if "high" in df.columns and "low" in df.columns:
        prev_close = df["close"].shift(1)
        amp = (df["high"] - df["low"]) / prev_close.replace(0, np.nan)
        features["amplitude_20d"] = float(amp.tail(20).mean() * 100)

    # ---------- 7. market_cap ----------
    # 用 close * float_share 计算流通市值（若可得）
    if "float_share" in df.columns:
        fs = df["float_share"].iloc[-1]
        if pd.notna(fs) and fs > 0:
            features["market_cap"] = float(df["close"].iloc[-1] * fs / 1e8)

    # ---------- 8. holder_num_change ----------
    if holder is not None and not holder.empty:
        h = holder.copy()
        h["股东户数统计截止日"] = pd.to_datetime(h["股东户数统计截止日"])
        h = h[h["股东户数统计截止日"] <= pd.Timestamp(as_of_date)]
        h = h.sort_values("股东户数统计截止日")

        if len(h) >= 2:
            latest = float(h.iloc[-1]["股东户数-本次"])
            prev = float(h.iloc[-2]["股东户数-本次"])
            if prev > 0:
                features["holder_num_change"] = (latest / prev - 1) * 100

        if len(h) >= 5:
            latest = float(h.iloc[-1]["股东户数-本次"])
            base = float(h.iloc[-5]["股东户数-本次"])
            if base > 0:
                features["holder_num_trend_4q"] = (latest / base - 1) * 100
        elif len(h) >= 2:
            latest = float(h.iloc[-1]["股东户数-本次"])
            base = float(h.iloc[0]["股东户数-本次"])
            if base > 0:
                features["holder_num_trend_4q"] = (latest / base - 1) * 100

    return features


# ============================================================
# 批量提取：用于离线预计算
# ============================================================
def extract_batch(
    data_dict: dict,
    as_of_date: datetime | None = None,
) -> dict:
    """从 fetch_all 的结果里提取特征。

    as_of_date=None 表示用最新数据；否则用指定日期之前的数据。
    """
    ts_code = data_dict.get("ts_code", "")
    quotes = data_dict.get("quotes")
    holder = data_dict.get("holder")

    if as_of_date is None:
        as_of_date = datetime.now()

    return extract_features(ts_code, quotes, holder, as_of_date)