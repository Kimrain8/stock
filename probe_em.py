# -*- coding: utf-8 -*-
import requests

url = "https://82.push2.eastmoney.com/api/qt/clist/get"
params = {
    "pn": "1",
    "pz": "100",
    "po": "1",
    "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
    "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
    "f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
    "Accept": "*/*",
}

try:
    r = requests.get(url, params=params, headers=headers, timeout=20)
    print('status', r.status_code)
    d = r.json()
    data = d.get('data', {})
    print('total', data.get('total'))
    rows = data.get('diff')
    if rows:
        print(rows[0])
except Exception as e:
    print('ERR', repr(e))
