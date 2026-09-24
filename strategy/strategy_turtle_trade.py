# -*- coding: utf-8 -*-
"""海龟交易选股策略 (20 日新高突破 + 成交额过亿 + 阳线防诱多) 。

源项目: https://github.com/sngyai/Sequoia-X  (TurtleTradeStrategy) 
本文件为独立可运行版本, 不依赖 sequoia_x 包。

选股条件 (全部命中才入选) :
    1. 突破新高:  今日 close > 前 20 个交易日 high 的最大值
    2. 流动性:    今日 turnover (成交额) > 100,000,000 元 (1 亿) 
    3. 防诱多:    今日必须是实体阳线 (close > open) 
    4. 真涨:      今日 close > 昨日 close (排除假阳线) 

输出排序 (可选) :
    - 默认按流通市值从大到小排序 (需 baostock 实时查询, 见下) 
    - 无 baostock 时降级为按当日成交额从大到小排序

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
    turnover  成交额(元)              120_000_000   ← 必填 (流动性过滤) 

数据要求:
    - 按 date 升序排列
    - 至少 21 根日 K 线 (20 日窗口 + 当日) 
    - 支持多只股票, run() 会自动按 symbol 分组

可选输入 (仅排序用, 不影响选股结果) :
    - baostock 已安装且联网时, 可通过流通市值排序
       (用不复权收盘价 × 流通股本, 流通股本 = 成交量/(换手率%/100)) 

运行方式:
    python turtle_trade_strategy.py
"""

from __future__ import annotations

import pandas as pd

MIN_BARS = 21  # 至少需要 21 根 K 线 (20 日窗口 + 当日) 


def _sort_by_market_cap(symbols: list[str]) -> list[str]:
    """通过 baostock 查询候选股票流通市值并降序排序 (与源项目一致) 。

    无法查询 (未安装 baostock / 网络失败 / 数据缺失) 时返回原顺序。

    Args:
        symbols: 待排序的股票代码列表。

    Returns:
        按流通市值降序排列的股票代码列表。
    """
    try:
        from datetime import date

        import baostock as bs
    except ImportError:
        return symbols

    today_str = date.today().strftime("%Y-%m-%d")
    market_caps: dict[str, float] = {}

    try:
        bs.login()
        for symbol in symbols:
            prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
            rs = bs.query_history_k_data_plus(
                f"{prefix}.{symbol}",
                "close,volume,turn",
                start_date=today_str,
                end_date=today_str,
                frequency="d",
                adjustflag="3",  # 不复权, 真实价格
            )
            while rs.next():
                row = rs.get_row_data()
                try:
                    close = float(row[0])
                    volume = float(row[1])
                    turn = float(row[2])
                    if turn > 0:
                        circulating_shares = volume / (turn / 100)
                        market_caps[symbol] = circulating_shares * close
                except (ValueError, ZeroDivisionError):
                    continue
    except Exception:
        return symbols
    finally:
        try:
            bs.logout()
        except Exception:
            pass

    return sorted(symbols, key=lambda s: market_caps.get(s, 0), reverse=True)


def run(df: pd.DataFrame, sort_by_market_cap: bool = True) -> list[str]:
    """执行海龟突破选股逻辑。

    Args:
        df: 标准 OHLCV 行情 DataFrame (见模块 docstring) 。
        sort_by_market_cap: 是否尝试用 baostock 流通市值排序。
            无 baostock 时自动降级为按成交额排序。

    Returns:
        满足条件的股票代码列表, 按流通市值 (或成交额) 降序。
    """
    required = ["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"输入缺少必要列: {missing}")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"])

    candidates: list[str] = []

    for symbol, g in df.groupby("symbol", sort=False):
        g = g.reset_index(drop=True)
        if len(g) < MIN_BARS:
            continue

        # 向量化：前 20 日 high 的滚动最大值 (不含当日, shift(1) 后 rolling(20)) 
        g["high_20"] = g["high"].shift(1).rolling(20).max()

        last = g.iloc[-1]
        prev = g.iloc[-2]
        if pd.isna(last["high_20"]):
            continue

        # 核心条件 1：突破前 20 天最高点
        breakout = last["close"] > last["high_20"]
        # 核心条件 2：流动性过亿 (成交额 > 1 亿) 
        liquid = last["turnover"] > 100_000_000
        # 防守条件：实体阳线 + 真涨 (排除高开低走诱多) 
        is_yang = last["close"] > last["open"]
        is_up = last["close"] > prev["close"]

        if breakout and liquid and is_yang and is_up:
            candidates.append(symbol)

    # 排序：优先流通市值, 降级为成交额
    if candidates:
        if sort_by_market_cap:
            candidates = _sort_by_market_cap(candidates)
        else:
            last_turnover = (
                df[df["symbol"].isin(candidates)]
                .groupby("symbol")["turnover"]
                .last()
                .to_dict()
            )
            candidates.sort(key=lambda s: last_turnover.get(s, 0), reverse=True)

    return candidates


def _demo_data() -> pd.DataFrame:
    """构造演示数据：一只今日创 20 日新高的放量阳线股 + 一只不满足的。"""
    import numpy as np

    rng = np.random.default_rng(7)
    rows: list[dict] = []

    for symbol in ["000001", "600519"]:
        base = 10.0 if symbol == "000001" else 100.0
        closes = [base + rng.uniform(-0.15, 0.15) for _ in range(20)]
        closes += [base * 1.10]  # 最后一日突破前 20 日高点

        opens = [c * rng.uniform(0.99, 1.01) for c in closes]
        opens[-1] = closes[-1] * 0.99  # 今日低开高走 → 实体阳线且真涨
        highs = [max(o, c) * 1.01 for o, c in zip(opens, closes)]
        lows = [min(o, c) * 0.99 for o, c in zip(opens, closes)]
        volumes = [1_000_000 + rng.uniform(-50_000, 50_000) for _ in range(21)]

        for i in range(21):
            rows.append({
                "symbol": symbol,
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i),
                "open": opens[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],
                "volume": volumes[i],
                "turnover": closes[i] * volumes[i] * (15.0 if symbol == "000001" else 0.01),
                # 000001 成交额 > 1 亿 (11元×100万股×15 ≈ 1.65亿) , 600519 < 1 亿
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    demo = _demo_data()
    result = run(demo, sort_by_market_cap=False)
    print(f"选出的股票: {result}")
