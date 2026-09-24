# -*- coding: utf-8 -*-
import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/",
    "Accept": "*/*",
}
url = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"

tests = [
    ("2024-07-01", "2025-06-30"),
    ("2025-07-01", "2025-07-05"),
    ("2024-01-01", "2024-12-31"),
]
for s, e in tests:
    params = {"startDate": s, "endDate": e, "currency": "USD/CNY", "pageNum": "1", "pageSize": "10"}
    r = requests.post(url, params=params, headers=headers, timeout=25)
    d = r.json()
    data = d.get("data", {}) or {}
    recs = d.get("records", [])
    flag = data.get("flagMessage") or ""
    print(f"{s} ~ {e} -> status={r.status_code} records={len(recs)} flag={flag!r}")
    if recs:
        print("   first:", recs[0])