# -*- coding: utf-8 -*-
"""RPS 极强动量突破选股策略（欧奈尔相对强度 + 接近新高）。

源项目: https://github.com/sngyai/Sequoia-X （RpsBreakoutStrategy）
本文件为独立可运行版本，不依赖 sequoia_x 包。

选股条件（全部命中才入选）:
    1. RPS 极强:  最近 120 个交易日区间涨幅在全市场百分位排名 >= 90
                   （RPS = (close - close_120日前) / close_120日前 的横向百分位）
    2. 接近新高:  今日 close >= 滚动 120 日最高价 × 0.90
                   （收盘价距 120 日高点回撤不超过 10%）

与其它策略的关键差异（输入信息）:
    - RPS 是"横向截面"指标，必须输入【全市场】多只股票 × 多日的历史数据，
      才能正确计算百分位排名；只给单只股票无法选股。
    - 内部按每个交易日计算全市场涨幅百分位，再取最新交易日结果。

────────────────────────────────────────────────────────────
输入信息（DataFrame，列名必须完全一致，需覆盖全市场截面）:
    列名      含义                    示例
    symbol    股票代码                "000001"
    date      交易日期(YYYY-MM-DD)    "2025-01-01"
    close     收盘价(后复权最佳)      10.60
    high      最高价                  10.80
    volume    成交量(股)              1_200_000    （非必需，可不传）

数据要求:
    - 包含尽可能多的股票（全市场 A 股约 5000+ 只，至少需数百只才有排名意义）
    - 每只股票至少 121 根日 K 线（120 日涨幅窗口 + 当日），
      不足的股票会被自动剔除（min_periods 允许 60 根）
    - 同一股票内按 date 升序

运行方式:
    python rps_breakout_strategy.py
"""

from __future__ import annotations

import pandas as pd


def run(df: pd.DataFrame, rps_period: int = 120, rps_threshold: float = 90.0) -> list[str]:
    """执行 RPS 极强动量突破选股逻辑。

    Args:
        df: 全市场日线 DataFrame（见模块 docstring）。
        rps_period: 动量回看窗口，默认 120 个交易日。
        rps_threshold: RPS 百分位门槛，默认 90（前 10%）。

    Returns:
        满足条件的股票代码列表。
    """
    required = ["symbol", "date", "close", "high"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"输入缺少必要列: {missing}")

    if df.empty:
        return []

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"])

    # ── 纵向计算区间涨幅 ──
    df["close_shift"] = df.groupby("symbol")["close"].shift(rps_period)
    df["pct_change"] = (df["close"] - df["close_shift"]) / df["close_shift"]

    # 取最新交易日的横截面
    latest_date = df["date"].max()
    latest_df = df[df["date"] == latest_date].copy()
    latest_df = latest_df.dropna(subset=["pct_change"])

    if latest_df.empty:
        return []

    # ── 横向百分位排名（RPS）──
    latest_df["rps"] = latest_df["pct_change"].rank(pct=True) * 100
    strong_stocks = latest_df[latest_df["rps"] >= rps_threshold].copy()

    # ── 滚动 120 日最高价（min_periods=60，允许新股）──
    roll_high = (
        df.groupby("symbol")["high"]
        .rolling(window=rps_period, min_periods=rps_period // 2)
        .max()
        .reset_index(level=0, drop=True)
    )
    df["roll_high"] = roll_high

    latest_roll_high = df[df["date"] == latest_date][["symbol", "roll_high"]]
    strong_stocks = strong_stocks.merge(latest_roll_high, on="symbol")
    strong_stocks = strong_stocks.dropna(subset=["roll_high"])

    # ── 接近新高判定：收盘价 >= 120 日最高价 × 0.90 ──
    breakout_condition = strong_stocks["close"] >= strong_stocks["roll_high"] * 0.90
    selected = strong_stocks[breakout_condition]

    return selected["symbol"].tolist()


def _demo_data() -> pd.DataFrame:
    """构造演示数据：50 只股票 × 130 日，其中 2 只动量极强且接近新高。"""
    import numpy as np

    rng = np.random.default_rng(2024)
    n_stocks, n_days = 50, 130
    rows: list[dict] = []

    for i in range(n_stocks):
        symbol = f"{600000 + i:06d}"
        # 大部分股票温和上涨 5%；最后 2 只暴涨 80%（RPS 靠前）
        # 用确定性趋势（无噪声），保证只有最后 2 只 RPS >= 90
        if i >= n_stocks - 2:
            trend = np.linspace(10.0, 18.0, n_days)  # +80%
        else:
            trend = np.linspace(10.0, 10.5, n_days)  # +5%
        closes = trend

        for j, c in enumerate(closes):
            rows.append({
                "symbol": symbol,
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=j),
                "close": c,
                "high": c * 1.01,
                "volume": 1_000_000 + rng.integers(0, 200_000),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    demo = _demo_data()
    result = run(demo)
    print(f"选出的股票: {result}")
