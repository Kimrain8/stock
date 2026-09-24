# -*- coding: utf-8 -*-
import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/",
    "Accept": "*/*",
}
url = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"

tests = [
    {"startDate": "2024-07-01", "endDate": "2026-08-14", "currency": "USD/CNY", "pageNum": "1", "pageSize": "10"},
    {"startDate": "2024-07-01", "endDate": "2026-08-14", "currency": "USD/CNY", "pageNum": "1", "pageSize": "100"},
    {"startDate": "2024-07-01", "endDate": "2026-08-14", "currency": "USD/CNY", "pageNum": "1", "pageSize": "500"},
    {"startDate": "2024-07-01", "endDate": "2026-08-14", "currency": "USD/CNY", "pageNum": "1", "pageSize": "1000"},
    {"startDate": "2024-07-01", "endDate": "2024-07-05", "currency": "USD/CNY", "pageNum": "1", "pageSize": "1000"},
]
for params in tests:
    try:
        r = requests.post(url, params=params, headers=headers, timeout=25)
        d = r.json() if r.headers.get('content-type', '').find('json') >= 0 else {}
        data = d.get("data", {}) or {}
        print(f"{params['pageSize']:>4} / {params['endDate']} -> {r.status_code} total={data.get('total')} pageTotal={data.get('pageTotal')}")
    except Exception as e:
        print(f"{params['pageSize']:>4} / {params['endDate']} -> ERR {type(e).__name__} {e}")