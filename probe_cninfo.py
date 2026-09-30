# -*- coding: utf-8 -*-
"""
股票区间涨幅突破筛选 —— 两种实现思路对比

功能：找出满足涨幅突破规则的股票区间，输出 ts_code / end_date / start_date / return_pct。

两种实现（同文件，供实际数据核实比较效率）：
  1) find_breakout_rolling —— rolling 时间窗（窗口 = 自然日历天数）
  2) find_breakout_merge   —— 自关联 merge（窗口 = 自然日历天数）
  两者业务逻辑完全一致（输出逐条相等），仅实现路径不同，用于公平比较效率。

公共规则：
  rules: list[(window, threshold), ...]，列表靠前 = 优先级越高
  默认 rules = [(14, 0.4), (5, 0.2)]  # 14(天)→40%，5(天)→20%
  优先级规则：rule_idx 越小越优先；同规则内涨幅越大越优先
  重叠区间：只要日期相交即判定重叠，只保留优先选中（优先级高 + 涨幅大）的一条
  输出字段：ts_code, end_date, start_date, return_pct
  脏数据：close_price 为空 或 =0 的记录直接剔除，不参与计算

============================================================
口径统一（两者完全一致，均为自然日）：
  窗口"14/5" = 自然日历天数，即 end_date - start_date == 14 / 5 自然天
  不跳过周末/节假日/停牌；若恰好 n 自然天前该股无K线，则该对不命中。
============================================================
"""

import pandas as pd
import numpy as np
from typing import List, Tuple
from utils.dbManager import MySQLHelper

# ============================================================
# 公共：重叠区间过滤内部函数
# ============================================================
def _filter_overlap_intervals(cand_df: pd.DataFrame) -> pd.DataFrame:
    """
    内部重叠区间过滤。
    排序：rule_idx 升序(优先级高在前)，同 rule_idx 按 return_pct 降序。
    区间只要日期相交即判定重叠，只保留先选中的那一条，其余丢弃。
    """
    if cand_df is None or cand_df.empty:
        return cand_df

    cand_df = cand_df.sort_values(
        by=["rule_idx", "return_pct"], ascending=[True, False]
    ).reset_index(drop=True)

    final_rows = []
    used_ranges = []
    for _, row in cand_df.iterrows():
        s_dt, e_dt = row["start_date"], row["end_date"]
        overlap = False
        for (saved_s, saved_e) in used_ranges:
            # 重叠判断： NOT (end < saved_start OR start > saved_end)
            if not (e_dt < saved_s or s_dt > saved_e):
                overlap = True
                break
        if not overlap:
            final_rows.append(row)
            used_ranges.append((s_dt, e_dt))

    return pd.DataFrame(final_rows)


# ============================================================
# 方式一：rolling 滚动窗口实现（窗口 = 自然日历天数）
# ============================================================
def find_breakout_rolling(
    df: pd.DataFrame,
    rules: List[Tuple[int, float]] = [(14, 0.4), (5, 0.2)],
) -> pd.DataFrame:
    """
    rolling 窗口版本 —— 已改为与 merge 版完全一致的自然日口径。

    窗口"14/5" 表示：end_date 的闭区间窗口 [end_date - n自然天, end_date]，
    取窗口内最早观测（即恰好 n 自然天前那天，当该日有K线时）作为起点，
    语义与 find_breakout_merge 的"end_date - start_date == n自然天"精确配对完全相同。

    实现：pandas 时间窗 rolling('Nd', closed='both')，
          用窗口最早 daynum == (end_daynum - n) 判定该日有K线，取该日收盘价为起点。
    """
    _validate(df, rules)

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = _clean(df)
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    candidates = []

    for _, group in df.groupby("ts_code"):
        g = group.sort_values("trade_date").reset_index(drop=True)
        ts = g["ts_code"].iloc[0]
        dates = g["trade_date"]
        close = g["close_price"].values

        # 每股内从 0 开始计的自然日序号（转 numpy，避免 Series 索引对齐）
        daynum = (dates - dates.min()).dt.days.astype("int64").to_numpy()
        idx = pd.DatetimeIndex(dates)
        s_close = pd.Series(close, index=idx)
        s_day = pd.Series(daynum, index=idx)

        for rid, (win, thresh) in enumerate(rules):
            # 窗口内最早 daynum（C 级 rolling.min），即窗口内最早观测日期
            earliest = s_day.rolling(
                f"{win}D", min_periods=1, closed="both"
            ).min().values
            # 仅当窗口最早观测恰好在 end - win 天（该日有K线）才配对
            valid = earliest == (daynum - win)
            # 起点收盘价：恰好 end - win 天那天的 close（按 daynum 查找）
            lookup = pd.Series(close, index=daynum)
            start_close = lookup.reindex(daynum - win).to_numpy()

            with np.errstate(divide="ignore", invalid="ignore"):
                # print(close,start_close)
                ret = (close - start_close) / start_close
            mask = valid & (ret >= thresh)

            for ei in np.flatnonzero(mask):
                candidates.append({
                    "ts_code": ts,
                    "end_date": dates[ei],
                    "start_date": dates[ei] - pd.Timedelta(days=win),
                    "return_pct": ret[ei],
                    "rule_idx": rid,
                })

    return _finalize(candidates)


