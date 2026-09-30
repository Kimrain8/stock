"""测试完整分析 pipeline。"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

from dashboard.analysis.agents import analyze_full


async def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "688508.SH"
    use_cache = "--no-cache" not in sys.argv
    scenario = ""
    for a in sys.argv:
        if a.startswith("--scenario="):
            scenario = a.split("=", 1)[1]

    print(f"\n{'='*60}")
    print(f"完整分析 | {code} | use_cache={use_cache} | scenario={scenario or 'auto'}")
    print(f"{'='*60}\n")

    result = await analyze_full(
        code, save=True, use_cache=use_cache, scenario_hint=scenario,
    )


if __name__ == "__main__":
    asyncio.run(main())