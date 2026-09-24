# -*- coding: utf-8 -*-
"""
不再使用了，改用调度器进行程序调度。如果要使用，此文件放在根目录下，而且.run_state.json也在根目录下
每日定时任务执行器
------------------
特性：
  1. 按天维度的断点续跑：当天已成功的步骤跳过，跨天自动重置
  2. 状态文件损坏立即终止（不静默覆盖），文件不存在则自动初始化
  3. 每个步骤独立记录，粒度精确到函数
  4. 支持 --reset 强制当天重跑
"""

import asyncio
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime

from crawl_stock import Myts,Myqq
from stock_run_states import StockState,run_step,run_step_async,StateCorruptedError
from crawl_qq_fq import fetch_qq_kline_db
from scheduler import sch_main

# ===========================================================================
#  一、状态文件路径 & 异常
# ===========================================================================

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".run_state.json")

# ===========================================================================
#  四、业务类（示例骨架，按你的实际实现替换）
# ===========================================================================

# ===========================================================================
#  五、主流程
# ===========================================================================

async def main(reset: bool = False):
    state = StockState(path=STATE_FILE)
    if reset:
        state.reset()

    runCnt = state.new_run()  # 当天第几次运行，由状态文件自增
    print(f"\n===== {state.day} 第 {runCnt} 次运行 =====\n")

    # ---------- 1. 基础数据 ----------
    ts = Myts()
    run_step(state, "Myts.get_trade_calendar", ts.get_trade_calendar)
    run_step(state, "Myts.get_stock_list",     ts.get_stock_list)
    run_step(state, "Myts.get_daily_deal",     ts.get_daily_deal)

    # ---------- 2. 板块相关 ----------
    qq = Myqq()

    await run_step_async(state, "Myqq.fetch_getBoardRankList_db", qq.fetch_getBoardRankList_db)
    await run_step_async(state, "Myqq.fetch_getRank_db", qq.fetch_getRank_db)
    await run_step_async(state, "Myqq.fetch_batch_plate_db", qq.fetch_batch_plate_db)

    # ---------- 3. 行情 ----------
    run_step(state, "Myqq.fetch_quotes_db", qq.fetch_quotes_db, codes=None)

    print("\n===== 全部完成 =====")


# ===========================================================================
#  六、入口
# ===========================================================================

if __name__ == "__main__":
    reset = "--reset" in sys.argv
    if "fetch_qq_kline_db" in sys.argv:
        state = StockState(path=STATE_FILE)
        run_step(state, "fetch_qq_kline_db", fetch_qq_kline_db)
    elif ".sch_config.json" in sys.argv:
        sch_main() #python scheduler.py continue <配置文件路径>  真实运行：python crawl_stock_run.py .sch_config.json
    else:
        try:
            asyncio.run(main(reset=reset))
        except StateCorruptedError as e:
            # 状态文件损坏 → 立即终止，不自动重置
            print(f"\n[致命] {e}", file=sys.stderr)
            sys.exit(2)
        except KeyboardInterrupt:
            print("\n[中断] 用户 Ctrl+C，状态已保存，下次运行续跑", file=sys.stderr)
            sys.exit(130)
        except Exception as e:
            print(f"\n[失败] {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)
    # cd /path/to/project
    # # 正常执行（当天第一次全跑，之后续跑 / 幂等）
    # pipenv run python crawl_stock_run.py
    # python crawl_stock_run.py .sch_config.json
    # # 当天强制重跑
    # pipenv run python run_daily.py --reset
    # # 查看当天状态
    # cat .run_state.json