# ============================================================
# 方式二：自关联 merge 实现（窗口 = 自然日历天数）
# ============================================================
def find_breakout_merge(
    df: pd.DataFrame,
    rules: List[Tuple[int, float]] = [(14, 0.4), (5, 0.2)],
) -> pd.DataFrame:
    """
    自关联 merge 版本。
    窗口"14/5" 表示：end_date - start_date == 14 / 5 自然天。

    实现：
      B 表(起点)构造 join_end_date = start_date + n 自然天；
      用 ts_code + end_date 等值关联 B 表 ts_code + join_end_date；
      主键 ts_code+trade_date 唯一 ⇒ 1:1 匹配，不产生笛卡尔积。
    """
    _validate(df, rules)

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = _clean(df)

    # end 表：作为区间结束日
    df_end = df[["ts_code", "trade_date", "close_price"]].rename(
        columns={"trade_date": "end_date", "close_price": "end_close"}
    )

    candidates = []
    for rule_idx, (n_days, thresh) in enumerate(rules):
        df_start = df[["ts_code", "trade_date", "close_price"]].copy()
        df_start = df_start.rename(
            columns={"trade_date": "start_date", "close_price": "start_close"}
        )
        df_start["join_end_date"] = df_start["start_date"] + pd.Timedelta(days=n_days)

        joined = pd.merge(
            df_end,
            df_start,
            left_on=["ts_code", "end_date"],
            right_on=["ts_code", "join_end_date"],
            how="inner",
        )
        if joined.empty:
            continue

        joined["return_pct"] = (
            (joined["end_close"] - joined["start_close"]) / joined["start_close"]
        )
        joined = joined[joined["return_pct"] >= thresh].copy()
        if joined.empty:
            continue

        joined["rule_idx"] = rule_idx
        candidates.append(
            joined[["ts_code", "end_date", "start_date", "return_pct", "rule_idx"]]
        )

    if not candidates:
        return _empty_result()
    cand_all = pd.concat(candidates, ignore_index=True)
    filtered = _filter_overlap_intervals(cand_all)
    return filtered[["ts_code", "end_date", "start_date", "return_pct"]].reset_index(drop=True)


# ============================================================
# 公共辅助函数
# ============================================================
def _validate(df: pd.DataFrame, rules):
    required = {"ts_code", "trade_date", "close_price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"输入 df 缺失必要字段：{missing}")
    if not isinstance(rules, list) or len(rules) == 0:
        raise ValueError("rules 不能为空，格式 [(window, threshold), ...]")
    for w, t in rules:
        if not isinstance(w, int) or w < 1:
            raise ValueError(f"窗口天数必须 >=1 整数，当前：{w}")
        if not isinstance(t, float) or t <= 0:
            raise ValueError(f"涨幅阈值必须 >0 浮点数，当前：{t}")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """剔除脏数据：close_price 为空 或 =0"""
    df = df.dropna(subset=["close_price"])
    df = df[df["close_price"] != 0].reset_index(drop=True)
    return df


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=["ts_code", "end_date", "start_date", "return_pct"])


