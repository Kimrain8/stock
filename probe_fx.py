# -*- coding: utf-8 -*-
import requests
import json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.chinamoney.com.cn/chinese/forsddshis/",
}
base = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-fx/SddsExchRateSwpHis"

for dt in ["1", "3", "6", "9", "12"]:
    try:
        r = requests.get(base, params={"lang": "cn", "dataType": dt}, headers=headers, timeout=20)
        d = r.json()
        data = d.get("data", {})
        records = d.get("records", [])
        print("=" * 60)
        print("dataType:", dt, "| status:", r.status_code)
        print("startDate:", data.get("startDate"), "endDate:", data.get("endDate"))
        print("head:", data.get("head"))
        print("dataTypekey:", data.get("dataTypekey"))
        if records:
            print("record[0]:", json.dumps(records[0], ensure_ascii=False)[:600])
    except Exception as e:
        print("dataType:", dt, "ERR", repr(e))