# -*- coding: utf-8 -*-
import requests
import json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/",
    "Accept": "*/*",
}
url = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
params = {"startDate": "2024-07-01", "endDate": "2026-08-14", "currency": "USD/CNY", "pageNum": "1", "pageSize": "10"}
r = requests.post(url, params=params, headers=headers, timeout=25)
print("status:", r.status_code)
print("content-type:", r.headers.get('content-type'))
print(r.text[:1500])