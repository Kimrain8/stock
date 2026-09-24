# -*- coding: utf-8 -*-
"""均线 + 成交量选股策略 (MA5 金叉 MA20 + 放量确认) 。
源项目: https://github.com/sngyai/Sequoia-X  (MaVolumeStrategy) 
本文件为独立可运行版本，不依赖 sequoia_x 包。

选股条件（全部命中才入选）:
    1. 金叉:  昨日 MA5 <= 昨日 MA20, 且今日 MA5 > 今日 MA20
               （即 5 日均线今日上穿 20 日均线）
    2. 放量:  今日成交量 > 过去 20 日平均成交量 × 1.5

────────────────────────────────────────────────────────────
输入信息 (DataFrame, 列名必须完全一致) :
    列名      含义                    示例
    symbol    股票代码                "000001"
    date      交易日期(YYYY-MM-DD)    "2025-01-01"
    open      开盘价                  10.50
    high      最高价                  10.80
    low       最低价                  10.30
    close     收盘价                  10.60
    volume    成交量(股)              1_200_000
    turnover  成交额(元)              12_600_000

数据要求:
    - 单只股票按 date 升序排列
    - 至少 21 根日 K 线 (20 日均线窗口 + 当日）
    - 支持多只股票, run() 会自动按 symbol 分组

运行方式:
    python ma_volume_strategy.py
"""

from __future__ import annotations

import pandas as pd


def run(df: pd.DataFrame) -> list[str]:
    """执行选股逻辑，返回满足金叉+放量条件的股票代码列表。

    Args:
        df: 标准 OHLCV 行情 DataFrame (见模块 docstring) 。

    Returns:
        满足条件的股票代码列表，如 ["000001", "600519"]。
    """
    # 基础校验
    required = ["symbol", "date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"输入缺少必要列: {missing}")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"])

    selected: list[str] = []

    for symbol, g in df.groupby("symbol", sort=False):
        if len(g) < 21:
            continue

        # 向量化计算均线（与源项目一致，不使用 iterrows）
        g = g.reset_index(drop=True)
        g["ma5"] = g["close"].rolling(5).mean()
        g["ma20"] = g["close"].rolling(20).mean()
        g["vol_ma20"] = g["volume"].rolling(20).mean()

        # 取最后两行判断金叉（与源项目一致：昨日严格 <，今日严格 >）
        last = g.iloc[-1]
        prev = g.iloc[-2]
        if pd.isna(last["ma5"]) or pd.isna(prev["ma5"]) or pd.isna(last["ma20"]) or pd.isna(prev["ma20"]):
            continue

        golden_cross = (prev["ma5"] < prev["ma20"]) and (last["ma5"] > last["ma20"])
        volume_surge = last["volume"] > last["vol_ma20"] * 1.5

        if golden_cross and volume_surge:
            selected.append(symbol)

    return selected


def _demo_data() -> pd.DataFrame:
    """构造演示数据：构造一只"金叉+放量"的股票和一只不满足条件的股票。"""
    import numpy as np

    rng = np.random.default_rng(42)
    rows: list[dict] = []

    for symbol in ["000001", "600519"]:
        # 000001: 15 天 10.0 → 5 天 9.8（短期均线低于长期）→ 最后一日跳涨 10.8
        #          （昨日 MA5 9.8 < 昨日 MA20 9.95，今日 MA5 10.0 > 今日 MA20 9.99：严格金叉）
        #          + 最后一日放量 → 入选
        # 600519: 同样的价格形态，但最后一日缩量 → 金叉但无量，不入选
        is_hit = symbol == "000001"
        base = 10.0 if symbol == "000001" else 100.0
        closes = [base] * 15 + [base * 0.98] * 5 + [base * 1.08]

        volume = [1_000_000 + rng.uniform(-50_000, 50_000) for _ in range(20)]
        volume += [4_000_000 if is_hit else 1_000_000]  # 放量(>1.5x) 或缩量

        for i, c in enumerate(closes):
            rows.append({
                "symbol": symbol,
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i),
                "open": c * 0.99,
                "high": c * 1.02,
                "low": c * 0.98,
                "close": c,
                "volume": volume[i],
                "turnover": c * volume[i],
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    demo = _demo_data()
    result = run(demo)
    print(f"选出的股票: {result}")
