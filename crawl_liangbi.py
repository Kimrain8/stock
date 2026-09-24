# -*- coding: utf-8 -*-
"""
抓取沪深京全部 A 股的量比和换手率，保存为 Excel 文件。

方案：
1. 用 akshare(新浪源) 获取全部 A 股代码列表
2. 用腾讯行情接口批量查询每只股票的量比和换手率
   (换手率=字段38, 量比=字段49, GBK 编码)
"""
import time
from datetime import datetime
from pathlib import Path
import pandas as pd
import requests

# 腾讯行情接口
TENCENT_URL = "https://qt.gtimg.cn/q="
BATCH_SIZE = 60  # 每批查询的股票数
BATCH_INTERVAL = 0.2  # 批次间隔(秒)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def get_stock_list() -> pd.DataFrame:
    """用 akshare 获取全部 A 股列表(新浪源)。"""
    import akshare as ak
    print("第一步：获取全部 A 股列表 ...")
    df = ak.stock_zh_a_spot()
    # 代码列形如 sh600519 / sz000001 / bj920000
    df = df.rename(columns={"代码": "symbol", "名称": "name"})
    print(f"  列表获取完成，共 {len(df)} 只")
    return df[["symbol", "name"]]


def fetch_tencent_batch(symbols: list) -> dict:
    """批量查询腾讯行情，返回 {symbol: (换手率, 量比)}。"""
    url = TENCENT_URL + ",".join(symbols)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        text = resp.content.decode("gbk", errors="replace")
    except Exception as e:
        print(f"  批量请求失败({len(symbols)}只): {e}")
        return {}
    result = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) <= 49:
            continue
        code = parts[2].strip()
        turnover = parts[38].strip()  # 换手率%
        liangbi = parts[49].strip()   # 量比
        result[code] = (turnover, liangbi)
    return result


def main():
    stocks = get_stock_list()

    print("第二步：通过腾讯接口逐批查询量比和换手率 ...")
    liangbi_list = []
    for i in range(0, len(stocks), BATCH_SIZE):
        batch = stocks.iloc[i : i + BATCH_SIZE]
        symbol_to_code = {}
        query_symbols = []
        for _, row in batch.iterrows():
            sym = row["symbol"]  # 形如 sh600519
            code = sym[2:]
            symbol_to_code[sym] = code
            query_symbols.append(sym)
        data = fetch_tencent_batch(query_symbols)
        for sym in query_symbols:
            code = symbol_to_code[sym]
            if code in data:
                liangbi_list.append(
                    {"symbol": sym, "换手率": data[code][0], "量比": data[code][1]}
                )
        print(f"  已查询 {min(i + BATCH_SIZE, len(stocks))}/{len(stocks)}")
        time.sleep(BATCH_INTERVAL)

    print(f"查询完成，共 {len(liangbi_list)} 条有效数据，开始整理 ...")

    result = pd.DataFrame(liangbi_list)
    merged = stocks.merge(result, on="symbol", how="left")
    merged = merged[["symbol", "name", "量比", "换手率"]]
    merged = merged.rename(columns={"symbol": "代码", "name": "名称"})

    # 转换数值类型
    merged["量比"] = pd.to_numeric(merged["量比"], errors="coerce")
    merged["换手率"] = pd.to_numeric(merged["换手率"], errors="coerce")

    # 排序：量比从高到低
    merged = merged.sort_values("量比", ascending=False, na_position="last").reset_index(drop=True)

    # 保存 Excel
    out_dir = Path("./outputs")
    out_dir.mkdir(exist_ok=True)
    filename = f"A股量比换手率_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    file_path = out_dir / filename
    merged.to_excel(file_path, index=False)

    print(f"\n完成！已保存 {len(merged)} 条记录到：{file_path.resolve()}")
    print(merged.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
