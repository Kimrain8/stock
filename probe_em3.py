# -*- coding: utf-8 -*-
import requests
import time

url = "https://82.push2.eastmoney.com/api/qt/clist/get"
params = {
    "pn": "1", "pz": "100", "po": "1", "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2", "invt": "2", "fid": "f12",
    "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
    "fields": "f2,f3,f8,f10,f12,f14",
}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}

for i in range(5):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        d = r.json()
        print(f"{i}: {r.status_code} total={d.get('data',{}).get('total')}")
    except Exception as e:
        print(f"{i}: ERR {type(e).__name__}")
    time.sleep(2)
