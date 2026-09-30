import logging
import sys
from pathlib import Path

"""清理数据缓存。
用法：
    python scripts/clear_cache.py 688508.SH     # 清一只
    python scripts/clear_cache.py --all          # 清全部
    python scripts/clear_cache.py --list         # 列出缓存
    pipenv run python scripts/clear_cache.py --list
"""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

from dashboard.analysis.data_cache import clear_cache, list_cache


def main():
    args = sys.argv[1:]

    if not args or args[0] == "--list":
        items = list_cache()
        if not items:
            print("缓存为空")
            return
        print(f"\n{'代码':<15} {'更新时间':<22} {'大小(KB)':>10}")
        print("-" * 50)
        for it in items:
            print(f"{it['ts_code']:<15} {it['updated_at'][:19]:<22} {it['size_kb']:>10}")
        print(f"\n共 {len(items)} 条")
        return

    if args[0] == "--all":
        n = clear_cache(None)
        print(f"已清理全部缓存 | 共 {n} 个文件")
        return

    for code in args:
        clear_cache(code)
        print(f"已清理 {code}")


if __name__ == "__main__":
    main()