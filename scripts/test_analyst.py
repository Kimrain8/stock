"""测试分析师（支持只跑基本面或全部）。"""
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

from dashboard.analysis import agents


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "688508.SH"
    mode = os.getenv("TEST_MODE", "all")   # 默认跑全部

    if mode == "single":
        asyncio.run(agents._test(code))
    else:
        asyncio.run(agents._test_all(code))