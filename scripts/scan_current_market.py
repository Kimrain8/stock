"""用训练好的资金意图模型扫描当前市场。
    1. 从 cache/data/ 加载所有缓存股票
    2. 对每只股票计算 10 个核心特征（基于最新数据）
    3. 用 money_model.pkl 算 money_score
    4. 结合 basic_score_library.pkl 的 basic_score
    5. 按综合标签排序，输出 TOP N
用法：
    python scripts\\scan_current_market.py
    python scripts\\scan_current_market.py --top 30
    python scripts\\scan_current_market.py --min-basic A     # 只保留基本面对 A 及以上的

⚠️ 单位说明：
    缓存 quotes.amount 单位是【元】（akshare 新浪接口），
    与数据库 ts_stock_daily_his 的 amount（千元）不同！
    缓存 → 亿元：÷ 1e8
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("scan_current_market")

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DATA_DIR = BASE_DIR / "cache" / "data"
CACHE_FEAT_DIR = BASE_DIR / "cache" / "features"


CORE_FEATURES = [
    "std_ratio_20", "std_ratio_10", "amount_last", "limit_up_20",
    "big_vol_days_20", "big_vol_days_10", "vol_ratio_v_10",
    "vol_ratio_v_20", "small_vol_days_10", "small_vol_days_5",
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-basic", default="",
                   choices=["", "S", "A", "B", "C", "D"],help="只保留基本面对应档位及以上的股票")
    return p.parse_args()


def compute_features_now(data: dict) -> dict | None:
    quotes = data.get("quotes")
    if quotes is None or quotes.empty:
        return None

    df = quotes.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    n = len(df)
    if n < 60:
        return None

    close = df["close"].to_numpy(dtype=np.float64)
    high = df["high"].to_numpy(dtype=np.float64)
    low = df["low"].to_numpy(dtype=np.float64)
    vol = df["volume"].to_numpy(dtype=np.float64)
    amount = df["amount"].to_numpy(dtype=np.float64) if "amount" in df.columns else None
    pct = df["close"].pct_change().fillna(0).to_numpy(dtype=np.float64) * 100

    feats = {}

    for w in [20, 10]:
        win = close[-w:]
        if win.mean() > 0:
            feats[f"std_ratio_{w}"] = float(win.std() / win.mean())
        else:
            feats[f"std_ratio_{w}"] = np.nan

    # ★ amount 单位是元，÷ 1e8 得到亿元
    if amount is not None and len(amount) > 0:
        feats["amount_last"] = float(amount[-1] / 1e8)
    else:
        feats["amount_last"] = np.nan

    pct_20 = pct[-20:]
    feats["limit_up_20"] = int((pct_20 >= 9.8).sum())

    v60 = vol[-60:]
    v60_mean = float(v60.mean()) if len(v60) > 0 else 0.0

    for w in [20, 10, 5]:
        if v60_mean > 0 and n >= w:
            v_w = vol[-w:]
            feats[f"big_vol_days_{w}"] = int((v_w > 1.5 * v60_mean).sum())
            feats[f"small_vol_days_{w}"] = int((v_w < 0.7 * v60_mean).sum())
        else:
            feats[f"big_vol_days_{w}"] = np.nan
            feats[f"small_vol_days_{w}"] = np.nan

    for w in [10, 20]:
        if v60_mean > 0 and n >= w:
            feats[f"vol_ratio_v_{w}"] = float(vol[-w:].mean() / v60_mean)
        else:
            feats[f"vol_ratio_v_{w}"] = np.nan

    return feats


def predict_money_score(model_pkg: dict, feats: dict) -> float:
    X = pd.DataFrame([feats])
    feats_order = model_pkg["features"]
    for c in feats_order:
        if c not in X.columns:
            X[c] = np.nan
    X = X[feats_order].fillna(0)

    m1 = model_pkg["model_2025to2026"]
    m2 = model_pkg["model_2026to2025"]
    p1 = float(m1.predict(X)[0])
    p2 = float(m2.predict(X)[0])
    return (p1 + p2) / 2.0


def money_score_to_grade(score: float) -> str:
    """资金意图分档（2026-10-02 重定标）。

    依据：诊断显示优值组 mean=0.36, max=0.65；对照组 mean=0.20, max=0.54。
    """
    if score >= 0.55:
        return "S"
    if score >= 0.40:
        return "A"
    if score >= 0.28:
        return "B"
    if score >= 0.15:
        return "C"
    return "D"


def combine_label(basic_grade: str, money_grade: str) -> str:
    if basic_grade == "D":
        return "❌ 排除"
    b_ok = basic_grade in ("S", "A")
    m_ok = money_grade in ("S", "A")
    if b_ok and m_ok:
        return "⭐ 强烈推荐"
    if b_ok and money_grade == "B":
        return "✅ 值得关注"
    if b_ok and money_grade in ("C", "D"):
        return "⏸ 基本面好，等时机"
    if money_grade in ("S", "A") and basic_grade in ("B", "C"):
        return "⚠️ 资金介入，需谨慎"
    return "🔍 观察"


def main():
    args = parse_args()

    logger.info("=" * 60)
    logger.info("scan_current_market | top=%d | min_basic=%s",
                args.top, args.min_basic or "(不限)")
    logger.info("=" * 60)

    t0 = time.time()

    model_path = CACHE_FEAT_DIR / "money_model.pkl"
    if not model_path.exists():
        logger.error("模型不存在：%s", model_path)
        return
    with model_path.open("rb") as f:
        model_pkg = pickle.load(f)
    logger.info("model loaded | avg_auc=%.4f", model_pkg.get("avg_auc", 0))

    basic_path = CACHE_FEAT_DIR / "basic_score_library.pkl"
    basic_df = None
    if basic_path.exists():
        with basic_path.open("rb") as f:
            basic_df = pickle.load(f)
        logger.info("basic library loaded | rows=%d", len(basic_df))
    else:
        logger.warning("基本面库不存在")

    files = sorted(CACHE_DATA_DIR.glob("*.pkl"))
    logger.info("scanning %d cached stocks ...", len(files))

    rows = []
    n_skip = 0
    for i, path in enumerate(files, 1):
        if i % 100 == 0:
            logger.info("  progress %d / %d (skip=%d)",
                        i, len(files), n_skip)
        code = path.stem
        try:
            with path.open("rb") as f:
                data = pickle.load(f)
        except Exception as e:
            logger.warning("load failed | %s | %s", code, e)
            n_skip += 1
            continue

        feats = compute_features_now(data)
        if feats is None:
            n_skip += 1
            continue

        try:
            money_score = predict_money_score(model_pkg, feats)
        except Exception as e:
            logger.warning("predict failed | %s | %s", code, e)
            n_skip += 1
            continue

        basic = data.get("basic", {}) or {}
        name = basic.get("A股简称", "") or basic.get("公司名称", "")

        rows.append({
            "ts_code": code, "name": name,
            "money_score": money_score,
            "money_grade": money_score_to_grade(money_score),
            **feats,
        })

    if not rows:
        logger.error("no results")
        return

    df = pd.DataFrame(rows)
    logger.info("scan done | %d stocks | skip=%d | %.1fs",
                len(df), n_skip, time.time() - t0)

    if basic_df is not None and not basic_df.empty:
        df = df.merge(
            basic_df[["ts_code", "basic_score", "basic_grade", "hard_pass"]],
            on="ts_code", how="left",
        )
        df["basic_score"] = df["basic_score"].fillna(-1)
        df["basic_grade"] = df["basic_grade"].fillna("?")
        df["hard_pass"] = df["hard_pass"].fillna(False)
    else:
        df["basic_score"] = -1
        df["basic_grade"] = "?"
        df["hard_pass"] = False

    df["label"] = df.apply(
        lambda r: combine_label(r["basic_grade"], r["money_grade"]),
        axis=1,
    )

    grade_order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "?": 5}
    df["_bg"] = df["basic_grade"].map(grade_order)
    df["_mg"] = df["money_grade"].map(grade_order)
    df = df.sort_values(
        ["_bg", "_mg", "basic_score", "money_score"],
        ascending=[True, True, False, False],
    ).reset_index(drop=True)

    if args.min_basic:
        cutoff = grade_order[args.min_basic]
        df = df[df["_bg"] <= cutoff].reset_index(drop=True)

    print(f"\n{'=' * 110}")
    print(f"扫描结果 | 共 {len(df)} 只股票 | TOP {args.top}")
    print(f"{'=' * 110}")

    show = df.head(args.top).copy()
    show["资金意图"] = show.apply(
        lambda r: f"{r['money_grade']} ({r['money_score']:.3f})", axis=1
    )
    show["基本面"] = show.apply(
        lambda r: f"{r['basic_grade']} ({int(r['basic_score'])})"
        if r['basic_score'] >= 0 else "? (无数据)",
        axis=1,
    )

    display_cols = ["ts_code", "name", "基本面", "资金意图",
                    "std_ratio_20", "amount_last",
                    "small_vol_days_10", "label"]
    print(show[display_cols].to_string(index=False))

    print(f"\n{'=' * 110}")
    print(f"分档统计")
    print(f"{'=' * 110}")
    print(f"\n资金意图档位分布：")
    mc = df["money_grade"].value_counts()
    for g in ["S", "A", "B", "C", "D"]:
        cnt = int(mc.get(g, 0))
        pct = cnt / len(df) * 100 if len(df) else 0
        print(f"  {g}: {cnt:5d}  ({pct:5.1f}%)")

    print(f"\n综合标签分布：")
    lc = df["label"].value_counts()
    for lb, cnt in lc.items():
        pct = cnt / len(df) * 100 if len(df) else 0
        print(f"  {lb}: {cnt:5d}  ({pct:5.1f}%)")

    out_path = CACHE_FEAT_DIR / "scan_result.pkl"
    with out_path.open("wb") as f:
        pickle.dump(df, f)
    logger.info("result saved | %s", out_path)


if __name__ == "__main__":
    main()