import logging
from collections import deque

import numpy as np
import pandas as pd
"""涨幅模式识别: N 周期内涨幅 ≥ P。
用法：
    from dashboard.data_ops.growth_patterns import find_growth_patterns
    # 自然日窗口
    hits = find_growth_patterns(df, patterns=((14, 0.4), (5, 0.2)),
                                mode='high', day_type='natural')
    # 交易日窗口
    hits = find_growth_patterns(df, patterns=((14, 0.4), (5, 0.2)),
                                mode='high', day_type='trading')

输出：[rule, ts_code, start_date, end_date, growth]
"""


logger = logging.getLogger("stock2.growth")


# ============================================================
# 单只股票扫描（单调队列 O(N)）
# ============================================================
def _scan_one_stock(dates: np.ndarray,
                    closes: np.ndarray,
                    max_days: int,
                    threshold: float,
                    day_type: str = "natural") -> list[tuple[int, int, float]]:
    """扫描单只股票，返回所有满足条件的 (j, i, growth)。

    参数：
        dates: datetime64[ns]，升序
        closes: float64，升序对应
        max_days: 窗口长度
        threshold: 涨幅阈值（0.4 表示 40%）
        day_type:
            'natural' → 窗口为 [dates[i] - (max_days-1)天, dates[i]]
            'trading' → 窗口为 [i - (max_days-1), i]（按索引往前数）

    返回：[(窗口最低价索引 j, 当前日索引 i, 涨幅 growth), ...]
    """
    n = len(closes)
    if n < 2 or max_days < 2:
        return []

    hits: list[tuple[int, int, float]] = []
    dq: deque[int] = deque()          # 单调递增队列（存索引）
    left = 0                          # 窗口左边界

    # 自然日模式：预处理 cutoff
    cutoff = np.timedelta64(max_days - 1, "D") if day_type == "natural" else None

    for i in range(n):
        # ---- 1. 更新 left ----
        if cutoff is not None:
            # 自然日：按时间淘汰
            while left < i and dates[left] < dates[i] - cutoff:
                left += 1
        else:
            # 交易日：按索引淘汰
            left = max(0, i - (max_days - 1))

        # ---- 2. 单调队列维护 ----
        while dq and closes[dq[-1]] > closes[i]:
            dq.pop()
        dq.append(i)
        while dq and dq[0] < left:
            dq.popleft()

        # ---- 3. 计算涨幅 ----
        j = dq[0]
        min_close = closes[j]
        if min_close <= 0:
            continue
        growth = closes[i] / min_close - 1.0
        if growth >= threshold:
            hits.append((j, i, float(growth)))

    return hits


# ============================================================
# 合并连续段
# ============================================================
def _merge_runs(hits: list[tuple[int, int, float]]
                ) -> list[tuple[int, int, float]]:
    """把连续满足条件的交易日合并成段，每段取涨幅最大那天。

    连续定义：两个 hits 的 i 索引相邻（即交易日相邻）。
    """
    if not hits:
        return []

    merged: list[tuple[int, int, float]] = []
    seg = [hits[0]]
    for h in hits[1:]:
        if h[1] == seg[-1][1] + 1:
            seg.append(h)
        else:
            merged.append(max(seg, key=lambda x: x[2]))
            seg = [h]
    merged.append(max(seg, key=lambda x: x[2]))
    return merged


# ============================================================
# 主入口
# ============================================================
def find_growth_patterns(
    df: pd.DataFrame,
    patterns: tuple[tuple[int, float], ...] = ((14, 0.4), (5, 0.2)),
    mode: str = "high",
    day_type: str = "natural",
) -> pd.DataFrame:
    """找出所有满足涨幅条件的股票。

    参数：
        df: 必须含列 [ts_code, trade_date, close_price]
        patterns: [(days, threshold), ...] 按优先级从高到低
        mode:
            'all'  → 所有满足条件的日期都输出，不合并
            'high' → 合并连续段，每只股票保留涨幅最大的段
            'end'  → 合并连续段，每只股票保留 end_date 最晚的段
        day_type:
            'natural' → 按自然日窗口
            'trading' → 按交易日窗口

    返回：
        DataFrame [rule, ts_code, start_date, end_date, growth]
    """
    if mode not in ("all", "high", "end"):
        raise ValueError(f"mode 必须为 all/high/end，收到 {mode}")
    if day_type not in ("natural", "trading"):
        raise ValueError(f"day_type 必须为 natural/trading，收到 {day_type}")

    # ---- 准备数据 ----
    d = df[["ts_code", "trade_date", "close_price"]].copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    d["close_price"] = pd.to_numeric(d["close_price"], errors="coerce")
    d = d.dropna(subset=["close_price"])
    d = d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    logger.info("growth scan start | rows=%d | codes=%d | mode=%s | "
                "day_type=%s | patterns=%s",
                len(d), d["ts_code"].nunique(), mode, day_type, patterns)

    # rule 名后缀：D 表示自然日，T 表示交易日
    suffix = "D" if day_type == "natural" else "T"

    results: list[dict] = []

    for days, threshold in patterns:
        rule = f"{days}{suffix}-{int(threshold * 100)}%"

        for ts_code, g in d.groupby("ts_code", sort=False):
            dates = g["trade_date"].to_numpy(dtype="datetime64[ns]")
            closes = g["close_price"].to_numpy(dtype=np.float64)

            hits = _scan_one_stock(
                dates, closes, int(days), float(threshold), day_type,
            )
            if not hits:
                continue

            # all 模式：不合并
            if mode == "all":
                for j, i, growth in hits:
                    results.append({
                        "rule": rule,
                        "ts_code": ts_code,
                        "start_date": pd.Timestamp(dates[j]),
                        "end_date": pd.Timestamp(dates[i]),
                        "growth": growth,
                    })
                continue

            # high / end：先合并
            segs = _merge_runs(hits)

            if mode == "high":
                j, i, growth = max(segs, key=lambda x: x[2])
            else:  # end
                j, i, growth = max(segs, key=lambda x: x[1])

            results.append({
                "rule": rule,
                "ts_code": ts_code,
                "start_date": pd.Timestamp(dates[j]),
                "end_date": pd.Timestamp(dates[i]),
                "growth": growth,
            })

    if not results:
        return pd.DataFrame(columns=[
            "rule", "ts_code", "start_date", "end_date", "growth",
        ])

    out = pd.DataFrame(results)
    # 按 rule 优先级 + 涨幅降序
    rule_order = {f"{d}{suffix}-{int(t * 100)}%": i
                  for i, (d, t) in enumerate(patterns)}
    out["_prio"] = out["rule"].map(rule_order)
    out = out.sort_values(["_prio", "growth"], ascending=[True, False])
    out = out.drop(columns="_prio").reset_index(drop=True)

    logger.info("growth scan done | hits=%d", len(out))
    return out