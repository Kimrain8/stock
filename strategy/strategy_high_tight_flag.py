# -*- coding: utf-8 -*-
"""高旗形整理选股策略（强动量 + 极度收敛 + 缩量）。

源项目: https://github.com/sngyai/Sequoia-X （HighTightFlagStrategy）
本文件为独立可运行版本，不依赖 sequoia_x 包。

选股条件（全部命中才入选）:
    1. 强动量:   过去 40 天区间最高价 / 区间最低价 > 1.6（涨幅超 60%）
    2. 极度收敛: 最近 10 天区间最高价 / 区间最低价 < 1.15（振幅低于 15%）
    3. 高位抗跌: 近 10 天最低点 >= 40 天最高点 × 0.8
                  （即回调不能太深，仍处高位）
    4. 缩量:     今日 volume < 过去 20 日 volume 均值 × 0.6

────────────────────────────────────────────────────────────
输入信息（DataFrame，列名必须完全一致）:
    列名      含义                    示例
    symbol    股票代码                "000001"
    date      交易日期(YYYY-MM-DD)    "2025-01-01"
    open      开盘价                  10.50
    high      最高价                  10.80
    low       最低价                  10.30
    close     收盘价                  10.60
    volume    成交量(股)              300_000
    turnover  成交额(元)              3_180_000

数据要求:
    - 按 date 升序排列
    - 至少 40 根日 K 线（40 日动量窗口）
    - 支持多只股票，run() 会自动按 symbol 分组

运行方式:
    python high_tight_flag_strategy.py
"""

from __future__ import annotations

import pandas as pd

MIN_BARS = 40  # 至少需要 40 根 K 线


def run(df: pd.DataFrame) -> list[str]:
    """执行高旗形整理选股逻辑。

    Args:
        df: 标准 OHLCV 行情 DataFrame（见模块 docstring）。

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

        tail40 = g.tail(40)
        tail10 = g.tail(10)

        high40 = tail40["high"].max()
        low40 = tail40["low"].min()
        high10 = tail10["high"].max()
        low10 = tail10["low"].min()

        if low40 == 0 or low10 == 0:
            continue

        # 条件 1：强动量（40 日涨幅超 60%）
        momentum = high40 / low40 > 1.6
        # 条件 2：极度收敛（10 日振幅低于 15%）
        consolidation = high10 / low10 < 1.15
        # 条件 3：高位抗跌（10 日低点不低于 40 日高点 80%）
        high_level = low10 >= high40 * 0.8
        # 条件 4：缩量（今日 < 前 20 日均量 0.6 倍，注意排除今日）
        vol_ma20 = g["volume"].iloc[-21:-1].mean()
        shrink = g["volume"].iloc[-1] < vol_ma20 * 0.6

        if momentum and consolidation and high_level and shrink:
            selected.append(symbol)

    return selected


def _demo_data() -> pd.DataFrame:
    """构造演示数据：一只先暴涨 40 天、后 10 天横盘收敛缩量的股票。"""
    import numpy as np

    rng = np.random.default_rng(11)
    rows: list[dict] = []

    for symbol in ["000001", "600519"]:
        # 阶段1(30天)：从 10 涨到 18（动量 > 1.6）
        rally = np.linspace(10.0, 18.0, 30) * rng.uniform(0.995, 1.005, 30)
        if symbol == "000001":
            # 阶段2(10天)：高位窄幅横盘（收敛 < 1.15）→ 满足收敛
            flat = 18.0 * rng.uniform(0.97, 1.03, 10)
        else:
            # 600519：高位宽幅震荡（收敛 >= 1.15）→ 不满足收敛，作为反例
            flat = 18.0 * rng.uniform(0.88, 1.10, 10)
        closes = np.concatenate([rally, flat])

        volumes = np.concatenate([
            rng.uniform(2_000_000, 3_000_000, 30),
            rng.uniform(150_000, 300_000, 10),  # 末段缩量
        ])

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
