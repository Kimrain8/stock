"""批量构建全市场基本面评分库。

流程：
    1. 从 cache/data 目录列出所有已缓存的股票代码
    2. 对每只股票算 basic_score
    3. 落盘为 cache/features/basic_score_library.pkl

用法：
    :: 默认从 cache 目录拿股票（推荐）
    python scripts\\build_basic_library.py

    :: 从数据库拿全部股票（会跳过无缓存的）
    python scripts\\build_basic_library.py --from-db

    :: 只算前 N 只（测试）
    python scripts\\build_basic_library.py --limit 10
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import pickle
import time

import pandas as pd

from utils.dbManager import MySQLHelper
from dashboard.config import settings
from dashboard.analysis.scoring import evaluate_one

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("build_basic_library")

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DATA_DIR = BASE_DIR / "cache" / "data"
CACHE_FEAT_DIR = BASE_DIR / "cache" / "features"
CACHE_FEAT_DIR.mkdir(parents=True, exist_ok=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--from-db", action="store_true",
                   help="从 ts_stock_basic 拿全部股票（默认从 cache 目录）")
    p.add_argument("--limit", type=int, default=0,
                   help="只算前 N 只（0=全部）")
    p.add_argument("--output", default="basic_score_library.pkl")
    return p.parse_args()


# ============================================================
# 股票列表来源
# ============================================================
def get_all_stocks_from_cache() -> list:
    """从 cache/data 目录列出所有已缓存的股票代码。"""
    if not CACHE_DATA_DIR.exists():
        return []
    return sorted([p.stem for p in CACHE_DATA_DIR.glob("*.pkl")])


def get_all_stocks_from_db() -> list:
    """从 ts_stock_basic 拿全市场股票代码。"""
    db = MySQLHelper(db=settings.DB_NAME)
    try:
        df = db.qry2df(sql="SELECT ts_code FROM ts_stock_basic")
        if df is None or df.empty:
            return []
        return sorted(df["ts_code"].astype(str).tolist())
    except Exception as e:
        logger.error("get all stocks failed | %s", e)
        return []


# ============================================================
# 主流程
# ============================================================
def main():
    args = parse_args()

    logger.info("=" * 60)
    source = "数据库" if args.from_db else "缓存目录"
    logger.info("build_basic_library | source=%s | limit=%d",
                source, args.limit)
    logger.info("=" * 60)

    t0 = time.time()

    # ---- 1. 股票列表 ----
    if args.from_db:
        codes = get_all_stocks_from_db()
        logger.info("from DB: %d stocks", len(codes))
    else:
        codes = get_all_stocks_from_cache()
        logger.info("from cache: %d stocks", len(codes))

    if not codes:
        logger.error("no stocks found | 请先预热数据："
                     "python scripts\\warm_cache.py --file watchlist.txt")
        return

    if args.limit > 0:
        codes = codes[:args.limit]
        logger.info("limited to %d", len(codes))

    # ---- 2. 逐只评分 ----
    rows = []
    n_skip = 0
    n_hard_fail = 0

    for i, code in enumerate(codes, 1):
        if i % 100 == 0 or i == len(codes):
            logger.info("  progress %d / %d (skip=%d, hard_fail=%d)",
                        i, len(codes), n_skip, n_hard_fail)

        r = evaluate_one(code)
        if r is None:
            n_skip += 1
            continue

        if not r["hard_pass"]:
            n_hard_fail += 1

        rows.append({
            "ts_code": r["ts_code"],
            "name": r["name"],
            "basic_score": r["basic_score"],
            "basic_grade": r["basic_grade"],
            "hard_pass": r["hard_pass"],
            "hard_fail_reason": r["hard_fail_reason"],
        })

    if not rows:
        logger.error("no results | 所有股票都被跳过")
        return

    # ---- 3. 保存 ----
    df = pd.DataFrame(rows)
    df = df.sort_values("basic_score", ascending=False).reset_index(drop=True)

    out_path = CACHE_FEAT_DIR / args.output
    with out_path.open("wb") as f:
        pickle.dump(df, f)

    logger.info("saved | %s | shape=%s | %.1fs",
                out_path, df.shape, time.time() - t0)

    # ---- 4. 统计 ----
    print(f"\n{'=' * 60}")
    print(f"基本面评分库统计")
    print(f"{'=' * 60}")
    print(f"输入股票数：{len(codes)}")
    print(f"成功评分：{len(df)}")
    print(f"跳过（无缓存）：{n_skip}")
    print(f"硬门槛不通过：{n_hard_fail}")

    print(f"\n=== 分档分布 ===")
    grade_counts = df["basic_grade"].value_counts()
    for g in ["S", "A", "B", "C", "D"]:
        cnt = int(grade_counts.get(g, 0))
        pct = cnt / len(df) * 100 if len(df) else 0
        print(f"  {g}: {cnt:5d}  ({pct:5.1f}%)")

    print(f"\n=== TOP 10 高分股 ===")
    show_cols = ["ts_code", "name", "basic_score", "basic_grade"]
    print(df[show_cols].head(10).to_string())

    if (~df["hard_pass"]).any():
        print(f"\n=== 硬门槛不通过 TOP 5 原因 ===")
        fails = df[~df["hard_pass"]]["hard_fail_reason"].value_counts().head(5)
        for reason, cnt in fails.items():
            print(f"  {cnt:4d}  {reason[:80]}")


if __name__ == "__main__":
    main()