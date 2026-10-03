"""生成沪深 300 成分股的 watchlist。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak


def main():
    print("获取沪深 300 成分股 ...")
    df = ak.index_stock_cons(symbol="000300")

    code_col = "品种代码" if "品种代码" in df.columns else "成分券代码"
    name_col = "品种名称" if "品种名称" in df.columns else "成分券名称"

    codes = []
    for _, r in df.iterrows():
        code = str(r[code_col]).zfill(6)
        if code.startswith(("60", "68")):
            suffix = ".SH"
        elif code.startswith(("00", "30")):
            suffix = ".SZ"
        elif code.startswith(("8", "4")):
            suffix = ".BJ"
        else:
            suffix = ".SH"
        codes.append(code + suffix)

    out_path = Path(__file__).resolve().parent.parent / "watchlist_hs300.txt"
    out_path.write_text("\n".join(codes), encoding="utf-8")
    print(f"✅ 已写入 {len(codes)} 只股票到 {out_path.name}")


if __name__ == "__main__":
    main()