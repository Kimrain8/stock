# -*- coding: utf-8 -*-
"""涨停洗盘选股策略（昨日涨停 → 今日放量收阴不破昨收）。

源项目: https://github.com/sngyai/Sequoia-X （LimitUpShakeoutStrategy）
本文件为独立可运行版本，不依赖 sequoia_x 包。

选股条件（全部命中才入选）:
    1. 昨日涨停:  昨日 close >= 前日 close × 1.095（约 10cm 涨停，
                   含创业板/科创板 20cm 股票时请按需调整阈值）
    2. 今日收阴:  今日 close < 今日 open
    3. 今日放量:  今日 volume > 昨日 volume × 2.0
    4. 支撑不破:  今日 low >= 昨日 close（洗盘不跌破昨收，守住涨停成果）

────────────────────────────────────────────────────────────
输入信息（DataFrame，列名必须完全一致）:
    列名      含义                    示例
    symbol    股票代码                "000001"
    date      交易日期(YYYY-MM-DD)    "2025-01-01"
    open      开盘价                  10.50
    high      最高价                  10.80
    low       最低价                  10.30
    close     收盘价                  10.60
    volume    成交量(股)              2_400_000
    turnover  成交额(元)              25_000_000

数据要求:
    - 按 date 升序排列
    - 至少 3 根日 K 线（前日、昨日、今日）
    - 支持多只股票，run() 会自动按 symbol 分组

运行方式:
    python limit_up_shakeout_strategy.py
"""

from __future__ import annotations

import pandas as pd

MIN_BARS = 3  # 至少需要 3 根 K 线（前日、昨日、今日）

# 涨停判定阈值：10% 涨停板约 1.095（考虑四舍五入），
# 20cm 股票（创业板 300/301、科创板 688）请改为 1.195
LIMIT_UP_RATIO = 1.095


def run(df: pd.DataFrame, limit_up_ratio: float = LIMIT_UP_RATIO) -> list[str]:
    """执行涨停洗盘选股逻辑。

    Args:
        df: 标准 OHLCV 行情 DataFrame（见模块 docstring）。
        limit_up_ratio: 涨停判定阈值，默认 1.095（10% 板）。

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

        prev2 = g.iloc[-3]  # 前日
        prev1 = g.iloc[-2]  # 昨日
        today = g.iloc[-1]  # 今日

        # 条件 1：昨日涨停
        limit_up_yesterday = prev1["close"] >= prev2["close"] * limit_up_ratio
        # 条件 2：今日收阴
        bearish_today = today["close"] < today["open"]
        # 条件 3：今日放量（> 昨日 2 倍）
        volume_surge = today["volume"] > prev1["volume"] * 2.0
        # 条件 4：支撑不破（今日最低价不低于昨日收盘价）
        support_hold = today["low"] >= prev1["close"]

        if limit_up_yesterday and bearish_today and volume_surge and support_hold:
            selected.append(symbol)

    return selected


def _demo_data() -> pd.DataFrame:
    """构造演示数据：昨日涨停、今日放量收阴但不破昨收的洗盘形态。"""
    rows: list[dict] = []

    # 000001：满足全部条件
    # 前日 10.00 → 昨日涨停 11.00（>=10*1.095）→ 今日 高开 11.20 收 11.05
    # （收阴但未破昨收：low 11.01 >= 11.00）
    rows += [
        {"symbol": "000001", "date": "2025-03-03", "open": 10.00, "high": 10.10, "low": 9.95, "close": 10.00, "volume": 1_000_000, "turnover": 10_000_000},
        {"symbol": "000001", "date": "2025-03-04", "open": 10.05, "high": 11.10, "low": 10.00, "close": 11.00, "volume": 1_200_000, "turnover": 13_000_000},
        {"symbol": "000001", "date": "2025-03-05", "open": 11.20, "high": 11.25, "low": 11.01, "close": 11.05, "volume": 3_000_000, "turnover": 33_000_000},
    ]

    # 600519：昨日涨停但今日跌破昨收（low 10.95 < 11.00，不满足条件 4）
    rows += [
        {"symbol": "600519", "date": "2025-03-03", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.0, "volume": 100_000, "turnover": 10_000_000},
        {"symbol": "600519", "date": "2025-03-04", "open": 100.5, "high": 111.0, "low": 100.0, "close": 110.0, "volume": 120_000, "turnover": 13_000_000},
        {"symbol": "600519", "date": "2025-03-05", "open": 111.0, "high": 110.5, "low": 109.5, "close": 109.8, "volume": 300_000, "turnover": 33_000_000},
    ]

    return pd.DataFrame(rows)


if __name__ == "__main__":
    demo = _demo_data()
    result = run(demo)
    print(f"选出的股票: {result}")
