# -*- coding: utf-8 -*-
"""
抓取中国货币网(www.chinamoney.com.cn) 人民币兑美元每日汇率中间价，保存为 Excel。

数据源：中国外汇交易中心（人民币汇率中间价历史数据）
接口：https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew
"""
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

API_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
PAGE_SIZE = 10  # 接口限制，超过10会返回403

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/",
}


def fetch_ccpr(start_date: str, end_date: str, currency: str) -> pd.DataFrame:
    """抓取指定区间的人民币汇率中间价，返回(日期, 汇率) DataFrame。

    单次接口查询区间限制为一年内，故按月分段抓取。
    """
    all_rows = []
    month_ranges = []
    d = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    while d <= end:
        month_end = (d + pd.offsets.MonthEnd(0))
        month_end = min(month_end, end)
        month_ranges.append((d.strftime("%Y-%m-%d"), month_end.strftime("%Y-%m-%d")))
        d = month_end + pd.Timedelta(days=1)

    for s, e in month_ranges:
        page_num = 1
        while True:
            params = {
                "startDate": s,
                "endDate": e,
                "currency": currency,
                "pageNum": str(page_num),
                "pageSize": str(PAGE_SIZE),
            }
            resp = requests.post(API_URL, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            if data.get("flagMessage"):
                raise RuntimeError(f"{s}~{e}: {data.get('flagMessage')}")
            records = resp.json().get("records") or []
            all_rows.extend(records)

            page_total = data.get("pageTotal") or 1
            if page_num >= page_total or not records:
                break
            page_num += 1
        print(f"  {s} ~ {e} 完成，累计 {len(all_rows)} 条")

    df = pd.DataFrame(all_rows)
    if df.empty:
        return df
    # values 只有一列(USD/CNY)
    df["date"] = pd.to_datetime(df["date"])
    df["USD/CNY"] = pd.to_numeric(df["values"].str[0], errors="coerce")
    df = df[["date", "USD/CNY"]].rename(columns={"USD/CNY": "美元/人民币汇率"})
    return df.sort_values("date")


def main():
    start_date = "2024-07-01"
    end_date = datetime.now().strftime("%Y-%m-%d")
    print(f"开始抓取人民币兑美元每日汇率中间价：{start_date} 至 {end_date} ...")

    df = fetch_ccpr(start_date, end_date, "USD/CNY")
    if df.empty:
        print("未获取到数据")
        return

    print(f"共获取 {len(df)} 条日度记录")

    out_dir = Path("./outputs")
    out_dir.mkdir(exist_ok=True)
    filename = f"人民币兑美元汇率中间价_{start_date}_{end_date}.xlsx"
    file_path = out_dir / filename
    df.to_excel(file_path, index=False)

    print(f"\n完成！已保存 {len(df)} 条记录到：{file_path.resolve()}")
    print("\n最近10条：")
    print(df.tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
