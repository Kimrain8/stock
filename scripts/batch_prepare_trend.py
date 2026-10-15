"""批量计算趋势派生列（独立脚本，不影响现有工程）。

用途：
    对 cache/data 下所有股票，按指定的 window 预先计算派生列，
    输出到 cache/trend_batch/w{window}/ 目录，方便后续快速加载。

用法：
    :: 用默认 window=0，4 进程
    python scripts\\batch_prepare_trend.py
    :: 指定 window=20，8 进程
    python scripts\\batch_prepare_trend.py --window 20 --workers 8
    :: 只处理前 100 只（测试用）
    python scripts\\batch_prepare_trend.py --window 5 --limit 100
    :: 强制重算（覆盖已有结果）
    python scripts\\batch_prepare_trend.py --window 0 --force
"""
import argparse
import logging
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.trend import _prepare_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("batch_trend")

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DATA_DIR = BASE_DIR / "cache" / "data"
OUTPUT_ROOT = BASE_DIR / "cache" / "trend_batch"


# ============================================================
# 参数
# ============================================================
def parse_args():
    p = argparse.ArgumentParser(description="批量计算趋势派生列")
    p.add_argument("--window", type=int, default=0,
                   help="向前汇总天数（默认 0）")
    p.add_argument("--workers", type=int, default=4,
                   help="并发进程数（默认 4）")
    p.add_argument("--limit", type=int, default=0,
                   help="只处理前 N 只（0=全部）")
    p.add_argument("--force", action="store_true",
                   help="强制重算（默认跳过已有文件）")
    return p.parse_args()


# ============================================================
# 单只股票处理（在子进程中执行）
# ============================================================
def process_one(task: tuple):
    """处理单只股票。

    task = (pkl_path_str, window, out_dir_str, force)
    返回：(code, status, info)
        status ∈ {"ok", "skip", "no_quotes", "load_fail", "prep_fail", "save_fail"}
    """
    pkl_path_str, window, out_dir_str, force = task
    pkl_path = Path(pkl_path_str)
    out_dir = Path(out_dir_str)
    code = pkl_path.stem
    out_path = out_dir / f"{code}.pkl"

    # 跳过已有结果
    if out_path.exists() and not force:
        return (code, "skip", 0)

    # 加载缓存
    try:
        with pkl_path.open("rb") as f:
            data = pickle.load(f)
    except Exception as e:
        return (code, "load_fail", str(e))

    quotes = data.get("quotes")
    if quotes is None or quotes.empty:
        return (code, "no_quotes", 0)

    # 计算派生列
    try:
        df = _prepare_data(quotes, window)
    except Exception as e:
        return (code, "prep_fail", str(e))

    # 保存
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as f:
            pickle.dump(df, f)
    except Exception as e:
        return (code, "save_fail", str(e))

    return (code, "ok", len(df))


# ============================================================
# 主流程
# ============================================================
def main():
    args = parse_args()

    out_dir = OUTPUT_ROOT / f"w{args.window}"

    logger.info("=" * 60)
    logger.info("batch_prepare_trend | window=%d | workers=%d | limit=%d | force=%s",
                args.window, args.workers, args.limit, args.force)
    logger.info("  输入：%s", CACHE_DATA_DIR)
    logger.info("  输出：%s", out_dir)
    logger.info("=" * 60)

    if not CACHE_DATA_DIR.exists():
        logger.error("缓存目录不存在：%s", CACHE_DATA_DIR)
        return

    # 收集任务
    pkl_files = sorted(CACHE_DATA_DIR.glob("*.pkl"))
    if not pkl_files:
        logger.error("缓存目录里没有 pkl 文件")
        return

    if args.limit > 0:
        pkl_files = pkl_files[:args.limit]

    logger.info("待处理股票数：%d", len(pkl_files))

    # 组装任务
    tasks = [
        (str(p), args.window, str(out_dir), args.force)
        for p in pkl_files
    ]

    t0 = time.time()
    ok, skip = 0, 0
    fail = {"load_fail": 0, "no_quotes": 0,
            "prep_fail": 0, "save_fail": 0}
    fail_samples = []   # 收集失败样本（最多 10 个）

    # 单进程模式（workers=1 时不启进程池，方便调试）
    if args.workers <= 1:
        for i, task in enumerate(tasks, 1):
            code, status, info = process_one(task)
            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail[status] = fail.get(status, 0) + 1
                if len(fail_samples) < 10:
                    fail_samples.append((code, status, info))

            if i % 100 == 0:
                logger.info("  progress %d / %d | ok=%d skip=%d fail=%d",
                            i, len(tasks), ok, skip, sum(fail.values()))
    else:
        # 多进程模式
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(process_one, task): task
                for task in tasks
            }
            for i, fut in enumerate(as_completed(futures), 1):
                try:
                    code, status, info = fut.result()
                except Exception as e:
                    code = futures[fut][0]
                    status, info = "load_fail", f"executor error: {e}"

                if status == "ok":
                    ok += 1
                elif status == "skip":
                    skip += 1
                else:
                    fail[status] = fail.get(status, 0) + 1
                    if len(fail_samples) < 10:
                        fail_samples.append((code, status, info))

                if i % 100 == 0:
                    logger.info("  progress %d / %d | ok=%d skip=%d fail=%d",
                                i, len(tasks), ok, skip, sum(fail.values()))

    elapsed = time.time() - t0

    # ---- 统计 ----
    print(f"\n{'=' * 60}")
    print(f"批量计算完成")
    print(f"{'=' * 60}")
    print(f"总股票数：{len(pkl_files)}")
    print(f"成功：    {ok}")
    print(f"跳过：    {skip}  （已存在，未重算）")
    print(f"失败：    {sum(fail.values())}")
    for k, v in fail.items():
        if v > 0:
            print(f"  {k}: {v}")
    print(f"耗时：    {elapsed:.1f}s （{elapsed / max(len(pkl_files), 1) * 1000:.1f}ms / 只）")
    print(f"输出目录：{out_dir}")

    if fail_samples:
        print(f"\n失败样本（前 10 条）：")
        for code, status, info in fail_samples:
            print(f"  {code} | {status} | {str(info)[:80]}")


if __name__ == "__main__":
    main()