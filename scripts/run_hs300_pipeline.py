"""一键执行沪深 300 全流程。

流程：
    1. 生成 watchlist_hs300.txt
    2. 批量预热缓存
    3. 重建基本面库
    4. 扫描当前市场
"""
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def run(cmd: list, desc: str):
    print(f"\n{'='*60}")
    print(f"▶ {desc}")
    print(f"{'='*60}")
    result = subprocess.run(
        [sys.executable] + cmd,
        cwd=str(BASE_DIR),
    )
    if result.returncode != 0:
        print(f"❌ 失败：{desc}")
        sys.exit(1)


def main():
    # 1. 生成 watchlist
    run(["scripts/build_hs300_watchlist.py"], "生成沪深 300 watchlist")

    # 2. 批量预热缓存
    run(["scripts/warm_cache.py", "--file", "watchlist_hs300.txt"],
        "批量预热缓存（约 20~30 分钟）")

    # 3. 重建基本面库
    run(["scripts/build_basic_library.py"], "重建基本面评分库")

    # 4. 扫描当前市场
    run(["scripts/scan_current_market.py", "--top", "30"],
        "扫描当前市场 TOP 30")

    print(f"\n{'='*60}")
    print(f"✅ 沪深 300 全流程完成")
    print(f"   结果文件：cache/features/scan_result.pkl")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()