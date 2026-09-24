# -*- coding: utf-8 -*-
"""上升趋势跌停选股策略（趋势中放量跌停，捕捉错杀机会）。
源项目: https://github.com/sngyai/Sequoia-X （UptrendLimitDownStrategy）
本文件为独立可运行版本，不依赖 sequoia_x 包。

选股条件（全部命中才入选）:
    1. 上升趋势:  昨日 20 日均线 > 昨日 60 日均线
    2. 跌停:      今日 close <= 昨日 close × 0.905（约 -10% 跌停）
    3. 放量:      今日 volume > 20 日均量 × 2.0

逻辑说明:
    - 均线用收盘价计算: MA20 = close.rolling(20).mean()，
      MA60 = close.rolling(60).mean()
    - 跌停阈值 0.905 对应 10% 跌停板；20cm 股票请改为 0.805

────────────────────────────────────────────────────────────
输入信息（DataFrame，列名必须完全一致）:
    列名      含义                    示例
    symbol    股票代码                "000001"
    date      交易日期(YYYY-MM-DD)    "2025-01-01"
    open      开盘价                  10.50
    high      最高价                  10.80
    low       最低价                  10.30
    close     收盘价                  10.60
    volume    成交量(股)              5_000_000
    turnover  成交额(元)              50_000_000

数据要求:
    - 按 date 升序排列
    - 至少 61 根日 K 线（60 日均线 + 当日）
    - 支持多只股票，run() 会自动按 symbol 分组

运行方式:
    python uptrend_limit_down_strategy.py
"""

from __future__ import annotations

import pandas as pd

MIN_BARS = 60  # 至少需要 60 根 K 线（60 日均线）

# 跌停判定阈值：-10% 跌停约 0.905；20cm 股票请改为 0.805
LIMIT_DOWN_RATIO = 0.905


def run(df: pd.DataFrame, limit_down_ratio: float = LIMIT_DOWN_RATIO) -> list[str]:
    """执行上升趋势跌停选股逻辑。

    Args:
        df: 标准 OHLCV 行情 DataFrame（见模块 docstring）。
        limit_down_ratio: 跌停判定阈值，默认 0.905（10% 板）。

    Returns:
        满足条件的股票代码列表。
    """
    required = ["symbol", "date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"输入缺少必要列: {missing}")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"])

    selected: list[str] = []

    for symbol, g in df.groupby("symbol", sort=False):
        g = g.reset_index(drop=True)
        if len(g) < MIN_BARS:
            continue

        # 向量化计算均线
        g["ma20"] = g["close"].rolling(20).mean()
        g["ma60"] = g["close"].rolling(60).mean()
        g["vol_ma20"] = g["volume"].rolling(20).mean()

        prev = g.iloc[-2]  # 昨日
        today = g.iloc[-1]  # 今日

        if pd.isna(prev["ma20"]) or pd.isna(prev["ma60"]) or pd.isna(today["vol_ma20"]):
            continue

        # 条件 1：上升趋势（昨日 MA20 > 昨日 MA60）
        uptrend = prev["ma20"] > prev["ma60"]
        # 条件 2：跌停 + 放量（> 20 日均量 2 倍）
        limit_down = today["close"] <= prev["close"] * limit_down_ratio
        volume_surge = today["volume"] > today["vol_ma20"] * 2.0

        if uptrend and limit_down and volume_surge:
            selected.append(symbol)

    return selected


def _demo_data() -> pd.DataFrame:
    """构造演示数据：一只 60 日上升趋势中最后一日放量跌停的股票。"""
    import numpy as np

    rng = np.random.default_rng(5)
    rows: list[dict] = []

    for symbol in ["000001", "600519"]:
        if symbol == "000001":
            # 阶段1(60天)：缓慢上升趋势（MA20 > MA60）→ 满足上升趋势
            trend = np.linspace(10.0, 15.0, 60) * rng.uniform(0.995, 1.005, 60)
        else:
            # 600519：下降趋势（MA20 < MA60）→ 不满足上升趋势，作为反例
            trend = np.linspace(15.0, 10.0, 60) * rng.uniform(0.995, 1.005, 60)
        # 阶段2(1天)：放量跌停（-10%）
        closes = np.concatenate([trend, [trend[-1] * 0.90]])

        volumes = np.concatenate([rng.uniform(1_000_000, 1_500_000, 60), [4_000_000]])

        for i, c in enumerate(closes):
            rows.append({
                "symbol": symbol,
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i),
                "open": c * rng.uniform(0.99, 1.01),
                "high": c * 1.01,
                "low": c * 0.99,
                "close": c,
                "volume": volumes[i],
                "turnover": c * volumes[i],
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    demo = _demo_data()
    result = run(demo)
    print(f"选出的股票: {result}")
