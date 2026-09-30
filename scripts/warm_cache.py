import asyncio
import logging
import sys
from pathlib import Path

"""批量预热数据缓存。
用法：
    # 传代码
    python scripts/warm_cache.py 688508.SH 002074.SZ 300750.SZ

    # 从文件读（每行一个代码）
    python scripts/warm_cache.py --file watchlist.txt

    # 强制刷新（忽略已有缓存）
    python scripts/warm_cache.py --force 688508.SH
    pipenv run python scripts/warm_cache.py 688508.SH 002074.SZ 300750.SZ
    pipenv run python scripts/warm_cache.py --file watchlist.txt
watchlist.txt：
# 我的自选股
688508.SH
002074.SZ
300750.SZ
"""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

from dashboard.analysis.data_cache import get_data, refresh_cache


def _parse_args():
    args = sys.argv[1:]
    force = False
    codes = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--force":
            force = True
        elif a == "--file":
            i += 1
            if i < len(args):
                fpath = Path(args[i])
                if fpath.exists():
                    for line in fpath.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            codes.append(line)
        else:
            codes.append(a)
        i += 1
    return codes, force


def main():
    codes, force = _parse_args()
    if not codes:
        print("用法：python scripts/warm_cache.py [--force] [--file watchlist.txt] CODE1 CODE2 ...")
        sys.exit(1)

    print(f"\n{'='*60}\n批量预热缓存 | count={len(codes)} | force={force}\n{'='*60}\n")

    ok, fail = 0, 0
    for i, code in enumerate(codes, 1):
        print(f"\n[{i}/{len(codes)}] {code}")
        try:
            if force:
                refresh_cache(code)
            else:
                get_data(code, max_age_hours=24.0)
            ok += 1
        except Exception as e:
            logging.exception("warm failed | %s", code)
            fail += 1

    print(f"\n{'='*60}\n完成 | 成功={ok} | 失败={fail}\n{'='*60}")


if __name__ == "__main__":
    main()