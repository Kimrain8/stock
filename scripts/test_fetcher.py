"""测试数据抓取层。"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

from dashboard.analysis.data_fetcher import fetch_all


def _short(s, n=200):
    s = str(s)
    return s if len(s) <= n else s[:n] + "..."


def main():
    ts_code = sys.argv[1] if len(sys.argv) > 1 else "688508.SH"
    print(f"\n测试股票：{ts_code}\n")

    data = fetch_all(ts_code)

    # 基本信息
    print("\n" + "=" * 60)
    print("【基本信息】")
    print("=" * 60)
    b = data["basic"]
    if b:
        for k in ["公司名称", "A股简称", "A股代码", "所属市场", "所属行业",
                  "法人代表", "上市日期", "主营业务"]:
            if k in b:
                print(f"  {k}: {_short(b[k], 100)}")
    else:
        print("  ✗ 未取到")

    # 财务摘要
    print("\n" + "=" * 60)
    print("【财务摘要】")
    print("=" * 60)
    fin = data["financials"]
    if "abstract" in fin:
        df = fin["abstract"]
        print(f"  形状: {df.shape}")
        # 取最近一期营收/净利润
        if "指标" in df.columns:
            cols = [c for c in df.columns if c not in ("选项", "指标")]
            latest = cols[0] if cols else None
            if latest:
                for k in ["营业总收入", "归母净利润"]:
                    row = df[df["指标"] == k]
                    if not row.empty:
                        print(f"  {k} ({latest}): {row.iloc[0][latest]:,.0f}")
    else:
        print("  ✗ 未取到")

    # 公告
    print("\n" + "=" * 60)
    print("【公告】")
    print("=" * 60)
    ann = data["announcements"]
    if not ann.empty:
        print(f"  行数: {len(ann)}")
        print(f"  列: {list(ann.columns)}")
        print(f"  前 5 条:")
        print(ann.head(5).to_string())
    else:
        print("  ✗ 未取到")

    # 行情
    print("\n" + "=" * 60)
    print("【行情】")
    print("=" * 60)
    q = data["quotes"]
    if not q.empty:
        print(f"  行数: {len(q)}")
        print(f"  列: {list(q.columns)}")
        print(f"  最近 3 天:")
        print(q.tail(3).to_string())
    else:
        print("  ✗ 未取到")

    # 分红
    print("\n" + "=" * 60)
    print("【分红】")
    print("=" * 60)
    d = data["dividends"]
    if not d.empty:
        print(f"  行数: {len(d)}")
        print(d.tail(3).to_string())
    else:
        print("  ✗ 未取到")

    # 实时快照
    print("\n" + "=" * 60)
    print("【实时快照】")
    print("=" * 60)
    if data["spot"]:
        for k in ["代码", "名称", "最新价", "涨跌幅", "成交量", "成交额"]:
            if k in data["spot"]:
                print(f"  {k}: {data['spot'][k]}")
    else:
        print("  ✗ 未取到")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()