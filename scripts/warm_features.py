"""离线构建全市场特征库。

用法：
    python scripts/warm_features.py --source hs300     # 沪深 300
    python scripts/warm_features.py --source zz500     # 中证 500
    python scripts/warm_features.py --codes 688508.SH 002074.SZ   # 指定
    python scripts/warm_features.py --file watchlist.txt
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

from dashboard.analysis.data_cache import get_data
from dashboard.analysis.feature_extractor import extract_batch
from dashboard.analysis.feature_store import save_features


def _load_universe(args) -> list[tuple[str, str]]:
    """返回 [(ts_code, name), ...]"""
    import akshare as ak

    out = []

    if args.source == "hs300":
        df = ak.index_stock_cons(symbol="000300")
        code_col = "品种代码" if "品种代码" in df.columns else "成分券代码"
        name_col = "品种名称" if "品种名称" in df.columns else "成分券名称"
        for _, r in df.iterrows():
            code = str(r[code_col])
            suffix = ".SH" if code.startswith(("60", "68")) else ".SZ"
            out.append((code + suffix, str(r[name_col])))
        return out

    if args.source == "zz500":
        df = ak.index_stock_cons(symbol="000905")
        code_col = "品种代码" if "品种代码" in df.columns else "成分券代码"
        name_col = "品种名称" if "品种名称" in df.columns else "成分券名称"
        for _, r in df.iterrows():
            code = str(r[code_col])
            suffix = ".SH" if code.startswith(("60", "68")) else ".SZ"
            out.append((code + suffix, str(r[name_col])))
        return out

    if args.codes:
        for c in args.codes:
            c = c.strip().upper()
            if c:
                if "." not in c:
                    if c.startswith(("60", "68")):
                        c += ".SH"
                    else:
                        c += ".SZ"
                out.append((c, ""))

    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "." not in line:
                if line.startswith(("60", "68")):
                    line += ".SH"
                else:
                    line += ".SZ"
            out.append((line.upper(), ""))

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="", choices=["", "hs300", "zz500"])
    parser.add_argument("--codes", nargs="*", default=[])
    parser.add_argument("--file", default="")
    args = parser.parse_args()

    universe = _load_universe(args)
    if not universe:
        print("⚠️ 未指定股票范围，用 --source hs300 / --codes / --file")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"构建特征库 | 股票数={len(universe)} | 时间={datetime.now()}")
    print(f"{'='*60}\n")

    rows = []
    t0 = time.time()
    ok, fail = 0, 0

    for i, (code, name) in enumerate(universe, 1):
        try:
            data = get_data(code, max_age_hours=24.0)
            feats = extract_batch(data, as_of_date=None)
            feats["ts_code"] = code
            feats["name"] = name or data.get("basic", {}).get("A股简称", "")
            rows.append(feats)
            ok += 1
        except Exception as e:
            logging.exception("extract failed | %s", code)
            fail += 1

        if i % 20 == 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(universe) - i)
            print(f"[{i}/{len(universe)}] ok={ok} fail={fail} "
                  f"elapsed={elapsed:.0f}s eta={eta:.0f}s")

    if not rows:
        print("❌ 无特征产出")
        sys.exit(1)

    df = pd.DataFrame(rows)
    path = save_features(df)
    print(f"\n✅ 特征库保存：{path}")
    print(f"   行数：{len(df)}")
    print(f"   耗时：{time.time() - t0:.0f}s")
    print(f"\n现在可以去 /analysis → 🎯 相似股扫描 使用。")


if __name__ == "__main__":
    main()