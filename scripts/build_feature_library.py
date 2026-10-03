"""构建特征库。

输入：
    cache/features/signals_xxx.pkl      ← 优值样本
    ts_stock_daily_his                  ← 原始日线

输出：
    cache/features/feature_library.pkl
    字段：ts_code, signal_date, label (1=优值, 0=对照), N 个特征
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
logger = logging.getLogger("build_features")

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache" / "features"


# ============================================================
# 参数
# ============================================================
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--signals", default="",
                   help="信号文件路径（默认取最新）")
    p.add_argument("--n-valid", type=int, default=200)
    p.add_argument("--n-control-per", type=int, default=3)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--table", default="ts_stock_daily_his")
    p.add_argument("--min-history", type=int, default=30,
                   help="最少历史交易日，低于此值的样本跳过")
    return p.parse_args()


# ============================================================
# 加载
# ============================================================
def find_latest_signals() -> Path:
    files = sorted(CACHE_DIR.glob("signals_*.pkl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("没有 signals_*.pkl 文件")
    return files[0]


def load_signals(path: Path) -> pd.DataFrame:
    with path.open("rb") as f:
        df = pickle.load(f)
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    return df


def load_daily(table: str) -> pd.DataFrame:
    db = MySQLHelper(db=settings.DB_NAME)
    logger.info("loading %s ...", table)
    t0 = time.time()
    df = db.qry2df(sql=f"""
        SELECT ts_code, trade_date, open_price, high, low, close_price,
               vol, amount, pct_chg
        FROM {table}
        ORDER BY ts_code, trade_date
    """)
    logger.info("loaded %d rows | %.1fs", len(df), time.time() - t0)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for col in ["open_price", "high", "low", "close_price",
                "vol", "amount", "pct_chg"]:
        df[col] = df[col].astype(float)
    return df


# ============================================================
# 采样
# ============================================================
def sample_valid(signals: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    if len(signals) <= n:
        return signals.copy()
    idx = rng.choice(len(signals), size=n, replace=False)
    return signals.iloc[idx].reset_index(drop=True)


def sample_controls(signals_all: pd.DataFrame,
                    valid_samples: pd.DataFrame,
                    daily: pd.DataFrame,
                    n_per: int,
                    seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    signal_set = set(
        zip(signals_all["ts_code"], signals_all["signal_date"])
    )

    daily_by_date = {}
    for date, g in daily.groupby("trade_date"):
        daily_by_date[date] = g["ts_code"].tolist()

    all_dates = sorted(daily_by_date.keys())

    controls = []
    for _, row in valid_samples.iterrows():
        sig_date = row["signal_date"]
        idx = np.searchsorted(all_dates, sig_date)
        if idx >= len(all_dates):
            idx = len(all_dates) - 1
        actual_date = all_dates[idx]

        candidates = daily_by_date.get(actual_date, [])
        if not candidates:
            continue

        candidates = [c for c in candidates if c != row["ts_code"]]
        candidates = [
            c for c in candidates
            if (c, actual_date) not in signal_set
        ]
        if not candidates:
            continue

        n_pick = min(n_per, len(candidates))
        picks = rng.choice(candidates, size=n_pick, replace=False)
        for c in picks:
            controls.append({
                "ts_code": c,
                "signal_date": actual_date,
            })

    return pd.DataFrame(controls)


# ============================================================
# 特征计算（核心修复）
# ============================================================
def compute_features(daily: pd.DataFrame,
                     ts_code: str,
                     signal_date: pd.Timestamp,
                     min_history: int = 30) -> dict | None:
    """计算单只股票在 signal_date 之前的特征。

    核心改动：
        - 最低历史要求降到 min_history（默认 30 天）
        - 每个窗口独立判断，历史不够则该窗口特征设为 NaN
        - 不再跳过整个样本
    """
    g = daily[daily["ts_code"] == ts_code].sort_values("trade_date")
    if len(g) < min_history:
        return None

    # 找 signal_date 的位置（不含当天）
    idx_mask = g["trade_date"] < signal_date
    if not idx_mask.any():
        return None
    idx = int(idx_mask.sum())   # hist 长度
    if idx < min_history:
        return None

    hist = g.iloc[:idx].copy()
    n = len(hist)

    close = hist["close_price"].to_numpy(dtype=np.float64)
    high = hist["high"].to_numpy(dtype=np.float64)
    low = hist["low"].to_numpy(dtype=np.float64)
    open_ = hist["open_price"].to_numpy(dtype=np.float64)
    vol = hist["vol"].to_numpy(dtype=np.float64)
    amount = hist["amount"].to_numpy(dtype=np.float64)
    pct = hist["pct_chg"].to_numpy(dtype=np.float64)

    c0 = close[-1]

    feats = {}

    # ---------- 价格趋势类 ----------
    for w in [5, 10, 20, 60]:
        if n >= w:
            c_w = close[-w]
            win_close = close[-w:]
            win_high = high[-w:]
            win_low = low[-w:]

            feats[f"ret_{w}"] = float(c0 / c_w - 1.0) if c_w > 0 else np.nan
            ma_w = float(win_close.mean())
            feats[f"ma_dev_{w}"] = float(c0 / ma_w - 1.0) if ma_w > 0 else np.nan
            hh = float(win_high.max())
            feats[f"high_dist_{w}"] = float(c0 / hh - 1.0) if hh > 0 else np.nan
            ll = float(win_low.min())
            feats[f"low_dist_{w}"] = float(c0 / ll - 1.0) if ll > 0 else np.nan
            amp_arr = (win_high - win_low) / np.where(win_close > 0, win_close, np.nan)
            feats[f"amp_{w}"] = float(np.nanmean(amp_arr))
            if win_close.mean() > 0:
                feats[f"std_ratio_{w}"] = float(win_close.std() / win_close.mean())
            else:
                feats[f"std_ratio_{w}"] = np.nan
        else:
            feats[f"ret_{w}"] = np.nan
            feats[f"ma_dev_{w}"] = np.nan
            feats[f"high_dist_{w}"] = np.nan
            feats[f"low_dist_{w}"] = np.nan
            feats[f"amp_{w}"] = np.nan
            feats[f"std_ratio_{w}"] = np.nan

    # ---------- 量能类 ----------
    for w in [5, 10, 20, 60]:
        base_n = min(60, n)
        v_base = vol[-base_n:]
        v_base_mean = float(v_base.mean()) if base_n > 0 else 0.0

        if n >= w and v_base_mean > 0:
            v_w = vol[-w:]
            feats[f"vol_ratio_v_{w}"] = float(v_w.mean() / v_base_mean)
            feats[f"big_vol_days_{w}"] = int((v_w > 1.5 * v_base_mean).sum())
            feats[f"small_vol_days_{w}"] = int((v_w < 0.7 * v_base_mean).sum())
        else:
            feats[f"vol_ratio_v_{w}"] = np.nan
            feats[f"big_vol_days_{w}"] = np.nan
            feats[f"small_vol_days_{w}"] = np.nan

    # ---------- 量价相关性（20 天） ----------
    if n >= 20:
        win_close = close[-20:]
        win_vol = vol[-20:]
        ret_arr = np.diff(win_close) / win_close[:-1]
        vol_chg = np.diff(win_vol) / win_vol[:-1]
        if (len(ret_arr) >= 5
                and np.std(ret_arr) > 0
                and np.std(vol_chg) > 0):
            feats["price_vol_corr_20"] = float(
                np.corrcoef(ret_arr, vol_chg)[0, 1]
            )
        else:
            feats["price_vol_corr_20"] = np.nan
    else:
        feats["price_vol_corr_20"] = np.nan

    # ---------- 形态类（20 天） ----------
    if n >= 20:
        win_open = open_[-20:]
        win_close_20 = close[-20:]
        win_high_20 = high[-20:]
        win_low_20 = low[-20:]

        up = int((win_close_20 > win_open).sum())
        feats["up_days_20"] = up
        feats["down_days_20"] = int(20 - up)

        upper = win_high_20 - np.maximum(win_open, win_close_20)
        lower = np.minimum(win_open, win_close_20) - win_low_20
        total = win_high_20 - win_low_20
        total_safe = np.where(total > 0, total, np.nan)
        feats["upper_shadow_20"] = float(np.nanmean(upper / total_safe))
        feats["lower_shadow_20"] = float(np.nanmean(lower / total_safe))

        pct_arr = pct[-20:]
        feats["limit_up_20"] = int((pct_arr >= 9.8).sum())
        feats["limit_down_20"] = int((pct_arr <= -9.8).sum())
    else:
        feats["up_days_20"] = np.nan
        feats["down_days_20"] = np.nan
        feats["upper_shadow_20"] = np.nan
        feats["lower_shadow_20"] = np.nan
        feats["limit_up_20"] = np.nan
        feats["limit_down_20"] = np.nan

    # ---------- 跨窗口对比 ----------
    if not np.isnan(feats.get("ret_5", np.nan)) and \
       not np.isnan(feats.get("ret_20", np.nan)):
        feats["ret_5_minus_20"] = feats["ret_5"] - feats["ret_20"]
    else:
        feats["ret_5_minus_20"] = np.nan

    if not np.isnan(feats.get("ret_10", np.nan)) and \
       not np.isnan(feats.get("ret_60", np.nan)):
        feats["ret_10_minus_60"] = feats["ret_10"] - feats["ret_60"]
    else:
        feats["ret_10_minus_60"] = np.nan

    # ---------- 最后一天成交额（亿元） ----------
    feats["amount_last"] = float(amount[-1] / 1e5) if n > 0 else np.nan
    feats["close_last"] = float(close[-1]) if n > 0 else np.nan

    return feats


# ============================================================
# 主流程
# ============================================================
def main():
    args = parse_args()

    logger.info("=" * 60)
    logger.info("build_feature_library | n_valid=%d | n_ctrl_per=%d | "
                "seed=%d | min_history=%d",
                args.n_valid, args.n_control_per, args.seed,
                args.min_history)
    logger.info("=" * 60)

    t0 = time.time()

    # ---- 1. 信号 ----
    sig_path = Path(args.signals) if args.signals else find_latest_signals()
    logger.info("signals file: %s", sig_path.name)
    signals_all = load_signals(sig_path)
    logger.info("total signals: %d", len(signals_all))

    # ---- 2. 优值采样 ----
    valid = sample_valid(signals_all, args.n_valid, args.seed)
    logger.info("valid samples: %d", len(valid))

    # ---- 3. 日线 ----
    daily = load_daily(args.table)

    # ---- 4. 对照采样 ----
    controls = sample_controls(
        signals_all, valid, daily, args.n_control_per, args.seed,
    )
    logger.info("control samples: %d", len(controls))

    # ---- 5. 合并 ----
    valid_tagged = valid[["ts_code", "signal_date"]].copy()
    valid_tagged["label"] = 1
    controls_tagged = controls.copy()
    controls_tagged["label"] = 0

    all_samples = pd.concat(
        [valid_tagged, controls_tagged], ignore_index=True,
    )
    logger.info("total samples: %d (valid=%d, control=%d)",
                len(all_samples), len(valid_tagged), len(controls_tagged))

    # ---- 6. 逐样本 ----
    logger.info("computing features ...")
    rows = []
    n_skip = 0
    for i, row in all_samples.iterrows():
        if i > 0 and i % 100 == 0:
            logger.info("  progress %d / %d (skip=%d)",
                        i, len(all_samples), n_skip)

        feats = compute_features(
            daily, row["ts_code"], row["signal_date"],
            min_history=args.min_history,
        )
        if feats is None:
            n_skip += 1
            continue

        feats["ts_code"] = row["ts_code"]
        feats["signal_date"] = row["signal_date"]
        feats["label"] = int(row["label"])
        rows.append(feats)

    if not rows:
        logger.error("no features computed | all skipped")
        return

    features_df = pd.DataFrame(rows)
    front = ["ts_code", "signal_date", "label"]
    other = [c for c in features_df.columns if c not in front]
    features_df = features_df[front + other]

    # ---- 7. 保存 ----
    out_path = CACHE_DIR / "feature_library.pkl"
    with out_path.open("wb") as f:
        pickle.dump(features_df, f)

    logger.info("saved | %s | shape=%s | %.1fs",
                out_path, features_df.shape, time.time() - t0)

    # ---- 8. 统计 ----
    print(f"\n{'=' * 60}")
    print(f"特征库统计")
    print(f"{'=' * 60}")
    print(f"总样本数：{len(features_df)}")
    print(f"优值组：{(features_df['label'] == 1).sum()}")
    print(f"对照组：{(features_df['label'] == 0).sum()}")
    print(f"特征数：{len([c for c in features_df.columns if c not in front])}")
    print(f"跳过样本（历史不足）：{n_skip}")

    print(f"\n=== 特征列 ===")
    feats_cols = [c for c in features_df.columns if c not in front]
    for i, c in enumerate(feats_cols, 1):
        print(f"  {i:3d}. {c}")

    print(f"\n=== 缺失值比例 TOP 10 ===")
    miss = features_df[feats_cols].isna().mean().sort_values(ascending=False)
    print(miss.head(10).to_string())

    print(f"\n=== 前 3 行（部分列）===")
    show_cols = front + feats_cols[:8]
    print(features_df[show_cols].head(3).to_string())


if __name__ == "__main__":
    main()