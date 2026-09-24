# -*- coding: utf-8 -*-
import requests
import time

hosts = [
    "https://82.push2.eastmoney.com",
    "https://push2.eastmoney.com",
    "http://82.push2.eastmoney.com",
    "http://push2.eastmoney.com",
    "https://push2his.eastmoney.com",
    "https://23.push2.eastmoney.com",
]
params = {
    "pn": "1", "pz": "5", "po": "1", "np": "1",
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

for host in hosts:
    try:
        r = requests.get(host + "/api/qt/clist/get", params=params, headers=headers, timeout=15)
        d = r.json()
        total = d.get('data', {}).get('total')
        print(f"{host} -> {r.status_code} total={total}")
    except Exception as e:
        print(f"{host} -> ERR {type(e).__name__}: {e}")
    time.sleep(1)
