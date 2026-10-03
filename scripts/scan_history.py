"""扫描历史数据，找出"优值样本"。

默认规则（D 组合）：
    未来 30 交易日，涨幅 >= 30%
    起涨前 60 交易日，涨幅 < 20%
    起涨前 20 交易日，振幅 < 30%     # (最高-最低)/最低
去重：
    30 天窗口内只保留首次满足的那一天

用法：
    :: 用默认参数
    pipenv run python scripts\\scan_history.py

    :: 自定义阈值（只看未来涨幅 20% 以上）
    pipenv run python scripts\\scan_history.py --threshold 0.20

    :: 放宽形态条件
    pipenv run python scripts\\scan_history.py --pre-gain-max 0.30 --amp-max 0.40

    :: 扫描 2025 年历史
    pipenv run python scripts\\scan_history.py --table ts_stock_daily_his_2025
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import pickle
import time

import numpy as np
import pandas as pd

from utils.dbManager import MySQLHelper
from dashboard.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("scan_history")

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "features"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 参数解析
# ============================================================
def parse_args():
    p = argparse.ArgumentParser(description="扫描历史找优值样本")
    p.add_argument("--table", default="ts_stock_daily_his",
                   help="数据表名")
    p.add_argument("--threshold", type=float, default=0.30,
                   help="未来 window 天涨幅阈值，默认 0.30 (30%%)")
    p.add_argument("--window", type=int, default=30,
                   help="未来窗口（交易日），默认 30")
    p.add_argument("--pre-window", type=int, default=60,
                   help="起涨前窗口（交易日），默认 60")
    p.add_argument("--pre-gain-max", type=float, default=0.20,
                   help="起涨前 pre_window 天涨幅上限，默认 0.20")
    p.add_argument("--amp-window", type=int, default=20,
                   help="振幅计算窗口，默认 20")
    p.add_argument("--amp-max", type=float, default=0.30,
                   help="起涨前 amp_window 天振幅上限，默认 0.30")
    return p.parse_args()


# ============================================================
# 加载
# ============================================================
def load_data(table: str) -> pd.DataFrame:
    db = MySQLHelper(db=settings.DB_NAME)
    logger.info("loading %s ...", table)
    t0 = time.time()

    df = db.qry2df(sql=f"""
        SELECT ts_code, trade_date, close_price, high, low, vol, amount
        FROM {table}
        ORDER BY ts_code, trade_date
    """)
    logger.info("loaded %d rows | %.1fs", len(df), time.time() - t0)

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for col in ["close_price", "high", "low", "vol", "amount"]:
        df[col] = df[col].astype(float)
    return df


# ============================================================
# 找信号
# ============================================================
def find_signals(df: pd.DataFrame,
                 threshold: float = 0.30,
                 window: int = 30,
                 pre_window: int = 60,
                 pre_gain_max: float = 0.20,
                 amp_window: int = 20,
                 amp_max: float = 0.30) -> pd.DataFrame:
    """按多个条件扫描历史数据。

    条件：
        1. 未来 window 天涨幅 >= threshold
        2. 起涨前 pre_window 天涨幅 < pre_gain_max
        3. 起涨前 amp_window 天振幅 < amp_max

    返回：
        DataFrame: ts_code, signal_date, signal_close, future_max,
                   gain, pre_gain, amp
    """
    from numpy.lib.stride_tricks import sliding_window_view

    results = []
    codes = df["ts_code"].unique()
    total = len(codes)

    min_len = pre_window + window + 1

    for idx, ts_code in enumerate(codes, 1):
        if idx % 500 == 0:
            logger.info("processing %d / %d ...", idx, total)

        g = df[df["ts_code"] == ts_code].sort_values("trade_date")
        close = g["close_price"].to_numpy(dtype=np.float64)
        high = g["high"].to_numpy(dtype=np.float64)
        low = g["low"].to_numpy(dtype=np.float64)
        dates = g["trade_date"].to_numpy()
        n = len(close)

        if n < min_len:
            continue

        # ---------- 未来 window 天最高价（不含当天） ----------
        # window_slides[i] = close[i : i+window+1]
        # future_max[i] = max(close[i+1 : i+window+1])
        window_slides = sliding_window_view(close, window + 1)
        future_max = window_slides[:, 1:].max(axis=1)
        # future_max 长度 = n - window，对应信号日 i 从 0 到 n-window-1

        # ---------- 起涨前 pre_window 天涨幅 ----------
        # pre_gain[t] = close[t] / close[t - pre_window] - 1
        pre_gain = np.full(n, np.nan)
        pre_gain[pre_window:] = close[pre_window:] / close[:-pre_window] - 1.0

        # ---------- 起涨前 amp_window 天振幅 ----------
        # 用 pandas rolling 计算 [t-amp_window, t] 的 (最高-最低)/最低
        high_roll_max = pd.Series(high).rolling(
            amp_window + 1, min_periods=amp_window + 1,
        ).max().to_numpy()
        low_roll_min = pd.Series(low).rolling(
            amp_window + 1, min_periods=amp_window + 1,
        ).min().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            amp = (high_roll_max - low_roll_min) / low_roll_min

        # ---------- 扫描 ----------
        max_i = len(future_max)   # 未来 30 天可算的最大索引 + 1

        i = pre_window    # 至少需要 pre_window 的历史
        while i < max_i:
            # 前置1：起涨前涨幅
            pg = pre_gain[i]
            if np.isnan(pg) or pg >= pre_gain_max:
                i += 1
                continue

            # 前置2：起涨前振幅
            am = amp[i]
            if np.isnan(am) or am >= amp_max:
                i += 1
                continue

            # 未来涨幅
            gain_i = future_max[i] / close[i] - 1.0
            if gain_i < threshold:
                i += 1
                continue

            # 通过全部条件
            results.append({
                "ts_code": ts_code,
                "signal_date": dates[i],
                "signal_close": close[i],
                "future_max": future_max[i],
                "gain": float(gain_i),
                "pre_gain": float(pg),
                "amp": float(am),
            })

            # 去重：跳过后续 window 天
            i += window + 1

    out = pd.DataFrame(results)
    if not out.empty:
        out["signal_date"] = pd.to_datetime(out["signal_date"])
        out = out.sort_values(["ts_code", "signal_date"]).reset_index(drop=True)
        logger.info("found %d signals | stocks=%d",
                    len(out), out["ts_code"].nunique())
    else:
        logger.warning("no signals found")
    return out


# ============================================================
# 主流程
# ============================================================
def main():
    args = parse_args()

    logger.info("=" * 60)
    logger.info("scan_history | table=%s", args.table)
    logger.info("  未来涨幅     >= %.0f%%  窗口 %d 天",
                args.threshold * 100, args.window)
    logger.info("  起涨前涨幅    < %.0f%%  窗口 %d 天",
                args.pre_gain_max * 100, args.pre_window)
    logger.info("  起涨前振幅    < %.0f%%  窗口 %d 天",
                args.amp_max * 100, args.amp_window)
    logger.info("=" * 60)

    t0 = time.time()
    df = load_data(args.table)

    logger.info("start scanning | stocks=%d | rows=%d",
                df["ts_code"].nunique(), len(df))

    signals = find_signals(
        df,
        threshold=args.threshold,
        window=args.window,
        pre_window=args.pre_window,
        pre_gain_max=args.pre_gain_max,
        amp_window=args.amp_window,
        amp_max=args.amp_max,
    )

    if signals.empty:
        logger.warning("no signals | exit")
        return

    # 保存
    tag = (f"{args.table}_thr{int(args.threshold * 100)}"
           f"_pre{int(args.pre_gain_max * 100)}"
           f"_amp{int(args.amp_max * 100)}")
    out_path = CACHE_DIR / f"signals_{tag}.pkl"
    with out_path.open("wb") as f:
        pickle.dump(signals, f)

    logger.info("saved to %s | rows=%d | %.1fs",
                out_path, len(signals), time.time() - t0)

    # ---------- 统计 ----------
    print(f"\n{'=' * 60}")
    print(f"信号统计 | {args.table}")
    print(f"{'=' * 60}")
    print(f"总信号数：{len(signals)}")
    print(f"涉及股票：{signals['ts_code'].nunique()} / "
          f"{df['ts_code'].nunique()}")
    print(f"信号日期范围：{signals['signal_date'].min().date()} ~ "
          f"{signals['signal_date'].max().date()}")

    print(f"\n--- 未来涨幅（gain）---")
    print(f"  平均：{signals['gain'].mean():.2%}")
    print(f"  中位数：{signals['gain'].median():.2%}")
    print(f"  25% 分位：{signals['gain'].quantile(0.25):.2%}")
    print(f"  75% 分位：{signals['gain'].quantile(0.75):.2%}")
    print(f"  90% 分位：{signals['gain'].quantile(0.90):.2%}")

    print(f"\n--- 起涨前涨幅（pre_gain）---")
    print(f"  平均：{signals['pre_gain'].mean():.2%}")
    print(f"  中位数：{signals['pre_gain'].median():.2%}")
    print(f"  25% 分位：{signals['pre_gain'].quantile(0.25):.2%}")
    print(f"  75% 分位：{signals['pre_gain'].quantile(0.75):.2%}")

    print(f"\n--- 起涨前振幅（amp）---")
    print(f"  平均：{signals['amp'].mean():.2%}")
    print(f"  中位数：{signals['amp'].median():.2%}")
    print(f"  25% 分位：{signals['amp'].quantile(0.25):.2%}")
    print(f"  75% 分位：{signals['amp'].quantile(0.75):.2%}")

    print(f"\n=== 前 10 条信号 ===")
    print(signals.head(10).to_string())

    print(f"\n=== 每月信号数 ===")
    monthly = signals.groupby(
        signals["signal_date"].dt.to_period("M")
    ).size()
    print(monthly.to_string())

    print(f"\n=== 信号最多的 10 只股票 ===")
    print(signals["ts_code"].value_counts().head(10).to_string())


if __name__ == "__main__":
    main()