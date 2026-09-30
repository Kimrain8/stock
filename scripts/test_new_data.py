"""测试新增的三个数据源。"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

from dashboard.analysis.data_fetcher import (
    fetch_share_release, fetch_holder_num, fetch_pledge,
)
from dashboard.analysis.preprocessor import (
    _build_release_text, _build_holder_text, _build_pledge_text,
)

CODE = sys.argv[1] if len(sys.argv) > 1 else "688508.SH"

print(f"\n{'='*60}\n测试新数据源 | {CODE}\n{'='*60}\n")

# ---- 解禁 ----
print("\n【解禁】")
try:
    df = fetch_share_release(CODE)
    print(f"  行数: {len(df)}")
    print(f"  列名: {list(df.columns)}")
    print(f"\n  ⟹ 预处理后文本:\n{_build_release_text(df)}")
except Exception as e:
    print(f"  ❌ {e}")

# ---- 股东户数 ----
print("\n【股东户数】")
try:
    df = fetch_holder_num(CODE)
    print(f"  行数: {len(df)}")
    print(f"\n  ⟹ 预处理后文本:\n{_build_holder_text(df)}")
except Exception as e:
    print(f"  ❌ {e}")

# ---- 质押 ----
print("\n【质押】（首次拉取可能 10-20 秒）")
try:
    df = fetch_pledge(CODE)
    print(f"  行数: {len(df)}")
    print(f"\n  ⟹ 预处理后文本:\n{_build_pledge_text(df)}")
except Exception as e:
    print(f"  ❌ {e}")