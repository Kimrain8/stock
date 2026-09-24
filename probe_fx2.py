# -*- coding: utf-8 -*-
import requests
import json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/",
}
url = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"

# 先不带参数获取元信息
for params in [
    {"pageNum": "1", "pageSize": "10"},
    {"startDate": "2024-07-01", "endDate": "2024-07-05", "currency": "USD/CNY", "pageNum": "1", "pageSize": "10"},
    {"startDate": "2024-07-01", "endDate": "2024-07-05", "currency": "USD", "pageNum": "1", "pageSize": "10"},
]:
    try:
        r = requests.post(url, params=params, headers=headers, timeout=20)
        d = r.json()
        data = d.get("data", {})
        print("=" * 60)
        print("params:", params)
        print("status bits:", r.status_code, "| startDate:", data.get("startDate"), "| endDate:", data.get("endDate"), "| total:", data.get("total"), "| pageTotal:", data.get("pageTotal"))
        print("head:", data.get("head"))
        print("currency:", data.get("currency"))
        recs = d.get("records", [])
        if recs:
            print("record[0]:", json.dumps(recs[0], ensure_ascii=False)[:400])
    except Exception as e:
        print("ERR", params, repr(e))