def _finalize(candidates: list) -> pd.DataFrame:
    if not candidates:
        return _empty_result()
    cand_all = pd.DataFrame(candidates)
    filtered = _filter_overlap_intervals(cand_all)
    return filtered[["ts_code", "end_date", "start_date", "return_pct"]].reset_index(drop=True)


# ============================================================
# 基准测试：两种方式效率与结果对比
# ============================================================
def _make_synthetic_df(n_stocks: int = 500, n_days: int = 250, seed: int = 42) -> pd.DataFrame:
    """
    生成合成K线数据：n_stocks 只股票，每只 n_days 个自然日。
    trade_date 用自然日（工作日+周末都在），保证 merge 的"自然日差"能命中。
    close_price 用随机游走生成，部分股票会满足涨幅条件。
    """
    db = MySQLHelper(db="jhd_rd")
    sql= (
        f"select h.ts_code,h.trade_date,h.close_price "
        f"from ts_stock_daily_his AS h "
    )
    df = db.qry2df(sql=sql)
    df['trade_date'] = pd.to_datetime(df["trade_date"]).dt.date
    df['close_price'] = pd.to_numeric(df['close_price'], errors="coerce")
    return df
    # rng = np.random.default_rng(seed)
    # rows = []
    # codes = [f"{i:06d}.SZ" for i in range(n_stocks)]
    # for code in codes:
    #     dates = pd.date_range("2023-01-01", periods=n_days, freq="D")
    #     close = 10.0 * np.exp(np.cumsum(rng.normal(0, 0.02, n_days)))
    #     close = np.round(close, 2)
    #     for d, c in zip(dates, close):
    #         rows.append({
    #             "ts_code": code,
    #             "trade_date": d,
    #             "close_price": float(c),
    #             # 其余字段保留原始列名占位，本函数只用上面三列
    #             "open_price": float(c),
    #             "high": float(c),
    #             "low": float(c),
    #             "pre_close": float(c),
    #             "change_amount": 0.0,
    #             "pct_chg": 0.0,
    #             "vol": 0,
    #             "amount": 0.0,
    #             "ah_vol": 0,
    #             "ah_amount": 0.0,
    #         })
    # return pd.DataFrame(rows)


def benchmark(n_stocks: int = 500, n_days: int = 250, seed: int = 42):
    import time

    df = _make_synthetic_df(n_stocks, n_days, seed)
    total_rows = len(df)
    print("=" * 70)
    print(f"基准测试输入：{n_stocks} 只股票 × {n_days} 个自然日 = {total_rows:,} 行")
    print(f"默认规则：rules = [(14, 0.4), (5, 0.2)]")
    print(f"  方式一 rolling：时间窗(自然日)；方式二 merge：自关联(自然日) —— 两者逻辑一致")
    print("=" * 70)

    # 方式一：rolling
    t0 = time.perf_counter()
    res_rolling = find_breakout_rolling(df)
    t_rolling = time.perf_counter() - t0

    # 方式二：merge
    t0 = time.perf_counter()
    res_merge = find_breakout_merge(df)
    t_merge = time.perf_counter() - t0

    print(f"\n[rolling] 耗时 {t_rolling:.3f}s，命中 {len(res_rolling):,} 条区间")
    print(f"[merge  ] 耗时 {t_merge:.3f}s，命中 {len(res_merge):,} 条区间")
    if t_rolling > 0:
        print(f"[效率] merge/rolling 耗时比 ≈ {t_merge / t_rolling:.2f}x")

    print("\n[rolling] 前 5 条示例：")
    if not res_rolling.empty:
        print(res_rolling.head(5).to_string(index=False))
    print("\n[merge  ] 前 5 条示例：")
    if not res_merge.empty:
        print(res_merge.head(5).to_string(index=False))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="两种突破筛选实现效率对比")
    parser.add_argument("--stocks", type=int, default=500, help="股票数量")
    parser.add_argument("--days", type=int, default=250, help="每只股票自然日数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    benchmark(args.stocks, args.days, args.seed)
