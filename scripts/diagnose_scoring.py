"""诊断评分器的疑点。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pickle
import pandas as pd

from dashboard.analysis.scoring import (
    load_cached_data, _get_abstract_value,
)


def check_one(ts_code: str):
    print(f"\n{'='*80}")
    print(f"诊断 {ts_code}")
    print(f"{'='*80}")

    data = load_cached_data(ts_code)
    if not data:
        print("❌ 缓存不存在")
        return

    # ---- 1. indicators 表的列名 ----
    fin = data.get("financials", {}) or {}
    ind = fin.get("indicators")
    if ind is not None and not ind.empty:
        print(f"\n【indicators 列名】共 {len(ind.columns)} 列")
        for c in ind.columns:
            if "收益" in c or "负债" in c or "增长" in c:
                v0 = ind.iloc[0].get(c)
                print(f"  {c} = {v0}")
        print(f"\n【indicators 前 3 行完整数据】")
        show_cols = [c for c in ind.columns
                     if "日期" in c or "收益" in c or "负债" in c
                     or "增长" in c][:12]
        print(ind[show_cols].head(3).to_string())

    # ---- 2. abstract 表最新几期 ----
    ab = fin.get("abstract")
    if ab is not None and not ab.empty:
        date_cols = [c for c in ab.columns if c not in ("选项", "指标")]
        date_cols = sorted(date_cols, reverse=True)[:4]
        print(f"\n【abstract 最近 4 期】{date_cols}")

        for kw in ["归母净利润", "营业总收入", "经营现金流",
                   "扣非净利润"]:
            vals = _get_abstract_value(ab, [kw], periods=4)
            print(f"  {kw}: {vals}")

    # ---- 3. 手动核实 G2 / F6 ----
    print(f"\n【手动核实】")
    if ind is not None and not ind.empty:
        for col in ["净利润增长率(%)", "主营业务收入增长率(%)"]:
            if col in ind.columns:
                print(f"  indicators.iloc[0][{col}] = {ind.iloc[0].get(col)}")
                print(f"  indicators 日期 = {ind.iloc[0].get('日期', '?')}")

    if ab is not None and not ab.empty:
        cf = _get_abstract_value(ab, ["经营现金流", "经营活动产生的现金流量净额"], periods=1)
        np_ = _get_abstract_value(ab, ["归母净利润", "净利润"], periods=1)
        print(f"  经营现金流（最近 1 期）: {cf}")
        print(f"  归母净利润（最近 1 期）: {np_}")
        if cf and np_ and np_[0] != 0:
            print(f"  现金流/净利润 = {cf[0] / np_[0]:.3f}")


if __name__ == "__main__":
    codes = sys.argv[1:] or ["688508.SH", "002074.SZ", "300750.SZ"]
    for c in codes:
        check_one(c)