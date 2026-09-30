"""测试回溯验证。

用法：
    python scripts/test_reflection.py 688508.SH verify   # 只验证
    python scripts/test_reflection.py 688508.SH full     # 完整复盘
"""
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

from dashboard.analysis.reflection import (
    extract_predictions,
    verify_all,
    reflect_and_save,
)


async def main():
    ts_code = sys.argv[1] if len(sys.argv) > 1 else "688508.SH"
    mode = sys.argv[2] if len(sys.argv) > 2 else "full"

    print(f"\n{'='*60}\n回溯验证 | {ts_code} | mode={mode}\n{'='*60}\n")

    # ---- 1. 检查历史报告 ----
    preds = extract_predictions(ts_code)
    print(f"\n发现 {len(preds)} 份历史报告：")
    for p in preds:
        print(f"  - {p.report_path.name}")
        print(f"    日期={p.analysis_date.date()} | "
              f"推荐={p.recommendation} | "
              f"区间=[{p.target_low}%, {p.target_high}%] | "
              f"周期={p.target_period}日")

    if not preds:
        print("\n⚠️ 无历史报告，无法复盘。请先运行 test_full.py 生成报告。")
        return

    # ---- 2. 只验证 ----
    if mode == "verify":
        print(f"\n{'='*60}\n验证结果\n{'='*60}\n")
        verdicts = verify_all(ts_code)
        for v in verdicts:
            print(f"\n【{v.prediction.analysis_date.date()} → "
                  f"{v.end_date.date()}】")
            print(f"  推荐：{v.prediction.recommendation}")
            print(f"  起点：{v.start_price:.2f}（{v.start_date.date()}）")
            print(f"  终点：{v.end_price:.2f}（{v.end_date.date()}）")
            print(f"  实际：{v.actual_return:+.2f}%")
            print(f"  方向对：{v.direction_correct} | 区间中：{v.range_hit}")
            print(f"  判定：{v.verdict}")
        return

    # ---- 3. 完整流程 ----
    print(f"\n{'='*60}\n完整复盘（验证 + 反思）\n{'='*60}\n")
    result = await reflect_and_save(ts_code)

    print(f"\n验证条数：{len(result['verdicts'])}")
    print(f"反思文件：{result['reflection_path']}")
    print(f"候选规则：{result['candidates_count']} 条")

    if result["reflection_path"] and result["reflection_path"].exists():
        print(f"\n{'='*60}\n反思内容\n{'='*60}\n")
        print(result["reflection_path"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())